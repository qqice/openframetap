from __future__ import annotations

from fractions import Fraction
import json
from pathlib import Path
import shutil
import subprocess


def _rate(value: str | None) -> float | None:
    if not value or value in {"0/0", "N/A"}:
        return None
    try:
        return float(Fraction(value))
    except (ValueError, ZeroDivisionError):
        return None


def probe_video(path: Path, *, ffprobe_bin: str | None = None) -> tuple[dict, list[float]]:
    if not path.is_file() or path.stat().st_size == 0:
        raise ValueError("phone video is missing or empty")
    executable = ffprobe_bin or shutil.which("ffprobe")
    if not executable:
        raise RuntimeError("ffprobe is unavailable")
    result = subprocess.run(
        [
            executable,
            "-v",
            "error",
            "-select_streams",
            "v:0",
            "-show_streams",
            "-show_format",
            "-show_frames",
            "-show_entries",
            "stream=codec_name,width,height,r_frame_rate,avg_frame_rate,time_base,duration,nb_frames:format=duration:frame=best_effort_timestamp_time,pts_time,pkt_duration_time",
            "-of",
            "json",
            str(path),
        ],
        text=True,
        capture_output=True,
        check=False,
        timeout=120,
    )
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or "ffprobe failed")
    payload = json.loads(result.stdout)
    streams = payload.get("streams") or []
    if not streams:
        raise ValueError("phone video has no video stream")
    stream = streams[0]
    timestamps = []
    for frame in payload.get("frames") or []:
        raw = frame.get("best_effort_timestamp_time", frame.get("pts_time"))
        if raw not in (None, "N/A"):
            timestamps.append(float(raw))
    if not timestamps:
        raise ValueError("phone video contains no usable frame timestamps")
    deltas = [right - left for left, right in zip(timestamps, timestamps[1:]) if right > left]
    nominal = _rate(stream.get("r_frame_rate"))
    average = _rate(stream.get("avg_frame_rate"))
    delta_spread = max(deltas) - min(deltas) if deltas else 0.0
    metadata = {
        "codec": stream.get("codec_name"),
        "width": int(stream.get("width", 0)),
        "height": int(stream.get("height", 0)),
        "nominal_fps": nominal,
        "average_fps": average,
        "time_base": stream.get("time_base"),
        "duration_seconds": float(
            stream.get("duration") or payload.get("format", {}).get("duration") or 0
        ),
        "declared_frames": int(stream["nb_frames"]) if str(stream.get("nb_frames", "")).isdigit() else None,
        "timestamp_count": len(timestamps),
        "variable_frame_rate": bool(
            (nominal and average and abs(nominal - average) > 0.01)
            or delta_spread > 0.0005
        ),
        "timestamp_delta_min_seconds": min(deltas) if deltas else None,
        "timestamp_delta_max_seconds": max(deltas) if deltas else None,
    }
    return metadata, timestamps


def first_video_frame(path: Path):
    import cv2

    capture = cv2.VideoCapture(str(path))
    ok, frame = capture.read()
    capture.release()
    if not ok or frame is None:
        raise ValueError("unable to decode the first phone-video frame")
    return frame


def select_rois_interactively(frame) -> tuple[tuple[int, int, int, int], tuple[int, int, int, int]]:
    import cv2

    source = cv2.selectROI("Select Windows SOURCE pattern, then Enter", frame, False, False)
    cv2.destroyWindow("Select Windows SOURCE pattern, then Enter")
    if source[2] <= 0 or source[3] <= 0:
        raise ValueError("SOURCE ROI selection was cancelled")
    dsi = cv2.selectROI("Select ROCK 4D DSI pattern, then Enter", frame, False, False)
    cv2.destroyWindow("Select ROCK 4D DSI pattern, then Enter")
    cv2.destroyAllWindows()
    if dsi[2] <= 0 or dsi[3] <= 0:
        raise ValueError("DSI ROI selection was cancelled")
    return tuple(map(int, source)), tuple(map(int, dsi))
