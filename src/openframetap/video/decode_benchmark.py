"""Offline H.264 decode benchmarks with explicit decoder selection."""

from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import time

from openframetap.video.decoder_probe import discover_decoders
from openframetap.video.metrics import ProcessMetrics, summarize_metrics
from openframetap.video.pipelines import PipelineProfile, offline_pipeline


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _decoded_frames(text: str) -> int | None:
    values = [
        int(value)
        for pattern in (r"rendered:\s*(\d+)", r"frame=\s*(\d+)")
        for value in re.findall(pattern, text)
    ]
    return max(values) if values else None


def _run_measured(
    argv: list[str], *, log_path: Path, metrics_path: Path, timeout: float, env: dict[str, str] | None = None
) -> dict:
    started = time.monotonic()
    timed_out = False
    samples = []
    with log_path.open("w", encoding="utf-8") as log:
        process = subprocess.Popen(
            argv,
            stdout=log,
            stderr=subprocess.STDOUT,
            text=True,
            env=env,
        )
        sampler = ProcessMetrics(process.pid)
        while process.poll() is None:
            samples.append(sampler.sample())
            if time.monotonic() - started > timeout:
                timed_out = True
                process.terminate()
                try:
                    process.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=3)
                break
            time.sleep(0.2)
        exit_code = process.wait()
    wall_time = time.monotonic() - started
    metrics_path.write_text(
        "".join(json.dumps(item.to_dict(), sort_keys=True) + "\n" for item in samples),
        encoding="utf-8",
    )
    log_text = log_path.read_text(encoding="utf-8", errors="replace")
    frames = _decoded_frames(log_text)
    metrics = summarize_metrics(samples)
    return {
        "argv": argv,
        "exit_code": exit_code,
        "timed_out": timed_out,
        "wall_time_seconds": wall_time,
        "decoded_frames": frames,
        "frames_per_second": frames / wall_time if frames is not None and wall_time else None,
        "decode_errors": len(
            [
                line
                for line in log_text.splitlines()
                if re.search(r"\b(error|failed|not-negotiated)\b", line, re.IGNORECASE)
            ]
        ),
        **metrics,
    }


def _hardware_evidence(decoder: str, log_text: str, argv: list[str]) -> dict:
    explicit = decoder in argv and "decodebin" not in argv
    plugin_instantiated = decoder in log_text or "GstMppVideoDec" in log_text
    return {
        "explicit_decoder_element": explicit,
        "plugin_instantiated_in_log": plugin_instantiated,
        "software_fallback_element_present": any(
            item in log_text for item in ("avdec_h264", "GstFFMpegVidDec")
        ),
        "mpp_device_present": Path("/dev/mpp_service").exists(),
        "confirmed": decoder == "mppvideodec" and explicit and plugin_instantiated,
    }


def run_decode_benchmark(input_path: Path, output_dir: Path) -> dict:
    if not input_path.is_file() or input_path.stat().st_size == 0:
        raise FileNotFoundError(f"benchmark input is missing: {input_path}")
    output_dir.mkdir(parents=True, exist_ok=True)
    capabilities = discover_decoders()
    available = {
        item.decoder_name: item for item in capabilities if item.framework == "gstreamer" and item.available
    }
    results = []
    candidates = ["mppvideodec", "v4l2h264dec", "v4l2slh264dec", "avdec_h264"]
    for decoder in candidates:
        capability = next(
            (item for item in capabilities if item.framework == "gstreamer" and item.decoder_name == decoder),
            None,
        )
        if decoder not in available:
            results.append(
                {
                    "decoder": decoder,
                    "framework": "gstreamer",
                    "hardware_or_software": capability.hardware_or_software if capability else "unknown",
                    "test_result": "unavailable",
                    "failure_reason": capability.failure_reason if capability else "not discovered",
                }
            )
            continue
        spec = offline_pipeline(
            input_path,
            decoder=decoder,
            sink="fakesink",
            profile=PipelineProfile.STABLE,
        )
        argv = list(spec.argv)
        argv.insert(2, "-v")
        log_path = output_dir / f"gst-{decoder}.log"
        metrics_path = output_dir / f"gst-{decoder}-metrics.jsonl"
        measured = _run_measured(argv, log_path=log_path, metrics_path=metrics_path, timeout=30)
        evidence = _hardware_evidence(decoder, log_path.read_text(errors="replace"), argv)
        result = {
            "decoder": decoder,
            "framework": "gstreamer",
            "hardware_or_software": capability.hardware_or_software,
            "pipeline": spec.to_dict(),
            **measured,
            "hardware_evidence": evidence,
        }
        result["test_result"] = (
            "passed"
            if measured["exit_code"] == 0
            and measured["decode_errors"] == 0
            and (capability.hardware_or_software != "hardware" or evidence["confirmed"])
            else "failed"
        )
        results.append(result)

    ffmpeg = shutil.which("ffmpeg")
    ffmpeg_capabilities = {
        item.decoder_name: item
        for item in capabilities
        if item.framework == "ffmpeg" and item.available
    }
    if ffmpeg:
        for decoder, kind in (
            ("h264", "software"),
            ("h264_rkmpp", "hardware"),
            ("h264_v4l2m2m", "hardware"),
        ):
            if decoder not in ffmpeg_capabilities:
                results.append(
                    {
                        "decoder": decoder,
                        "framework": "ffmpeg",
                        "hardware_or_software": kind,
                        "test_result": "unavailable",
                        "failure_reason": "decoder unavailable",
                    }
                )
                continue
            argv = [
                ffmpeg,
                "-hide_banner",
                "-benchmark",
                "-v",
                "info",
                "-c:v",
                decoder,
                "-i",
                str(input_path),
                "-map",
                "0:v:0",
                "-an",
                "-progress",
                "pipe:1",
                "-nostats",
                "-f",
                "null",
                "-",
            ]
            log_path = output_dir / f"ffmpeg-{decoder}.log"
            measured = _run_measured(
                argv,
                log_path=log_path,
                metrics_path=output_dir / f"ffmpeg-{decoder}-metrics.jsonl",
                timeout=30,
            )
            log_text = log_path.read_text(encoding="utf-8", errors="replace")
            hardware_evidence = {
                "explicit_decoder": decoder in argv,
                "decoder_named_in_log": decoder in log_text,
                "software_fallback_named": kind == "hardware" and "h264 (native)" in log_text,
                "mpp_device_present": Path("/dev/mpp_service").exists(),
                "confirmed": (
                    kind == "hardware"
                    and decoder in argv
                    and decoder in log_text
                    and measured["exit_code"] == 0
                    and (measured["decoded_frames"] or 0) > 0
                    and "h264 (native)" not in log_text
                ),
            }
            passed = (
                measured["exit_code"] == 0
                and (measured["decoded_frames"] or 0) > 0
                and (kind != "hardware" or hardware_evidence["confirmed"])
            )
            results.append(
                {
                    "decoder": decoder,
                    "framework": "ffmpeg",
                    "hardware_or_software": kind,
                    **measured,
                    "test_result": "passed" if passed else "failed",
                    "hardware_evidence": hardware_evidence,
                }
            )
    else:
        results.append(
            {
                "decoder": "h264",
                "framework": "ffmpeg",
                "hardware_or_software": "software",
                "test_result": "unavailable",
                "failure_reason": "ffmpeg unavailable",
            }
        )
    payload = {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "input_name": input_path.name,
        "input_sha256": _sha256(input_path),
        "results": results,
    }
    (output_dir / "summary.json").write_text(json.dumps(payload, indent=2) + "\n")
    checksum_lines = []
    for path in sorted(item for item in output_dir.iterdir() if item.is_file()):
        if path.name != "checksums.sha256":
            checksum_lines.append(f"{_sha256(path)}  {path.name}\n")
    (output_dir / "checksums.sha256").write_text("".join(checksum_lines), encoding="ascii")
    return payload
