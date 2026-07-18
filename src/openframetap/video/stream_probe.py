"""Normalize ffprobe JSON while preserving unknown stream fields."""

from __future__ import annotations

from fractions import Fraction
from typing import Any


def _rate(value: str | None) -> float | None:
    if not value or value in {"0/0", "N/A"}:
        return None
    try:
        return float(Fraction(value))
    except (ValueError, ZeroDivisionError):
        return None


def parse_ffprobe(payload: dict[str, Any]) -> dict[str, Any]:
    streams = list(payload.get("streams") or [])
    video = next((item for item in streams if item.get("codec_type") == "video"), None)
    audio = next((item for item in streams if item.get("codec_type") == "audio"), None)
    format_info = dict(payload.get("format") or {})
    return {
        "video": (
            {
                "codec": video.get("codec_name"),
                "profile": video.get("profile"),
                "level": video.get("level"),
                "pixel_format": video.get("pix_fmt"),
                "width": video.get("width"),
                "height": video.get("height"),
                "average_frame_rate_raw": video.get("avg_frame_rate"),
                "average_frame_rate": _rate(video.get("avg_frame_rate")),
                "real_frame_rate_raw": video.get("r_frame_rate"),
                "real_frame_rate": _rate(video.get("r_frame_rate")),
                "time_base": video.get("time_base"),
                "bit_rate": video.get("bit_rate"),
                "raw": video,
            }
            if video
            else None
        ),
        "audio": (
            {
                "codec": audio.get("codec_name"),
                "sample_rate": audio.get("sample_rate"),
                "channels": audio.get("channels"),
                "channel_layout": audio.get("channel_layout"),
                "bit_rate": audio.get("bit_rate"),
                "raw": audio,
            }
            if audio
            else None
        ),
        "format": {
            "name": format_info.get("format_name"),
            "long_name": format_info.get("format_long_name"),
            "start_time": format_info.get("start_time"),
            "duration": format_info.get("duration"),
            "size": format_info.get("size"),
            "bit_rate": format_info.get("bit_rate"),
            "raw": format_info,
        },
        "stream_count": len(streams),
        "unknown_streams": [
            item for item in streams if item.get("codec_type") not in {"video", "audio"}
        ],
    }


def redact_probe_metadata(value: Any, replacements: dict[str, str]) -> Any:
    """Recursively redact URLs, keys, SSIDs, or passwords before serialization."""

    if isinstance(value, dict):
        return {key: redact_probe_metadata(item, replacements) for key, item in value.items()}
    if isinstance(value, list):
        return [redact_probe_metadata(item, replacements) for item in value]
    if isinstance(value, str):
        rendered = value
        for sensitive, replacement in replacements.items():
            if sensitive:
                rendered = rendered.replace(sensitive, replacement)
        return rendered
    return value
