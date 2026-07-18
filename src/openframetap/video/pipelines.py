"""Structured GStreamer pipeline specifications for Pocket 3 video."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from enum import Enum
from pathlib import Path


class PipelineProfile(str, Enum):
    STABLE = "stable"
    LOW_LATENCY = "low-latency"
    AGGRESSIVE = "aggressive-low-latency"


@dataclass(frozen=True, slots=True)
class PipelineSpec:
    source_kind: str
    decoder: str
    sink: str
    profile: str
    elements: tuple[str, ...]
    argv: tuple[str, ...]
    audio_enabled: bool = False

    def to_dict(self) -> dict:
        return asdict(self)


def profile_parameters(profile: PipelineProfile) -> dict:
    return {
        PipelineProfile.STABLE: {
            "queue": ("max-size-buffers=8", "leaky=no"),
            "sink_sync": "true",
            "rtsp_latency": "150",
            "drop_on_latency": "false",
        },
        PipelineProfile.LOW_LATENCY: {
            "queue": ("max-size-buffers=3", "leaky=downstream"),
            "sink_sync": "false",
            "rtsp_latency": "50",
            "drop_on_latency": "true",
        },
        PipelineProfile.AGGRESSIVE: {
            "queue": ("max-size-buffers=1", "leaky=downstream"),
            "sink_sync": "false",
            "rtsp_latency": "0",
            "drop_on_latency": "true",
        },
    }[profile]


def _sink_tokens(sink: str, *, fullscreen: bool, sync: str) -> tuple[str, ...]:
    if sink == "fakesink":
        return (
            "fpsdisplaysink",
            "text-overlay=false",
            "video-sink=fakesink",
            "silent=false",
            "fps-update-interval=50",
            f"sync={sync}",
        )
    if sink == "wayland":
        nested = f"waylandsink fullscreen={'true' if fullscreen else 'false'} sync={sync}"
        return (
            "fpsdisplaysink",
            "text-overlay=false",
            f"video-sink={nested}",
            "silent=false",
            "fps-update-interval=500",
            f"sync={sync}",
        )
    raise ValueError(f"unsupported sink: {sink}")


def offline_pipeline(
    path: Path,
    *,
    decoder: str,
    sink: str = "fakesink",
    fullscreen: bool = False,
    profile: PipelineProfile = PipelineProfile.STABLE,
) -> PipelineSpec:
    params = profile_parameters(profile)
    elements = (
        "filesrc",
        "flvdemux",
        "queue",
        "h264parse",
        "capsfilter",
        decoder,
        "fpsdisplaysink" if sink == "fakesink" else "waylandsink",
    )
    argv = (
        "gst-launch-1.0",
        "-e",
        "filesrc",
        f"location={path}",
        "!",
        "flvdemux",
        "name=demux",
        "demux.video",
        "!",
        "queue",
        *params["queue"],
        "!",
        "h264parse",
        "config-interval=-1",
        "!",
        "video/x-h264,stream-format=byte-stream,alignment=au",
        "!",
        decoder,
        "!",
        *_sink_tokens(sink, fullscreen=fullscreen, sync=params["sink_sync"]),
    )
    return PipelineSpec("file", decoder, sink, profile.value, elements, argv)


def live_pipeline(
    url: str,
    *,
    source: str,
    decoder: str,
    sink: str = "wayland",
    fullscreen: bool = True,
    profile: PipelineProfile = PipelineProfile.LOW_LATENCY,
) -> PipelineSpec:
    if source == "hls":
        raise ValueError("HLS is forbidden as the low-latency default")
    params = profile_parameters(profile)
    if source == "rtmp":
        source_tokens = ("rtmpsrc", f"location={url}", "!", "flvdemux", "name=demux", "demux.video")
        elements = ("rtmpsrc", "flvdemux", "queue", "h264parse", "capsfilter", decoder)
    elif source == "rtsp":
        source_tokens = (
            "rtspsrc",
            f"location={url}",
            f"latency={params['rtsp_latency']}",
            f"drop-on-latency={params['drop_on_latency']}",
            "protocols=tcp",
            "!",
            "rtph264depay",
        )
        elements = ("rtspsrc", "rtph264depay", "queue", "h264parse", "capsfilter", decoder)
    else:
        raise ValueError(f"unsupported live source: {source}")
    argv = (
        "gst-launch-1.0",
        "-e",
        *source_tokens,
        "!",
        "queue",
        *params["queue"],
        "!",
        "h264parse",
        "config-interval=-1",
        "!",
        "video/x-h264,stream-format=byte-stream,alignment=au",
        "!",
        decoder,
        "!",
        *_sink_tokens(sink, fullscreen=fullscreen, sync=params["sink_sync"]),
    )
    return PipelineSpec(source, decoder, sink, profile.value, elements, argv)
