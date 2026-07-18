from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any

from openframetap.tools.latency_pattern import (
    NormalizedRect,
    gray_to_binary,
    pattern_layout,
)


@dataclass(frozen=True, slots=True)
class BankDecode:
    frame_id: int | None
    gray_code: int | None
    bit_values: tuple[int, ...]
    luminance: tuple[float, ...]
    threshold: float | None
    black_level: float
    white_level: float
    contrast: float
    confidence: float
    valid: bool
    reason: str | None

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class PatternDecode:
    frame_id: int | None
    gray_code: int | None
    confidence: float
    valid: bool
    mixed_refresh: bool
    adjacent_gray_transition: bool
    reason: str | None
    top: BankDecode
    bottom: BankDecode

    def to_dict(self) -> dict:
        return asdict(self)


def _mean_rect(gray: Any, rect: NormalizedRect) -> float:
    height, width = gray.shape[:2]
    x0, y0, x1, y1 = rect.pixels(width, height)
    margin_x = max(1, (x1 - x0) // 6)
    margin_y = max(1, (y1 - y0) // 6)
    sample = gray[y0 + margin_y : y1 - margin_y, x0 + margin_x : x1 - margin_x]
    if sample.size == 0:
        raise ValueError("pattern cell falls outside rectified ROI")
    return float(sample.mean())


def decode_bank(
    gray: Any,
    rects: tuple[NormalizedRect, ...],
    black_reference: NormalizedRect,
    white_reference: NormalizedRect,
    *,
    invert: bool = False,
    minimum_contrast: float = 35.0,
    minimum_confidence: float = 0.18,
) -> BankDecode:
    black_sample = _mean_rect(gray, black_reference)
    white_sample = _mean_rect(gray, white_reference)
    dark, bright = sorted((black_sample, white_sample))
    contrast = bright - dark
    values = tuple(_mean_rect(gray, rect) for rect in rects)
    if contrast < minimum_contrast:
        return BankDecode(
            None,
            None,
            (),
            values,
            None,
            dark,
            bright,
            contrast,
            0.0,
            False,
            "low_contrast",
        )
    threshold = (dark + bright) / 2.0
    bits = tuple(int(value >= threshold) ^ int(invert) for value in values)
    confidence = min(
        1.0,
        min(abs(value - threshold) / max(contrast / 2.0, 1e-9) for value in values),
    )
    gray_code = 0
    for bit in bits:
        gray_code = (gray_code << 1) | bit
    frame_id = gray_to_binary(gray_code)
    valid = confidence >= minimum_confidence
    return BankDecode(
        frame_id,
        gray_code,
        bits,
        values,
        threshold,
        dark,
        bright,
        contrast,
        confidence,
        valid,
        None if valid else "ambiguous_bit_luminance",
    )


def decode_pattern(
    image: Any,
    *,
    bits: int = 16,
    invert: bool = False,
    minimum_contrast: float = 35.0,
    minimum_confidence: float = 0.18,
) -> PatternDecode:
    import cv2

    if image is None or getattr(image, "size", 0) == 0:
        raise ValueError("cannot decode an empty image")
    gray = image if len(image.shape) == 2 else cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    layout = pattern_layout(bits)
    top = decode_bank(
        gray,
        layout.top_bits,
        layout.top_black_reference,
        layout.top_white_reference,
        invert=invert,
        minimum_contrast=minimum_contrast,
        minimum_confidence=minimum_confidence,
    )
    bottom = decode_bank(
        gray,
        layout.bottom_bits,
        layout.bottom_black_reference,
        layout.bottom_white_reference,
        invert=invert,
        minimum_contrast=minimum_contrast,
        minimum_confidence=minimum_confidence,
    )
    if not top.valid or not bottom.valid:
        reason = top.reason or bottom.reason
        return PatternDecode(None, None, 0.0, False, False, False, reason, top, bottom)
    mixed = top.frame_id != bottom.frame_id
    adjacent = bool(
        mixed
        and top.gray_code is not None
        and bottom.gray_code is not None
        and (top.gray_code ^ bottom.gray_code).bit_count() == 1
    )
    confidence = min(top.confidence, bottom.confidence)
    return PatternDecode(
        None if mixed else top.frame_id,
        None if mixed else top.gray_code,
        confidence,
        not mixed,
        mixed,
        adjacent,
        "mixed_refresh" if mixed else None,
        top,
        bottom,
    )


def rectify_roi(
    frame: Any,
    roi,
    transform: tuple[tuple[float, float], ...] | None,
    *,
    width: int = 1600,
    height: int = 900,
):
    import cv2
    import numpy as np

    frame_height, frame_width = frame.shape[:2]
    if roi.x + roi.width > frame_width or roi.y + roi.height > frame_height:
        raise ValueError("ROI exceeds video frame bounds")
    if transform:
        source = np.array(transform, dtype=np.float32)
    else:
        source = np.array(
            [
                [roi.x, roi.y],
                [roi.x + roi.width, roi.y],
                [roi.x + roi.width, roi.y + roi.height],
                [roi.x, roi.y + roi.height],
            ],
            dtype=np.float32,
        )
    destination = np.array(
        [[0, 0], [width - 1, 0], [width - 1, height - 1], [0, height - 1]],
        dtype=np.float32,
    )
    matrix = cv2.getPerspectiveTransform(source, destination)
    return cv2.warpPerspective(frame, matrix, (width, height), flags=cv2.INTER_LINEAR)
