"""Foreground file/live preview with bounded lifetime and auditable cleanup."""

from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import time
from urllib.parse import urlsplit, urlunsplit

from openframetap.display.session import (
    discover_active_wayland_session,
    gnome_overview_active,
    set_gnome_overview_active,
)
from openframetap.network.secrets import require_private_directory
from openframetap.video.latency import StartupTimeline, parse_latency_tracer
from openframetap.video.metrics import ProcessMetrics, summarize_metrics
from openframetap.video.pipelines import PipelineSpec
from openframetap.video.player_process import ProcessRegistry


def _linux_parent_death_signal() -> None:
    """Terminate the media child if the SSH-owned Python parent disappears."""

    if not sys.platform.startswith("linux"):
        return
    import ctypes
    import signal

    parent = os.getppid()
    libc = ctypes.CDLL(None)
    if libc.prctl(1, signal.SIGTERM) != 0:
        os._exit(127)
    if os.getppid() != parent:
        os.kill(os.getpid(), signal.SIGTERM)


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def checksum_manifest(paths: list[Path], root: Path) -> str:
    return "".join(
        f"{_sha256(path)}  {path.relative_to(root).as_posix()}\n" for path in paths
    )


def _redact(value: str) -> str:
    if not value.startswith(("rtmp://", "rtsp://")):
        return value
    parsed = urlsplit(value)
    parts = [part for part in parsed.path.split("/") if part]
    path = f"/{parts[0]}/<redacted>" if parts else "/<redacted>"
    return urlunsplit((parsed.scheme, parsed.netloc, path, "", ""))


def _redacted_argv(argv: tuple[str, ...]) -> list[str]:
    rendered = []
    for item in argv:
        if item.startswith("location="):
            rendered.append("location=" + _redact(item.split("=", 1)[1]))
        else:
            rendered.append(item)
    return rendered


def parse_fps_messages(text: str) -> dict:
    structured = re.findall(r"OPENFRAMETAP_PLAYER_STATS=(\{[^\n]+\})", text)
    if structured:
        try:
            payload = json.loads(structured[-1])
        except json.JSONDecodeError:
            pass
        else:
            return {
                "rendered_frames": payload.get("rendered_frames"),
                "dropped_frames": payload.get("dropped_frames"),
                "last_reported_fps": payload.get("last_reported_fps"),
                "maximum_reported_fps": payload.get("maximum_reported_fps"),
            }
    rendered = [int(value) for value in re.findall(r"rendered:\s*(\d+)", text)]
    dropped = [int(value) for value in re.findall(r"dropped:\s*(\d+)", text)]
    fps = [float(value) for value in re.findall(r"(?:current|fps):\s*([\d.]+)", text)]
    return {
        "rendered_frames": max(rendered) if rendered else None,
        "dropped_frames": max(dropped) if dropped else None,
        "last_reported_fps": fps[-1] if fps else None,
        "maximum_reported_fps": max(fps) if fps else None,
    }


def decode_error_lines(text: str) -> list[str]:
    return [
        line
        for line in text.splitlines()
        if not line.startswith("OPENFRAMETAP_PLAYER_")
        and re.search(
            r"\bERROR\b|not-negotiated|No valid frames|Error while opening decoder|decoder[^\n]*failed",
            line,
            re.IGNORECASE,
        )
    ]


def parse_frame_reports(text: str) -> list[dict]:
    reports = []
    for encoded in re.findall(r"OPENFRAMETAP_FRAME=(\{[^\n]+\})", text):
        try:
            reports.append(json.loads(encoded))
        except json.JSONDecodeError:
            continue
    if reports:
        return reports
    for line in text.splitlines():
        match = re.search(
            r"rendered:\s*(\d+),\s*dropped:\s*(\d+),\s*current:\s*([\d.]+)",
            line,
        )
        if match:
            reports.append(
                {
                    "rendered_frames": int(match.group(1)),
                    "dropped_frames": int(match.group(2)),
                    "current_fps": float(match.group(3)),
                }
            )
    return reports


def write_sanitized_preview_artifacts(directory: Path, payload: dict) -> list[Path]:
    directory.mkdir(parents=True, exist_ok=True)
    documents = {
        "decoder-summary.json": {
            "decoder": payload["pipeline"]["decoder"],
            "source": payload["pipeline"]["source_kind"],
            "decode_errors": payload["decode_errors"],
            "frames": payload["frames"],
        },
        "display-summary.json": {
            "session": payload["display_session"],
            "fullscreen": payload["fullscreen"],
            "screenshot": payload["screenshot"],
        },
        "performance-summary.json": payload,
        "latency-summary.json": {
            "startup_timeline": payload["startup_timeline"],
            "internal_latency": payload["latency"],
            "glass_to_glass_measured": False,
        },
    }
    paths = []
    for name, document in documents.items():
        path = directory / name
        path.write_text(json.dumps(document, indent=2) + "\n", encoding="utf-8")
        paths.append(path)
    report = directory / "report.md"
    report.write_text(
        "# Live preview evidence\n\n"
        f"- Hardware decoder element: `{payload['pipeline']['decoder']}`\n"
        f"- Rendered/dropped: {payload['frames']['rendered_frames']} / "
        f"{payload['frames']['dropped_frames']}\n"
        f"- Decode errors: {payload['decode_errors']}\n"
        f"- Fullscreen applied after surface: "
        f"{payload['fullscreen']['applied_after_surface']}\n"
        f"- Internal tracer average: {payload['latency'].get('average_ms')} ms\n"
        "- Glass-to-glass latency: not measured\n",
        encoding="utf-8",
    )
    paths.append(report)
    checksum = directory / "checksums.sha256"
    checksum.write_text(
        "".join(f"{_sha256(path)}  {path.name}\n" for path in paths),
        encoding="ascii",
    )
    return paths + [checksum]


def run_preview(
    spec: PipelineSpec,
    *,
    private_output: Path,
    sanitized_output: Path,
    duration_seconds: int,
    registry_path: Path = Path("runtime/media-processes.json"),
) -> dict:
    if not 1 <= duration_seconds <= 900:
        raise ValueError("preview duration must be 1..900 seconds")
    private_dir = require_private_directory(private_output)
    sanitized_dir = sanitized_output.resolve()
    if "sanitized" not in {part.lower() for part in sanitized_dir.parts}:
        raise ValueError("sanitized output must be under artifacts/sanitized")
    sanitized_dir.mkdir(parents=True, exist_ok=True)
    session = discover_active_wayland_session()
    env = session.environment()
    env["GST_TRACERS"] = os.environ.get("GST_TRACERS", "latency(flags=pipeline)")
    env["GST_DEBUG"] = os.environ.get(
        "GST_DEBUG", "2,fpsdisplaysink:6,GST_TRACER:7"
    )
    log_path = private_dir / "gst.log"
    metrics_path = private_dir / "metrics.jsonl"
    frames_path = private_dir / "frames.jsonl"
    pipeline_path = private_dir / "pipeline.json"
    timeline = StartupTimeline(command_started_ns=time.monotonic_ns())
    pipeline_path.write_text(
        json.dumps(spec.to_dict(), indent=2) + "\n", encoding="utf-8"
    )
    registry = ProcessRegistry(registry_path)
    samples = []
    timed_out = False
    interrupted = False
    screenshot_path = private_dir / "screenshots" / "wayland-preview.png"
    screenshot_attempted = False
    screenshot_error = None
    overview_initial = None
    overview_hidden = False
    overview_restored = False
    runtime_argv = [
        sys.executable,
        "-m",
        "openframetap.video.gst_player",
        "--pipeline-json",
        str(pipeline_path),
    ]
    with log_path.open("w", encoding="utf-8") as log:
        process = subprocess.Popen(
            runtime_argv,
            stdout=log,
            stderr=subprocess.STDOUT,
            text=True,
            env=env,
            preexec_fn=_linux_parent_death_signal if sys.platform.startswith("linux") else None,
        )
        timeline.process_started_ns = time.monotonic_ns()
        registry.register("preview", process, runtime_argv)
        sampler = ProcessMetrics(process.pid)
        deadline = time.monotonic() + duration_seconds
        try:
            if spec.sink == "wayland" and spec.fullscreen:
                overview_initial = gnome_overview_active(env)
                if overview_initial:
                    set_gnome_overview_active(False, env)
                    overview_hidden = True
            while process.poll() is None:
                samples.append(sampler.sample())
                if (
                    not screenshot_attempted
                    and time.monotonic() >= deadline - duration_seconds + 3.0
                ):
                    screenshot_attempted = True
                    screenshot_path.parent.mkdir(parents=True, exist_ok=True)
                    try:
                        shot = subprocess.run(
                            ["gnome-screenshot", "-f", str(screenshot_path)],
                            env=env,
                            text=True,
                            capture_output=True,
                            check=False,
                            timeout=10,
                        )
                        if shot.returncode != 0:
                            screenshot_error = shot.stderr.strip() or f"exit {shot.returncode}"
                    except (FileNotFoundError, subprocess.SubprocessError) as exc:
                        screenshot_error = str(exc)
                log.flush()
                if timeline.sink_caps_observed_ns is None:
                    try:
                        text = log_path.read_text(encoding="utf-8", errors="replace")
                    except OSError:
                        text = ""
                    if (
                        "OPENFRAMETAP_PLAYER_EVENT=" in text
                        and '"event": "first_frame_observed"' in text
                    ) or ("GstWaylandSink" in text and "caps = video/x-raw" in text):
                        timeline.sink_caps_observed_ns = time.monotonic_ns()
                if time.monotonic() >= deadline:
                    timed_out = True
                    process.terminate()
                    break
                time.sleep(0.25)
        except KeyboardInterrupt:
            interrupted = True
            process.terminate()
        finally:
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=3)
            registry.unregister("preview")
            if overview_initial is True:
                set_gnome_overview_active(True, env)
                overview_restored = True
    timeline.process_ended_ns = time.monotonic_ns()
    metrics_path.write_text(
        "".join(json.dumps(item.to_dict(), sort_keys=True) + "\n" for item in samples),
        encoding="utf-8",
    )
    log_text = log_path.read_text(encoding="utf-8", errors="replace")
    frame_stats = parse_fps_messages(log_text)
    frame_reports = parse_frame_reports(log_text)
    frames_path.write_text(
        "".join(json.dumps(item, sort_keys=True) + "\n" for item in frame_reports),
        encoding="utf-8",
    )
    fullscreen_events = []
    for encoded in re.findall(r"OPENFRAMETAP_PLAYER_EVENT=(\{[^\n]+\})", log_text):
        try:
            fullscreen_events.append(json.loads(encoded))
        except json.JSONDecodeError:
            continue
    errors = decode_error_lines(log_text)
    payload = {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "pipeline": {**spec.to_dict(), "argv": _redacted_argv(spec.argv)},
        "display_session": session.to_dict(),
        "exit_code": process.returncode,
        "timeout_reached": timed_out,
        "interrupted": interrupted,
        "decode_errors": len(errors),
        "frames": frame_stats,
        "fullscreen": {
            "requested": spec.fullscreen,
            "applied_after_surface": any(
                event.get("event") == "fullscreen_applied"
                and event.get("property_value") is True
                for event in fullscreen_events
            ),
            "gnome_overview_initially_active": overview_initial,
            "gnome_overview_hidden_for_preview": overview_hidden,
            "gnome_overview_restored": overview_restored,
        },
        "metrics": summarize_metrics(samples),
        "startup_timeline": timeline.to_dict(),
        "latency": parse_latency_tracer(log_text),
        "audio_output_enabled": False,
        "screenshot": {
            "attempted": screenshot_attempted,
            "saved": screenshot_path.is_file(),
            "private_file": "screenshots/wayland-preview.png" if screenshot_path.is_file() else None,
            "error": screenshot_error,
        },
        "ble_connection_status": "not_required_for_media_pull",
        "cleanup": {
            "preview_registry_empty": "preview" not in registry.load(),
            "owned_pid_stopped": process.poll() is not None,
        },
        "disconnects": {
            "wayland": len(re.findall(r"wayland[^\n]*(?:disconnect|broken pipe)", log_text, re.IGNORECASE)),
            "decoder_resets": len(re.findall(r"decoder[^\n]*reset", log_text, re.IGNORECASE)),
            "pipeline_restarts": 0,
            "client_reconnects": 0,
        },
        "late_frame_warnings": len(re.findall(r"Dropping frame due to QoS", log_text)),
    }
    if spec.source_kind in {"rtmp", "rtsp"}:
        try:
            from openframetap.video.rtmp_server import make_server

            media_status = make_server().status().to_dict()
        except Exception as exc:  # evidence only; preview result remains authoritative
            media_status = {"state": "unknown", "warning": str(exc)}
        payload["mediamtx_status"] = media_status
        (private_dir / "mediamtx-status.json").write_text(
            json.dumps(media_status, indent=2) + "\n", encoding="utf-8"
        )
    private_summary = private_dir / "summary.json"
    private_summary.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    write_sanitized_preview_artifacts(sanitized_dir, payload)
    checks = [pipeline_path, log_path, metrics_path, frames_path, private_summary]
    if (private_dir / "mediamtx-status.json").exists():
        checks.append(private_dir / "mediamtx-status.json")
    if screenshot_path.is_file():
        checks.append(screenshot_path)
    (private_dir / "checksums.sha256").write_text(
        checksum_manifest(checks, private_dir),
        encoding="ascii",
    )
    return payload
