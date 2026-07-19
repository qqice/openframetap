from __future__ import annotations

import hashlib
import json
from pathlib import Path
import subprocess

import pytest

np = pytest.importorskip("numpy")
cv2 = pytest.importorskip("cv2")

from openframetap.analysis.glass_latency.decoder import (
    decode_pattern,
    detect_green_pattern_quad,
    rectify_roi,
)
from openframetap.analysis.glass_latency.models import ROI, ROIConfig, parse_transform
from openframetap.analysis.glass_latency.session import (
    analyze_glass_latency,
    sanitized_peer,
    rescale_capture_timestamps,
    validate_phone_fps,
)
from openframetap.analysis.glass_latency.statistics import (
    latency_statistics,
    percentile,
    run_length_encode,
)
from openframetap.analysis.glass_latency.timing import PatternTimingMap, unwrap_frame_ids
from openframetap.analysis.glass_latency.video import probe_video
from openframetap.tools.latency_pattern import pattern_layout, render_pattern_image


def _bgr(frame_id: int, *, invert: bool = False, width: int = 1600, height: int = 900):
    rgb = np.array(
        render_pattern_image(frame_id, width=width, height=height, invert=invert)
    )
    return cv2.cvtColor(rgb, cv2.COLOR_RGB2BGR)


def _timing_log(path: Path, count: int = 200, refresh_hz: float = 60.0) -> None:
    start = 10_000_000_000
    interval = round(1e9 / refresh_hz)
    with path.open("w", encoding="utf-8", newline="\n") as stream:
        for frame_id in range(count):
            stream.write(
                json.dumps(
                    {
                        "frame_id": frame_id % 65536,
                        "monotonic_ns": start + frame_id * interval,
                        "refresh_hz": refresh_hz,
                    }
                )
                + "\n"
            )


def test_roi_and_transform_serialization() -> None:
    roi = ROI.parse("1,2,300,200")
    transform = parse_transform("1,2,3,4,5,6,7,8")
    config = ROIConfig(roi, roi, transform, None)
    assert config.to_dict()["source"]["width"] == 300
    assert transform[-1] == (7.0, 8.0)
    with pytest.raises(ValueError):
        ROI.parse("1,2,3")


def test_known_pattern_decodes_without_ocr() -> None:
    decoded = decode_pattern(_bgr(4242))
    assert decoded.valid
    assert decoded.frame_id == 4242
    assert decoded.confidence > 0.9


def test_inverted_pattern_decodes_with_explicit_inversion() -> None:
    decoded = decode_pattern(_bgr(1234, invert=True), invert=True)
    assert decoded.valid and decoded.frame_id == 1234


def test_low_contrast_pattern_fails_closed() -> None:
    image = np.full((900, 1600, 3), 120, dtype=np.uint8)
    decoded = decode_pattern(image)
    assert not decoded.valid
    assert decoded.reason == "low_contrast"


def test_colored_moire_in_black_cell_is_rejected_as_white() -> None:
    image = _bgr(158)
    layout = pattern_layout(16)
    # Gray(158) bit 14 is zero.  A purple alias can have high HSV Value and
    # ordinary luma even though it is not an achromatic white pattern cell.
    for rect in (layout.top_bits[14], layout.bottom_bits[14]):
        x0, y0, x1, y1 = rect.pixels(1600, 900)
        image[y0:y1, x0:x1] = (243, 204, 213)
    decoded = decode_pattern(image)
    assert decoded.valid
    assert decoded.frame_id == 158


def test_mildly_tinted_white_cell_remains_white() -> None:
    image = _bgr(158)
    layout = pattern_layout(16)
    # Gray(158) bit 11 is one.  Mild white-balance tint must not turn it black.
    for rect in (layout.top_bits[11], layout.bottom_bits[11]):
        x0, y0, x1, y1 = rect.pixels(1600, 900)
        image[y0:y1, x0:x1] = (230, 215, 225)
    decoded = decode_pattern(image)
    assert decoded.valid
    assert decoded.frame_id == 158


def test_screen_reference_normalization_handles_cyan_dsi_white() -> None:
    image = _bgr(31337)
    white = np.all(image > 200, axis=2)
    black = np.all(image < 30, axis=2)
    image[white] = (254, 240, 172)
    image[black] = (22, 11, 9)
    decoded = decode_pattern(image)
    assert decoded.valid
    assert decoded.frame_id == 31337


def test_single_bit_bank_error_is_mixed_refresh() -> None:
    image = _bgr(99)
    rect = pattern_layout(16).bottom_bits[0]
    x0, y0, x1, y1 = rect.pixels(1600, 900)
    current = int(image[(y0 + y1) // 2, (x0 + x1) // 2, 0])
    image[y0:y1, x0:x1] = 0 if current > 127 else 255
    decoded = decode_pattern(image)
    assert decoded.mixed_refresh
    assert decoded.adjacent_gray_transition


def test_perspective_rectification_recovers_pattern() -> None:
    source = _bgr(777)
    source_points = np.float32([[0, 0], [1599, 0], [1599, 899], [0, 899]])
    quad = np.float32([[120, 80], [1810, 140], [1700, 1080], [160, 1020]])
    matrix = cv2.getPerspectiveTransform(source_points, quad)
    photographed = cv2.warpPerspective(source, matrix, (2000, 1200))
    corrected = rectify_roi(
        photographed,
        ROI(0, 0, 2000, 1200),
        tuple(tuple(map(float, point)) for point in quad),
    )
    assert decode_pattern(corrected).frame_id == 777


def test_green_border_tracker_recovers_handheld_quad() -> None:
    source = _bgr(778)
    source_points = np.float32([[0, 0], [1599, 0], [1599, 899], [0, 899]])
    quad = np.float32([[100, 70], [1810, 120], [1710, 1080], [150, 1020]])
    matrix = cv2.getPerspectiveTransform(source_points, quad)
    photographed = cv2.warpPerspective(source, matrix, (2000, 1200))
    detected = detect_green_pattern_quad(photographed, ROI(0, 0, 2000, 1200))
    assert detected is not None
    corrected = rectify_roi(photographed, ROI(0, 0, 2000, 1200), detected)
    assert decode_pattern(corrected).frame_id == 778


def test_green_border_tracker_combines_fragmented_outer_edges() -> None:
    source = _bgr(779)
    # Break every edge away from the corners; no individual border contour can
    # describe the full screen, but the combined green-pixel hull still can.
    source[0:8, 500:1100] = 0
    source[-8:, 500:1100] = 0
    source[250:650, 0:8] = 0
    source[250:650, -8:] = 0
    detected = detect_green_pattern_quad(source, ROI(0, 0, 1600, 900))
    assert detected is not None
    corrected = rectify_roi(source, ROI(0, 0, 1600, 900), detected)
    assert decode_pattern(corrected).frame_id == 779


def test_sixteen_bit_unwrap_and_timing_lookup(tmp_path: Path) -> None:
    assert unwrap_frame_ids([65534, 65535, 0, 1], 16) == [65534, 65535, 65536, 65537]
    path = tmp_path / "timing.jsonl"
    with path.open("w", encoding="utf-8") as stream:
        for index, frame_id in enumerate((65534, 65535, 0, 1)):
            stream.write(
                json.dumps(
                    {
                        "frame_id": frame_id,
                        "monotonic_ns": 1_000_000_000 + index * 10_000_000,
                        "refresh_hz": 100.0,
                    }
                )
                + "\n"
            )
    timing = PatternTimingMap.load(path, bits=16)
    source, dsi, source_ns, dsi_ns = timing.resolve_pair(
        1, 65535, previous_source_unwrapped=None, maximum_latency_ms=100
    )
    assert (source, dsi) == (65537, 65535)
    assert source_ns - dsi_ns == 20_000_000


def test_nearest_timing_map_rejects_unphysical_offset(tmp_path: Path) -> None:
    path = tmp_path / "timing.jsonl"
    _timing_log(path)
    timing = PatternTimingMap.load(path, bits=16)
    with pytest.raises(ValueError, match="maximum latency"):
        timing.resolve_pair(100, 1, previous_source_unwrapped=None, maximum_latency_ms=100)


def test_latency_statistics_percentiles_and_invalid_exclusion() -> None:
    values = [10.0, 20.0, 30.0, 40.0, 100.0]
    summary = latency_statistics(values)
    assert summary["median_latency_ms"] == 30.0
    assert summary["p95_latency_ms"] == pytest.approx(88.0)
    assert summary["p99_latency_ms"] == pytest.approx(97.6)
    assert percentile(values, 90) == pytest.approx(76.0)
    assert latency_statistics([])["median_latency_ms"] is None


def test_decoder_confidence_gate_rejects_borderline_bit() -> None:
    image = _bgr(158)
    layout = pattern_layout(16)
    for rect in (layout.top_bits[0], layout.bottom_bits[0]):
        x0, y0, x1, y1 = rect.pixels(1600, 900)
        image[y0:y1, x0:x1] = 133
    assert not decode_pattern(image).valid


def test_phone_sampling_run_length_is_not_called_a_decoder_drop() -> None:
    runs = run_length_encode([10, 10, 10, 12, 12, 14])
    assert [item["length"] for item in runs] == [3, 2, 1]
    assert [item["frame_id"] for item in runs] == [10, 12, 14]


def test_ffprobe_vfr_metadata(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    video = tmp_path / "phone.mp4"
    video.write_bytes(b"fixture")
    response = {
        "streams": [
            {
                "codec_name": "h264",
                "width": 1920,
                "height": 1080,
                "r_frame_rate": "240/1",
                "avg_frame_rate": "239/1",
                "time_base": "1/1000",
                "duration": "0.02",
                "nb_frames": "4",
            }
        ],
        "frames": [
            {"best_effort_timestamp_time": value}
            for value in ("0.000", "0.004", "0.008", "0.013")
        ],
        "format": {"duration": "0.02"},
    }
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda *args, **kwargs: subprocess.CompletedProcess(
            args[0], 0, json.dumps(response), ""
        ),
    )
    metadata, timestamps = probe_video(video, ffprobe_bin="ffprobe")
    assert metadata["variable_frame_rate"]
    assert timestamps[-1] == 0.013


def test_empty_or_missing_video_fails_cleanly(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="missing or empty"):
        probe_video(tmp_path / "missing.mp4", ffprobe_bin="ffprobe")
    empty = tmp_path / "empty.mp4"
    empty.touch()
    with pytest.raises(ValueError, match="missing or empty"):
        probe_video(empty, ffprobe_bin="ffprobe")


def test_invalid_phone_fps_metadata_fails_closed() -> None:
    with pytest.raises(ValueError, match="phone fps"):
        validate_phone_fps(None)
    with pytest.raises(ValueError, match="phone fps"):
        validate_phone_fps(2000.0)


def test_slow_motion_pts_are_rescaled_to_capture_time() -> None:
    timestamps, scale = rescale_capture_timestamps(
        [10.0, 10.0 + 1 / 30, 10.0 + 2 / 30],
        encoded_fps=30.0,
        capture_fps=240.0,
    )
    assert scale == pytest.approx(0.125)
    assert timestamps[-1] == pytest.approx(2 / 240)


def test_missing_pattern_log_is_explicit(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError, match="pattern timing log"):
        PatternTimingMap.load(tmp_path / "missing.jsonl", bits=16)


def test_sanitized_peer_and_private_git_exclusion(tmp_path: Path) -> None:
    private = tmp_path / "artifacts" / "private" / "glass-test"
    assert sanitized_peer(private) == tmp_path / "artifacts" / "sanitized" / "glass-test"
    gitignore = Path(".gitignore").read_text(encoding="utf-8")
    assert "artifacts" in gitignore


def test_end_to_end_synthetic_phone_video(tmp_path: Path) -> None:
    video = tmp_path / "synthetic-phone.mp4"
    width, height = 640, 360
    writer = cv2.VideoWriter(
        str(video), cv2.VideoWriter_fourcc(*"mp4v"), 240.0, (width * 2, height)
    )
    assert writer.isOpened()
    for index in range(96):
        source_id = 20 + index // 4
        dsi_id = source_id - 6
        source = _bgr(source_id, width=width, height=height)
        dsi = _bgr(dsi_id, width=width, height=height)
        writer.write(np.concatenate([source, dsi], axis=1))
    writer.release()
    original_hash = hashlib.sha256(video.read_bytes()).hexdigest()
    timing = tmp_path / "pattern-timing.jsonl"
    _timing_log(timing)
    output = tmp_path / "artifacts" / "private" / "glass-synthetic"
    result = analyze_glass_latency(
        video,
        pattern_log=timing,
        source_roi=ROI(0, 0, width, height),
        dsi_roi=ROI(width, 0, width, height),
        interactive_roi=False,
        phone_fps=240.0,
        output=output,
        source_transform=None,
        dsi_transform=None,
        maximum_latency_ms=500,
    )
    summary = result["summary"]
    assert summary["decode_success_rate"] > 0.90
    assert summary["median_latency_ms"] == pytest.approx(100.0, abs=0.1)
    assert hashlib.sha256(video.read_bytes()).hexdigest() == original_hash
    assert (output / "plots" / "latency-cdf.png").is_file()
    sanitized = sanitized_peer(output)
    sanitized_text = (sanitized / "summary.json").read_text(encoding="utf-8")
    assert str(video) not in sanitized_text
    assert (sanitized / "checksums.sha256").is_file()
