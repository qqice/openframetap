"""Selection policy for Pocket 3 preview without sending DJI commands."""

from __future__ import annotations

from pathlib import Path

from openframetap.video.decoder_probe import discover_decoders
from openframetap.video.pipelines import PipelineProfile, live_pipeline, offline_pipeline


def select_decoder(requested: str = "auto") -> str:
    capabilities = discover_decoders()
    available = {item.decoder_name for item in capabilities if item.available}
    if requested != "auto":
        if requested not in available:
            raise RuntimeError(f"requested decoder is unavailable: {requested}")
        return requested
    for candidate in ("mppvideodec", "avdec_h264"):
        if candidate in available:
            return candidate
    raise RuntimeError("no explicit H.264 decoder is available")


def file_preview_spec(
    path: Path,
    *,
    decoder: str = "auto",
    fullscreen: bool = False,
) :
    selected = select_decoder(decoder)
    return offline_pipeline(
        path,
        decoder=selected,
        sink="wayland",
        fullscreen=fullscreen,
        profile=PipelineProfile.STABLE,
    )


def live_preview_spec(
    url: str,
    *,
    source: str = "auto",
    decoder: str = "auto",
    fullscreen: bool = True,
    profile: str = PipelineProfile.LOW_LATENCY.value,
    sink: str = "wayland",
):
    selected_decoder = select_decoder(decoder)
    selected_source = "rtmp" if source == "auto" else source
    return live_pipeline(
        url,
        source=selected_source,
        decoder=selected_decoder,
        sink=sink,
        fullscreen=fullscreen,
        profile=PipelineProfile(profile),
    )
