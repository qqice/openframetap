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


def _mean_rect(image: Any, rect: NormalizedRect):
    height, width = image.shape[:2]
    x0, y0, x1, y1 = rect.pixels(width, height)
    margin_x = max(1, (x1 - x0) // 6)
    margin_y = max(1, (y1 - y0) // 6)
    sample = image[y0 + margin_y : y1 - margin_y, x0 + margin_x : x1 - margin_x]
    if sample.size == 0:
        raise ValueError("pattern cell falls outside rectified ROI")
    if len(sample.shape) == 2:
        return float(sample.mean())
    return sample.astype("float32").mean(axis=(0, 1))


def decode_bank(
    image: Any,
    rects: tuple[NormalizedRect, ...],
    black_reference: NormalizedRect,
    white_reference: NormalizedRect,
    *,
    invert: bool = False,
    minimum_contrast: float = 35.0,
    minimum_confidence: float = 0.10,
) -> BankDecode:
    import numpy as np

    first_reference = _mean_rect(image, black_reference)
    second_reference = _mean_rect(image, white_reference)
    raw_values = tuple(_mean_rect(image, rect) for rect in rects)
    if np.isscalar(first_reference):
        dark, bright = sorted((float(first_reference), float(second_reference)))
        contrast = bright - dark
        values = tuple(float(value) for value in raw_values)
    else:
        first = np.asarray(first_reference, dtype=np.float32)
        second = np.asarray(second_reference, dtype=np.float32)
        dark_vector, bright_vector = (
            (first, second) if first.mean() <= second.mean() else (second, first)
        )
        channel_range = bright_vector - dark_vector
        observed_contrast = float(np.sqrt(np.mean(np.square(channel_range))))
        if observed_contrast < minimum_contrast:
            values = tuple(float(np.asarray(value).mean()) for value in raw_values)
            return BankDecode(
                None,
                None,
                (),
                values,
                None,
                float(dark_vector.mean()),
                float(bright_vector.mean()),
                observed_contrast,
                0.0,
                False,
                "low_contrast",
            )
        fallback = max(float(channel_range.mean()), 1.0)
        channel_range = np.where(channel_range >= 10.0, channel_range, fallback)

        def achromatic_score(sample) -> float:
            normalized = (np.asarray(sample, dtype=np.float32) - dark_vector) / channel_range
            minimum = float(normalized.min())
            chroma = float(normalized.max() - minimum)
            return float(np.clip(minimum - 3.0 * chroma, 0.0, 1.0) * 255.0)

        dark, bright = 0.0, 255.0
        contrast = 255.0
        values = tuple(achromatic_score(value) for value in raw_values)
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
    minimum_confidence: float = 0.10,
) -> PatternDecode:
    if image is None or getattr(image, "size", 0) == 0:
        raise ValueError("cannot decode an empty image")
    # Camera/display moire creates bright but strongly chromatic purple bands in
    # nominally black cells.  Decode achromatic brightness instead of ordinary
    # luma or HSV Value: a true white cell has all three channels high, while a
    # colour alias is penalized by its channel spread.  The fixed coefficient is
    # intentionally conservative and is covered by synthetic and real-frame
    # regression tests; this is not an adaptive search for a convenient answer.
    layout = pattern_layout(bits)
    top = decode_bank(
        image,
        layout.top_bits,
        layout.top_black_reference,
        layout.top_white_reference,
        invert=invert,
        minimum_contrast=minimum_contrast,
        minimum_confidence=minimum_confidence,
    )
    bottom = decode_bank(
        image,
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


def order_quad(points: Any) -> tuple[tuple[float, float], ...]:
    import numpy as np

    array = np.asarray(points, dtype=np.float32).reshape(4, 2)
    sums = array.sum(axis=1)
    differences = array[:, 0] - array[:, 1]
    ordered = (
        array[int(sums.argmin())],
        array[int(differences.argmax())],
        array[int(sums.argmax())],
        array[int(differences.argmin())],
    )
    return tuple((float(point[0]), float(point[1])) for point in ordered)


def detect_green_pattern_quad(
    frame: Any,
    roi,
    *,
    minimum_pixels: int = 80,
    maximum_detection_width: int = 1000,
) -> tuple[tuple[float, float], ...] | None:
    """Track the outer green ROI border; internal arrow pixels stay inside its hull."""

    import cv2
    import numpy as np

    frame_height, frame_width = frame.shape[:2]
    if roi.x + roi.width > frame_width or roi.y + roi.height > frame_height:
        raise ValueError("ROI exceeds video frame bounds")
    crop = frame[roi.y : roi.y + roi.height, roi.x : roi.x + roi.width]
    scale = min(1.0, maximum_detection_width / max(crop.shape[1], 1))
    if scale < 1.0:
        crop = cv2.resize(crop, None, fx=scale, fy=scale, interpolation=cv2.INTER_AREA)
    hsv = cv2.cvtColor(crop, cv2.COLOR_BGR2HSV)
    mask = cv2.inRange(
        hsv,
        np.array([35, 70, 45], dtype=np.uint8),
        np.array([100, 255, 255], dtype=np.uint8),
    )
    if cv2.countNonZero(mask) < minimum_pixels:
        return None
    contours, _hierarchy = cv2.findContours(
        mask, cv2.RETR_LIST, cv2.CHAIN_APPROX_SIMPLE
    )
    if not contours:
        return None
    candidates = []
    # Exposure and rolling-shutter bands can split the thin outer border into
    # several contours.  Its pixels still surround the internal green arrow,
    # so their combined convex hull is the strongest candidate.  Individual
    # contours remain as a fallback for frames containing isolated green noise.
    all_green = cv2.findNonZero(mask)
    hulls = [cv2.convexHull(all_green)] if all_green is not None else []
    hulls.extend(
        cv2.convexHull(contour)
        for contour in sorted(contours, key=cv2.contourArea, reverse=True)[:20]
    )
    for hull in hulls:
        perimeter = cv2.arcLength(hull, True)
        approximation = cv2.approxPolyDP(hull, 0.02 * perimeter, True)
        if len(approximation) == 4:
            box = approximation.reshape(4, 2).astype(np.float32)
        else:
            box = cv2.boxPoints(cv2.minAreaRect(hull))
        box[:, 0] = box[:, 0] / scale + roi.x
        box[:, 1] = box[:, 1] / scale + roi.y
        ordered = order_quad(box)
        points = np.asarray(ordered, dtype=np.float32)
        top = float(np.linalg.norm(points[1] - points[0]))
        bottom = float(np.linalg.norm(points[2] - points[3]))
        left = float(np.linalg.norm(points[3] - points[0]))
        right = float(np.linalg.norm(points[2] - points[1]))
        width = (top + bottom) / 2.0
        height = (left + right) / 2.0
        if min(width, height) <= 0:
            continue
        aspect = max(width, height) / min(width, height)
        area_ratio = abs(cv2.contourArea(points)) / (roi.width * roi.height)
        if 1.35 <= aspect <= 2.35 and area_ratio >= 0.20:
            candidates.append((area_ratio, ordered))
    return max(candidates, key=lambda item: item[0])[1] if candidates else None
