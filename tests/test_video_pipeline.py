from __future__ import annotations

from pathlib import Path
import re

import pytest

from openframetap.display.dsi import parse_mutter_state
from openframetap.display.session import parse_loginctl_session, parse_overview_active
from openframetap.video.decoder_probe import parse_gst_inspect
from openframetap.video.metrics import read_temperature_c, summarize_metrics, MetricSample
from openframetap.video.live_preview import (
    checksum_manifest,
    decode_error_lines,
    parse_fps_messages,
    parse_frame_reports,
)
from openframetap.video.gst_player import should_apply_fullscreen
from openframetap.video.latency import parse_latency_tracer
from openframetap.video.pipelines import (
    PipelineProfile,
    live_pipeline,
    offline_pipeline,
    profile_parameters,
)
from openframetap.tools.latency_pattern import pattern_state


MPP_INSPECT = """
Plugin Details:
  Filename                 /usr/lib/gstreamer-1.0/libgstrockchipmpp.so
  Version                  1.14.4
  SINK template: 'sink'
    Capabilities:
      video/x-h264
  SRC template: 'src'
    Capabilities:
      video/x-raw(memory:DMABuf)
"""


def test_decoder_capability_parser_confirms_mpp_and_dmabuf() -> None:
    result = parse_gst_inspect("mppvideodec", MPP_INSPECT, kind="hardware")
    assert result.available
    assert result.hardware_or_software == "hardware"
    assert result.accepted_caps == ["video/x-h264"]
    assert result.output_caps == ["video/x-raw(memory:DMABuf)"]
    assert result.zero_copy_candidate
    assert result.plugin_version == "1.14.4"


def test_missing_gstreamer_plugin_is_explicit() -> None:
    result = parse_gst_inspect(
        "v4l2h264dec", "No such element or plugin 'v4l2h264dec'", returncode=255
    )
    assert not result.available
    assert result.failure_reason == "plugin unavailable"


def test_pipeline_profiles_have_bounded_leaky_queues() -> None:
    stable = profile_parameters(PipelineProfile.STABLE)
    low = profile_parameters(PipelineProfile.LOW_LATENCY)
    aggressive = profile_parameters(PipelineProfile.AGGRESSIVE)
    assert "leaky=no" in stable["queue"]
    assert "leaky=downstream" in low["queue"]
    assert "max-size-buffers=1" in aggressive["queue"]
    assert int(aggressive["rtsp_latency"]) < int(low["rtsp_latency"])


def test_offline_pipeline_has_explicit_decoder_and_no_fallback(tmp_path: Path) -> None:
    spec = offline_pipeline(tmp_path / "sample.flv", decoder="mppvideodec")
    assert "mppvideodec" in spec.elements
    assert "decodebin" not in spec.argv
    assert "h264parse" in spec.argv


def test_live_rtmp_and_rtsp_sources_are_structured_and_hls_is_forbidden() -> None:
    rtmp = live_pipeline(
        "rtmp://192.168.1.229:1935/live/redacted",
        source="rtmp",
        decoder="mppvideodec",
    )
    rtsp = live_pipeline(
        "rtsp://192.168.1.229:8554/live/redacted",
        source="rtsp",
        decoder="mppvideodec",
    )
    assert rtmp.elements[:2] == ("rtmpsrc", "flvdemux")
    assert "demux.audio" in rtmp.argv
    assert "fakesink" in rtmp.argv
    assert rtmp.audio_enabled is False
    assert rtsp.elements[:2] == ("rtspsrc", "rtph264depay")
    assert "leaky=downstream" in rtmp.argv
    assert any("waylandsink fullscreen=false" in item for item in rtmp.argv)
    assert rtmp.fullscreen is True
    with pytest.raises(ValueError, match="HLS"):
        live_pipeline("http://invalid", source="hls", decoder="mppvideodec")


def test_active_wayland_session_recovers_environment_missing_from_ssh(tmp_path: Path) -> None:
    runtime = tmp_path / "1000"
    runtime.mkdir()
    (runtime / "wayland-0").touch()
    session = parse_loginctl_session(
        "3",
        "User=1000\nName=qqice\nSeat=seat0\nType=wayland\nActive=yes\nRemote=no\n",
        runtime_root=tmp_path,
    )
    env = session.environment()
    assert env["XDG_RUNTIME_DIR"] == str(runtime)
    assert env["WAYLAND_DISPLAY"] == "wayland-0"
    assert env["DBUS_SESSION_BUS_ADDRESS"].endswith("/bus")


def test_gnome_overview_property_parser() -> None:
    assert parse_overview_active("(<true>,)") is True
    assert parse_overview_active("(<false>,)") is False
    with pytest.raises(ValueError):
        parse_overview_active("()")


def test_dsi_rotation_yields_landscape_logical_resolution() -> None:
    text = "[(0, 0, 1.0, uint32 1, true, [('DSI-1', 'unknown', 'unknown', 'unknown')])]"
    assert parse_mutter_state(text, physical_width=720, physical_height=1280) == (
        1280,
        720,
        1,
    )


def test_temperature_and_metric_summary(tmp_path: Path) -> None:
    zone = tmp_path / "thermal_zone0"
    zone.mkdir()
    (zone / "temp").write_text("52125\n")
    assert read_temperature_c(tmp_path) == 52.125
    samples = [
        MetricSample(1, 10.0, 100, 50.0, 1.0),
        MetricSample(2, 30.0, 300, 54.0, 1.2),
    ]
    summary = summarize_metrics(samples)
    assert summary["average_cpu_percent"] == 20.0
    assert summary["peak_rss_bytes"] == 300
    assert summary["peak_temperature_c"] == 54.0


def test_gstreamer_qos_frame_messages_are_serialized() -> None:
    text = """
last-message = rendered: 26, dropped: 5, current: 29.96, average: 25.57
last-message = rendered: 56, dropped: 5, current: 30.00, average: 27.77
"""
    payload = parse_fps_messages(text)
    assert payload["rendered_frames"] == 56
    assert payload["dropped_frames"] == 5
    assert payload["last_reported_fps"] == 30.0
    assert parse_frame_reports(text)[-1] == {
        "rendered_frames": 56,
        "dropped_frames": 5,
        "current_fps": 30.0,
    }


def test_structured_player_stats_override_debug_log_messages() -> None:
    text = (
        "last-message = rendered: 12, dropped: 1, current: 20.0\n"
        'OPENFRAMETAP_PLAYER_STATS={"rendered_frames": 42, "dropped_frames": 3, '
        '"last_reported_fps": 29.9, "maximum_reported_fps": null}\n'
    )
    assert parse_fps_messages(text)["rendered_frames"] == 42
    assert decode_error_lines(text) == []


def test_structured_frame_reports_preserve_monotonic_clock() -> None:
    text = (
        'OPENFRAMETAP_FRAME={"monotonic_ns": 123, "rendered_frames": 7, '
        '"dropped_frames": 1, "current_fps": 29.8}\n'
    )
    assert parse_frame_reports(text)[0]["monotonic_ns"] == 123


def test_checksum_manifest_uses_portable_relative_paths(tmp_path: Path) -> None:
    screenshot = tmp_path / "screenshots" / "preview.png"
    screenshot.parent.mkdir()
    screenshot.write_bytes(b"frame")
    manifest = checksum_manifest([screenshot], tmp_path)
    assert manifest.endswith("  screenshots/preview.png\n")
    assert str(tmp_path) not in manifest


def test_wayland_fullscreen_is_only_applied_after_first_frame() -> None:
    assert not should_apply_fullscreen(
        requested=True, already_applied=False, frames_rendered=0
    )
    assert should_apply_fullscreen(
        requested=True, already_applied=False, frames_rendered=1
    )
    assert not should_apply_fullscreen(
        requested=True, already_applied=True, frames_rendered=2
    )
    assert not should_apply_fullscreen(
        requested=False, already_applied=False, frames_rendered=2
    )


def test_expected_flv_eos_warning_is_not_a_decode_error() -> None:
    line = "failed when pulling 4 bytes from offset 1234: eos"
    assert not re.search(
        r"\bERROR\b|not-negotiated|No valid frames|Error while opening decoder|decoder[^\n]*failed",
        line,
        re.IGNORECASE,
    )


def test_latency_tracer_is_labeled_internal_not_glass_to_glass() -> None:
    payload = parse_latency_tracer(
        "latency, src-element-id=(string)src, time=(guint64)12000000;\n"
        "latency, src-element-id=(string)src, time=(guint64)18000000;\n"
    )
    assert payload["internal_latency_available"]
    assert payload["average_ms"] == 15.0
    assert payload["glass_to_glass_measured"] is False


def test_latency_pattern_has_frame_id_time_and_flash_state() -> None:
    first = pattern_state(0, 12_345_678_000)
    later = pattern_state(15, 12_595_678_000)
    assert first.milliseconds == 12_345
    assert first.white is True
    assert later.white is False
