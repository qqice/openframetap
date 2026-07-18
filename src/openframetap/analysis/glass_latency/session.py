from __future__ import annotations

import csv
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import shutil

from openframetap.analysis.glass_latency.decoder import decode_pattern, rectify_roi
from openframetap.analysis.glass_latency.models import ROI, ROIConfig
from openframetap.analysis.glass_latency.plots import write_latency_plots
from openframetap.analysis.glass_latency.statistics import latency_statistics, run_length_encode
from openframetap.analysis.glass_latency.timing import PatternTimingMap
from openframetap.analysis.glass_latency.video import (
    first_video_frame,
    probe_video,
    select_rois_interactively,
)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _json(path: Path, payload: object) -> None:
    with path.open("w", encoding="utf-8", newline="\n") as stream:
        json.dump(payload, stream, indent=2, ensure_ascii=False)
        stream.write("\n")


def _jsonl(path: Path, records: list[dict]) -> None:
    with path.open("w", encoding="utf-8", newline="\n") as stream:
        for record in records:
            stream.write(json.dumps(record, sort_keys=True) + "\n")


def _manifest(directory: Path) -> None:
    paths = sorted(
        path
        for path in directory.rglob("*")
        if path.is_file() and path.name != "checksums.sha256"
    )
    with (directory / "checksums.sha256").open(
        "w", encoding="ascii", newline="\n"
    ) as stream:
        for path in paths:
            stream.write(f"{_sha256(path)}  {path.relative_to(directory).as_posix()}\n")


def default_output() -> tuple[Path, Path]:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    name = f"glass-latency-{stamp}"
    return Path("artifacts/private") / name, Path("artifacts/sanitized") / name


def sanitized_peer(path: Path) -> Path:
    parts = list(path.parts)
    for index, part in enumerate(parts):
        if part.lower() == "private":
            parts[index] = "sanitized"
            return Path(*parts)
    raise ValueError("glass-latency output must be under a private artifacts directory")


def _profile_environment(profile: str) -> dict:
    values = {
        "stable": {"queue_buffers": 8, "leaky": "no", "sync": True},
        "low-latency": {"queue_buffers": 3, "leaky": "downstream", "sync": False},
        "aggressive-low-latency": {
            "queue_buffers": 1,
            "leaky": "downstream",
            "sync": False,
        },
    }
    if profile not in values:
        raise ValueError("unknown pipeline profile")
    return values[profile]


def validate_phone_fps(value: float | None) -> float:
    if value is None or not 1 <= value <= 1000:
        raise ValueError("phone fps metadata is missing or invalid")
    return float(value)


def analyze_glass_latency(
    video_path: Path,
    *,
    pattern_log: Path,
    source_roi: ROI | None,
    dsi_roi: ROI | None,
    interactive_roi: bool,
    phone_fps: float | None,
    output: Path | None,
    source_transform: tuple[tuple[float, float], ...] | None,
    dsi_transform: tuple[tuple[float, float], ...] | None,
    maximum_latency_ms: float,
    bits: int = 16,
    invert: bool = False,
    pipeline_profile: str = "low-latency",
    phone_model: str | None = None,
    ambient_notes: str | None = None,
) -> dict:
    import cv2

    if not 1 <= maximum_latency_ms <= 5000:
        raise ValueError("max latency must be 1..5000 ms")
    if not 12 <= bits <= 16:
        raise ValueError("bits must be 12..16")
    profile = _profile_environment(pipeline_profile)
    video_hash_before = _sha256(video_path)
    metadata, timestamps = probe_video(video_path)
    effective_fps = validate_phone_fps(
        phone_fps or metadata.get("average_fps") or metadata.get("nominal_fps")
    )
    first_frame = first_video_frame(video_path)
    if interactive_roi:
        source_values, dsi_values = select_rois_interactively(first_frame)
        source_roi, dsi_roi = ROI(*source_values), ROI(*dsi_values)
    if source_roi is None or dsi_roi is None:
        raise ValueError("provide both ROIs or use --interactive-roi")
    roi_config = ROIConfig(source_roi, dsi_roi, source_transform, dsi_transform)
    timing = PatternTimingMap.load(pattern_log, bits=bits)
    private_dir, sanitized_dir = default_output()
    if output:
        private_dir = output
        sanitized_dir = sanitized_peer(private_dir)
    private_dir.mkdir(parents=True, exist_ok=False)
    sanitized_dir.mkdir(parents=True, exist_ok=False)
    (private_dir / "plots").mkdir()
    (sanitized_dir / "plots").mkdir()
    input_metadata = {
        **metadata,
        "input_file": str(video_path.resolve()),
        "input_sha256_before": video_hash_before,
        "phone_fps_used": effective_fps,
        "phone_model": phone_model,
        "ambient_lighting_notes": ambient_notes,
        "pattern_log_file": str(pattern_log.resolve()),
        "pattern_refresh_hz": timing.refresh_hz,
        "pattern_timestamp_kind": "application_submission_monotonic_ns",
        "actual_pattern_present_timestamp_available": False,
        "pocket_stream": "H.264 High 1280x720 approximately 29.97 fps",
        "pipeline_profile": pipeline_profile,
        "decoder": "mppvideodec",
        "queue_buffers": profile["queue_buffers"],
        "queue_leaky": profile["leaky"],
        "sink_sync": profile["sync"],
        "media_path": "direct local RTMP via MediaMTX",
    }
    _json(private_dir / "input-metadata.json", input_metadata)
    _json(private_dir / "roi-config.json", roi_config.to_dict())

    capture = cv2.VideoCapture(str(video_path))
    decoded_records: list[dict] = []
    invalid_records: list[dict] = []
    mixed_records: list[dict] = []
    latency_records: list[dict] = []
    previous_source_absolute = None
    index = 0
    while index < len(timestamps):
        ok, frame = capture.read()
        if not ok or frame is None:
            break
        source_image = rectify_roi(
            frame,
            source_roi,
            source_transform,
            width=roi_config.rectified_width,
            height=roi_config.rectified_height,
        )
        dsi_image = rectify_roi(
            frame,
            dsi_roi,
            dsi_transform,
            width=roi_config.rectified_width,
            height=roi_config.rectified_height,
        )
        source = decode_pattern(source_image, bits=bits, invert=invert)
        dsi = decode_pattern(dsi_image, bits=bits, invert=invert)
        base = {
            "phone_frame_index": index,
            "phone_video_timestamp": timestamps[index],
            "source": source.to_dict(),
            "dsi": dsi.to_dict(),
        }
        decoded_records.append(base)
        if source.mixed_refresh or dsi.mixed_refresh:
            mixed = {
                **base,
                "reason": "source_mixed_refresh" if source.mixed_refresh else "dsi_mixed_refresh",
            }
            mixed_records.append(mixed)
            index += 1
            continue
        if not source.valid or not dsi.valid:
            invalid_records.append(
                {
                    **base,
                    "reason": source.reason if not source.valid else dsi.reason,
                }
            )
            index += 1
            continue
        try:
            source_absolute, dsi_absolute, source_ns, dsi_ns = timing.resolve_pair(
                int(source.frame_id),
                int(dsi.frame_id),
                previous_source_unwrapped=previous_source_absolute,
                maximum_latency_ms=maximum_latency_ms,
            )
        except ValueError as exc:
            invalid_records.append({**base, "reason": str(exc)})
            index += 1
            continue
        previous_source_absolute = source_absolute
        latency_ms = (source_ns - dsi_ns) / 1_000_000.0
        latency_records.append(
            {
                "phone_frame_index": index,
                "phone_video_timestamp": timestamps[index],
                "source_frame_id": source.frame_id,
                "displayed_frame_id": dsi.frame_id,
                "source_unwrapped_frame_id": source_absolute,
                "displayed_unwrapped_frame_id": dsi_absolute,
                "source_timestamp_ns": source_ns,
                "displayed_source_timestamp_ns": dsi_ns,
                "glass_to_glass_latency_ms": latency_ms,
                "source_confidence": source.confidence,
                "dsi_confidence": dsi.confidence,
            }
        )
        index += 1
    capture.release()
    if index == 0:
        raise ValueError("phone video could not be decoded")

    video_hash_after = _sha256(video_path)
    if video_hash_after != video_hash_before:
        raise RuntimeError("phone video changed during analysis")
    input_metadata["input_sha256_after"] = video_hash_after
    input_metadata["input_immutable"] = True
    input_metadata["decoded_phone_frames"] = index
    _json(private_dir / "input-metadata.json", input_metadata)
    _jsonl(private_dir / "decoded-frames.jsonl", decoded_records)
    _jsonl(private_dir / "invalid-samples.jsonl", invalid_records)
    _jsonl(private_dir / "mixed-refresh.jsonl", mixed_records)
    _jsonl(private_dir / "latency-samples.jsonl", latency_records)
    with (private_dir / "latency-samples.csv").open(
        "w", encoding="utf-8", newline=""
    ) as stream:
        fields = list(latency_records[0]) if latency_records else [
            "phone_frame_index",
            "phone_video_timestamp",
            "source_frame_id",
            "displayed_frame_id",
            "source_timestamp_ns",
            "displayed_source_timestamp_ns",
            "glass_to_glass_latency_ms",
        ]
        writer = csv.DictWriter(stream, fieldnames=fields)
        writer.writeheader()
        writer.writerows(latency_records)

    values = [record["glass_to_glass_latency_ms"] for record in latency_records]
    source_ids = [int(record["source_unwrapped_frame_id"]) for record in latency_records]
    dsi_ids = [int(record["displayed_unwrapped_frame_id"]) for record in latency_records]
    runs = run_length_encode(dsi_ids)
    transitions = [run["frame_id"] for run in runs]
    seen = set()
    duplicate_dsi_frames = 0
    for value in transitions:
        if value in seen:
            duplicate_dsi_frames += 1
        seen.add(value)
    skipped = sum(
        max(0, right - left - 1) for left, right in zip(transitions, transitions[1:])
    )
    total = len(decoded_records)
    summary = {
        "schema_version": 1,
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "pipeline_profile": pipeline_profile,
        "decoder": "mppvideodec",
        "queue_buffers": profile["queue_buffers"],
        "queue_leaky": profile["leaky"],
        "sink_sync": profile["sync"],
        "phone_video_frames_analyzed": total,
        "valid_samples": len(latency_records),
        "invalid_samples": len(invalid_records),
        "mixed_refresh_samples": len(mixed_records),
        "decode_success_rate": len(latency_records) / total if total else 0.0,
        "unique_dsi_frame_runs": len(runs),
        "phone_resample_repetitions": sum(max(0, run["length"] - 1) for run in runs),
        "duplicate_dsi_frames": duplicate_dsi_frames,
        "skipped_source_frames": skipped,
        "skipped_source_frames_note": (
            "This includes normal source-pattern IDs skipped by approximately 30 fps camera "
            "sampling and is not automatically a decoder drop count."
        ),
        "method_validated": bool(
            total and len(latency_records) / total >= 0.90 and len(runs) >= 100
        ),
        "method_validation_requirements": {
            "decode_success_rate_at_least": 0.90,
            "unique_dsi_frame_runs_at_least": 100,
        },
        "pattern_refresh_hz": timing.refresh_hz,
        "pattern_actual_present_timestamp_available": False,
        "timing_uncertainty_note": (
            "Pattern timing uses Windows application submission timestamps, not measured scanout."
        ),
        "raw_video_immutable": True,
        **latency_statistics(values),
    }
    _json(private_dir / "summary.json", summary)
    confidences = [
        min(record["source_confidence"], record["dsi_confidence"])
        for record in latency_records
    ]
    plots = write_latency_plots(
        private_dir / "plots",
        latencies=values,
        source_ids=source_ids,
        dsi_ids=dsi_ids,
        run_lengths=[run["length"] for run in runs],
        confidences=confidences,
    )
    report = private_dir / "report.md"
    report.write_text(_report_text(summary), encoding="utf-8", newline="\n")
    _manifest(private_dir)

    sanitized_summary = dict(summary)
    _json(sanitized_dir / "summary.json", sanitized_summary)
    (sanitized_dir / "report.md").write_text(
        _report_text(sanitized_summary), encoding="utf-8", newline="\n"
    )
    for plot in plots:
        shutil.copy2(plot, sanitized_dir / "plots" / plot.name)
    _manifest(sanitized_dir)
    return {
        "private_output": str(private_dir),
        "sanitized_output": str(sanitized_dir),
        "summary": summary,
    }


def _format(value: object) -> str:
    return "n/a" if value is None else f"{float(value):.3f}"


def _report_text(summary: dict) -> str:
    validity = "validated" if summary["method_validated"] else "not yet validated"
    return (
        "# Glass-to-glass latency report\n\n"
        f"Method status: **{validity}**\n\n"
        f"- Pipeline: `{summary['pipeline_profile']}` / `{summary['decoder']}` / "
        f"queue {summary['queue_buffers']} / sync={summary['sink_sync']}\n"
        f"- Valid samples: {summary['valid_samples']}\n"
        f"- Decode success: {summary['decode_success_rate'] * 100:.2f}%\n"
        f"- Mixed refresh: {summary['mixed_refresh_samples']}\n"
        f"- Median ± MAD: {_format(summary['median_latency_ms'])} ± "
        f"{_format(summary['median_absolute_deviation_ms'])} ms\n"
        f"- P90/P95/P99: {_format(summary['p90_latency_ms'])} / "
        f"{_format(summary['p95_latency_ms'])} / {_format(summary['p99_latency_ms'])} ms\n"
        f"- Min/max: {_format(summary['minimum_latency_ms'])} / "
        f"{_format(summary['maximum_latency_ms'])} ms\n\n"
        "Pattern timestamps are application submission times, not measured scanout. "
        "These results are distinct from GStreamer internal source-to-sink tracer latency.\n"
    )
