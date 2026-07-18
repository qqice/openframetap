"""Foreground file/live preview with bounded lifetime and auditable cleanup."""

from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import time
from urllib.parse import urlsplit, urlunsplit

from openframetap.display.session import discover_active_wayland_session
from openframetap.network.secrets import require_private_directory
from openframetap.video.latency import StartupTimeline, pipeline_latency_statement
from openframetap.video.metrics import ProcessMetrics, summarize_metrics
from openframetap.video.pipelines import PipelineSpec
from openframetap.video.player_process import ProcessRegistry


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


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
    rendered = [int(value) for value in re.findall(r"rendered:\s*(\d+)", text)]
    dropped = [int(value) for value in re.findall(r"dropped:\s*(\d+)", text)]
    fps = [float(value) for value in re.findall(r"(?:current|fps):\s*([\d.]+)", text)]
    return {
        "rendered_frames": max(rendered) if rendered else None,
        "dropped_frames": max(dropped) if dropped else None,
        "last_reported_fps": fps[-1] if fps else None,
        "maximum_reported_fps": max(fps) if fps else None,
    }


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
    env["GST_DEBUG"] = os.environ.get("GST_DEBUG", "2,fpsdisplaysink:6")
    log_path = private_dir / "gst.log"
    metrics_path = private_dir / "metrics.jsonl"
    pipeline_path = private_dir / "pipeline.json"
    timeline = StartupTimeline(command_started_ns=time.monotonic_ns())
    pipeline_path.write_text(
        json.dumps(spec.to_dict(), indent=2) + "\n", encoding="utf-8"
    )
    registry = ProcessRegistry(registry_path)
    samples = []
    timed_out = False
    interrupted = False
    with log_path.open("w", encoding="utf-8") as log:
        process = subprocess.Popen(
            list(spec.argv), stdout=log, stderr=subprocess.STDOUT, text=True, env=env
        )
        timeline.process_started_ns = time.monotonic_ns()
        registry.register("preview", process, list(spec.argv))
        sampler = ProcessMetrics(process.pid)
        deadline = time.monotonic() + duration_seconds
        try:
            while process.poll() is None:
                samples.append(sampler.sample())
                log.flush()
                if timeline.sink_caps_observed_ns is None:
                    try:
                        text = log_path.read_text(encoding="utf-8", errors="replace")
                    except OSError:
                        text = ""
                    if "GstWaylandSink" in text and "caps = video/x-raw" in text:
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
    timeline.process_ended_ns = time.monotonic_ns()
    metrics_path.write_text(
        "".join(json.dumps(item.to_dict(), sort_keys=True) + "\n" for item in samples),
        encoding="utf-8",
    )
    log_text = log_path.read_text(encoding="utf-8", errors="replace")
    frame_stats = parse_fps_messages(log_text)
    errors = [
        line
        for line in log_text.splitlines()
        if re.search(r"\b(error|not-negotiated|failed)\b", line, re.IGNORECASE)
    ]
    payload = {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "pipeline": {**spec.to_dict(), "argv": _redacted_argv(spec.argv)},
        "display_session": session.to_dict(),
        "exit_code": process.returncode,
        "timeout_reached": timed_out,
        "interrupted": interrupted,
        "decode_errors": len(errors),
        "frames": frame_stats,
        "metrics": summarize_metrics(samples),
        "startup_timeline": timeline.to_dict(),
        "latency": pipeline_latency_statement(),
        "audio_output_enabled": False,
        "ble_connection_status": "not_required_for_media_pull",
        "cleanup": {
            "preview_registry_empty": "preview" not in registry.load(),
            "owned_pid_stopped": process.poll() is not None,
        },
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
    sanitized_summary = sanitized_dir / "performance-summary.json"
    sanitized_summary.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    checks = [pipeline_path, log_path, metrics_path, private_summary]
    if (private_dir / "mediamtx-status.json").exists():
        checks.append(private_dir / "mediamtx-status.json")
    (private_dir / "checksums.sha256").write_text(
        "".join(f"{_sha256(path)}  {path.name}\n" for path in checks), encoding="ascii"
    )
    return payload
