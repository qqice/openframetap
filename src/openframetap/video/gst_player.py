"""GStreamer child process with post-surface Wayland fullscreen handling."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import re
import signal
import sys
import time
from typing import Any


STATS_PREFIX = "OPENFRAMETAP_PLAYER_STATS="
EVENT_PREFIX = "OPENFRAMETAP_PLAYER_EVENT="
FRAME_PREFIX = "OPENFRAMETAP_FRAME="


def should_apply_fullscreen(
    *, requested: bool, already_applied: bool, frames_rendered: int
) -> bool:
    """Only touch waylandsink fullscreen after it has created a render surface."""

    return requested and not already_applied and frames_rendered > 0


def _load_gstreamer() -> tuple[Any, Any]:
    system_packages = "/usr/lib/python3/dist-packages"
    if system_packages not in sys.path and Path(system_packages).is_dir():
        sys.path.append(system_packages)
    try:
        import gi
    except ImportError as exc:
        raise RuntimeError("PyGObject is unavailable to the runtime Python") from exc
    gi.require_version("Gst", "1.0")
    from gi.repository import Gst, GLib

    Gst.init(None)
    return Gst, GLib


def _pipeline_tokens(path: Path) -> tuple[list[str], bool]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    argv = payload.get("argv")
    if not isinstance(argv, list) or argv[:1] != ["gst-launch-1.0"]:
        raise ValueError("pipeline JSON does not contain a gst-launch argv")
    tokens = [str(item) for item in argv[1:] if item not in {"-e", "-v"}]
    return tokens, bool(payload.get("fullscreen", False))


def _integer_property(element: Any, name: str) -> int:
    try:
        return int(element.get_property(name))
    except (TypeError, ValueError):
        return 0


def run(path: Path) -> int:
    Gst, _GLib = _load_gstreamer()
    tokens, fullscreen_requested = _pipeline_tokens(path)
    pipeline = Gst.parse_launchv(tokens)
    fps_sink = pipeline.get_by_name("fpsdisplaysink0")
    if fps_sink is None:
        raise RuntimeError("pipeline has no fpsdisplaysink0")
    video_sink = fps_sink.get_property("video-sink")
    if video_sink is None:
        raise RuntimeError("fpsdisplaysink has no video sink")
    if fullscreen_requested and video_sink.get_factory().get_name() != "waylandsink":
        raise RuntimeError("fullscreen was requested without an explicit waylandsink")

    stop_requested = False
    fullscreen_applied = False
    first_frame_reported = False
    maximum_reported_fps: float | None = None
    last_frame_report_ns = 0
    error_message: str | None = None
    eos = False
    started_ns = time.monotonic_ns()

    def request_stop(_signum: int, _frame: object) -> None:
        nonlocal stop_requested
        stop_requested = True

    signal.signal(signal.SIGTERM, request_stop)
    signal.signal(signal.SIGINT, request_stop)
    bus = pipeline.get_bus()
    result = pipeline.set_state(Gst.State.PLAYING)
    if result == Gst.StateChangeReturn.FAILURE:
        raise RuntimeError("GStreamer failed to enter PLAYING")
    print(
        EVENT_PREFIX
        + json.dumps(
            {"event": "playing_requested", "monotonic_ns": time.monotonic_ns()},
            sort_keys=True,
        ),
        flush=True,
    )
    try:
        while not stop_requested:
            frames = _integer_property(fps_sink, "frames-rendered")
            if frames > 0 and not first_frame_reported:
                first_frame_reported = True
                print(
                    EVENT_PREFIX
                    + json.dumps(
                        {
                            "event": "first_frame_observed",
                            "frames_rendered": frames,
                            "monotonic_ns": time.monotonic_ns(),
                        },
                        sort_keys=True,
                    ),
                    flush=True,
                )
            try:
                last_message = str(fps_sink.get_property("last-message") or "")
            except TypeError:
                last_message = ""
            match = re.search(r"(?:current|fps):\s*([\d.]+)", last_message)
            current_fps = None
            if match:
                current_fps = float(match.group(1))
                maximum_reported_fps = max(maximum_reported_fps or 0.0, current_fps)
            now_ns = time.monotonic_ns()
            if frames > 0 and now_ns - last_frame_report_ns >= 500_000_000:
                last_frame_report_ns = now_ns
                print(
                    FRAME_PREFIX
                    + json.dumps(
                        {
                            "monotonic_ns": now_ns,
                            "rendered_frames": frames,
                            "dropped_frames": _integer_property(
                                fps_sink, "frames-dropped"
                            ),
                            "current_fps": current_fps,
                        },
                        sort_keys=True,
                    ),
                    flush=True,
                )
            if should_apply_fullscreen(
                requested=fullscreen_requested,
                already_applied=fullscreen_applied,
                frames_rendered=frames,
            ):
                video_sink.set_property("fullscreen", True)
                fullscreen_applied = bool(video_sink.get_property("fullscreen"))
                print(
                    EVENT_PREFIX
                    + json.dumps(
                        {
                            "event": "fullscreen_applied",
                            "frames_rendered": frames,
                            "monotonic_ns": time.monotonic_ns(),
                            "property_value": fullscreen_applied,
                        },
                        sort_keys=True,
                    ),
                    flush=True,
                )
            message = bus.timed_pop_filtered(
                100 * Gst.MSECOND,
                Gst.MessageType.ERROR | Gst.MessageType.EOS | Gst.MessageType.STATE_CHANGED,
            )
            if message is None:
                continue
            if message.type == Gst.MessageType.ERROR:
                error, debug = message.parse_error()
                error_message = str(error)
                print(
                    EVENT_PREFIX
                    + json.dumps(
                        {"event": "error", "message": error_message, "debug": debug},
                        sort_keys=True,
                    ),
                    flush=True,
                )
                break
            if message.type == Gst.MessageType.EOS:
                eos = True
                break
    finally:
        rendered = _integer_property(fps_sink, "frames-rendered")
        dropped = _integer_property(fps_sink, "frames-dropped")
        elapsed = max((time.monotonic_ns() - started_ns) / 1_000_000_000, 1e-9)
        stats = {
            "rendered_frames": rendered,
            "dropped_frames": dropped,
            "last_reported_fps": rendered / elapsed,
            "maximum_reported_fps": maximum_reported_fps,
            "fullscreen_requested": fullscreen_requested,
            "fullscreen_applied": fullscreen_applied,
            "waylandsink_fullscreen_property": bool(
                video_sink.get_property("fullscreen")
            ),
            "eos": eos,
            "error": error_message,
        }
        pipeline.set_state(Gst.State.NULL)
        print(STATS_PREFIX + json.dumps(stats, sort_keys=True), flush=True)
    return 1 if error_message else 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--pipeline-json", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        return run(args.pipeline_json)
    except Exception as exc:
        print(
            EVENT_PREFIX + json.dumps({"event": "fatal", "message": str(exc)}),
            flush=True,
        )
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
