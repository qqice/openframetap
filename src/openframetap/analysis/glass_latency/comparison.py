from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import shutil

from openframetap.analysis.glass_latency.statistics import latency_statistics
from openframetap.analysis.glass_latency.timing import PatternTimingMap
from openframetap.analysis.glass_latency.session import sanitized_peer


def _read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _read_jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def verify_manifest(directory: Path) -> dict:
    manifest = directory / "checksums.sha256"
    if not manifest.is_file():
        raise FileNotFoundError(f"checksum manifest missing: {manifest}")
    checked = 0
    for line in manifest.read_text(encoding="ascii").splitlines():
        if not line.strip():
            continue
        expected, relative = line.split("  ", 1)
        actual = _sha256(directory / relative)
        if actual != expected:
            raise RuntimeError(f"checksum mismatch: {directory / relative}")
        checked += 1
    return {"verified": True, "files_checked": checked}


def profile_metrics(
    analysis_dir: Path,
    *,
    warmup_seconds: float,
    preview_summary: Path | None,
) -> dict:
    integrity = verify_manifest(analysis_dir)
    summary = _read_json(analysis_dir / "summary.json")
    metadata = _read_json(analysis_dir / "input-metadata.json")
    rows = _read_jsonl(analysis_dir / "latency-samples.jsonl")
    invalid = _read_jsonl(analysis_dir / "invalid-samples.jsonl")
    steady_rows = [
        row for row in rows if float(row["phone_capture_timestamp"]) >= warmup_seconds
    ]
    steady_values = [float(row["glass_to_glass_latency_ms"]) for row in steady_rows]
    timestamps = [float(row["phone_capture_timestamp"]) for row in rows]
    raw_hash_before = metadata.get("input_sha256_before")
    raw_hash_after = metadata.get("input_sha256_after")
    if not raw_hash_before or raw_hash_before != raw_hash_after:
        raise RuntimeError("raw recording immutability evidence is missing or inconsistent")

    result = {
        "summary": summary,
        "steady_state": {
            "warmup_excluded_seconds": warmup_seconds,
            "valid_samples": len(steady_values),
            **latency_statistics(steady_values),
        },
        "accepted_time_coverage_seconds": {
            "first": min(timestamps) if timestamps else None,
            "last": max(timestamps) if timestamps else None,
            "span": max(timestamps) - min(timestamps) if timestamps else None,
        },
        "invalid_reason_counts": dict(Counter(row.get("reason") for row in invalid)),
        "raw_recording_sha256": raw_hash_before,
        "pattern_timing_sha256": _sha256(Path(metadata["pattern_log_file"])),
        "analysis_manifest": integrity,
    }
    if preview_summary:
        preview = _read_json(preview_summary)
        result["rock_runtime"] = {
            "preview_evidence_id": preview_summary.parent.name,
            "frames": preview.get("frames"),
            "metrics": preview.get("metrics"),
            "internal_latency": preview.get("latency"),
            "late_frame_warnings": preview.get("late_frame_warnings"),
            "disconnects": preview.get("disconnects"),
            "cleanup": preview.get("cleanup"),
        }
    return result


def confidence_sensitivity(analysis_dir: Path) -> dict:
    metadata = _read_json(analysis_dir / "input-metadata.json")
    decoded = _read_jsonl(analysis_dir / "decoded-frames.jsonl")
    timing = PatternTimingMap.load(Path(metadata["pattern_log_file"]), bits=16)
    output = {}
    for threshold in (0.10, 0.12, 0.15, 0.18):
        values: list[float] = []
        previous_source = None
        for row in decoded:
            if "source" not in row or "dsi" not in row:
                continue
            source_top, source_bottom = row["source"]["top"], row["source"]["bottom"]
            dsi_top, dsi_bottom = row["dsi"]["top"], row["dsi"]["bottom"]
            ids = (
                source_top.get("frame_id"),
                source_bottom.get("frame_id"),
                dsi_top.get("frame_id"),
                dsi_bottom.get("frame_id"),
            )
            if None in ids or ids[0] != ids[1] or ids[2] != ids[3]:
                continue
            confidence = min(
                float(source_top["confidence"]),
                float(source_bottom["confidence"]),
                float(dsi_top["confidence"]),
                float(dsi_bottom["confidence"]),
            )
            if confidence < threshold:
                continue
            try:
                source, _dsi, source_ns, dsi_ns = timing.resolve_pair(
                    int(ids[0]),
                    int(ids[2]),
                    previous_source_unwrapped=previous_source,
                    maximum_latency_ms=1000.0,
                )
            except ValueError:
                continue
            previous_source = source
            values.append((source_ns - dsi_ns) / 1_000_000.0)
        output[f"{threshold:.2f}"] = {
            "valid_samples": len(values),
            **latency_statistics(values),
        }
    return output


def _write_plots(output: Path, profiles: dict[str, dict], rows_by_profile: dict[str, list[dict]]) -> None:
    from PIL import Image, ImageDraw

    plots = output / "plots"
    plots.mkdir(parents=True)
    colors = {"low-latency": "#0066cc", "stable": "#cc6600", "aggressive": "#228833"}
    width, height, margin = 1200, 700, 80
    image = Image.new("RGB", (width, height), "white")
    draw = ImageDraw.Draw(image)
    draw.text((margin, 25), "Steady-state glass-to-glass latency ordered distributions", fill="black")
    draw.line((margin, height - margin, width - 30, height - margin), fill="black", width=2)
    draw.line((margin, 55, margin, height - margin), fill="black", width=2)
    all_values = [
        float(row["glass_to_glass_latency_ms"])
        for name, rows in rows_by_profile.items()
        for row in rows
        if float(row["phone_capture_timestamp"])
        >= profiles[name]["steady_state"]["warmup_excluded_seconds"]
    ]
    low, high = min(all_values), max(all_values)
    for line_index, (name, rows) in enumerate(rows_by_profile.items()):
        values = sorted(
            float(row["glass_to_glass_latency_ms"])
            for row in rows
            if float(row["phone_capture_timestamp"])
            >= profiles[name]["steady_state"]["warmup_excluded_seconds"]
        )
        points = [
            (
                margin + index * (width - margin - 40) / max(len(values) - 1, 1),
                height - margin - (value - low) * (height - 2 * margin) / max(high - low, 1e-9),
            )
            for index, value in enumerate(values)
        ]
        if len(points) > 1:
            draw.line(points, fill=colors[name], width=3)
        draw.text((width - 300, 65 + line_index * 25), name, fill=colors[name])
    image.save(plots / "profile-latency-cdf.png")


def _report(payload: dict) -> str:
    profiles = payload["profiles"]
    lines = [
        "# Pocket 3 glass-to-glass latency profile comparison",
        "",
        "## Measurement status",
        "",
        "- 【实机事实】 Three independent Pocket 4P 240 fps slow-motion captures were analyzed; each contains simultaneous Windows SOURCE and ROCK 4D DSI views.",
        "- 【实机事实】 Raw recordings were unchanged before/after analysis and all analysis manifests verified.",
        "- 【统计观察】 None of the three recordings reached the predeclared 90% raw-frame Gray Code decode gate. Results below are therefore exploratory distributions, not a method-valid final benchmark.",
        "- 【捕获推断】 Colour rolling-shutter bands from the two LCDs caused most rejected bits. No global latency offset was searched or applied.",
        "",
        "## Results",
        "",
        "| Profile | valid / total | decode | median | P95 | P99 | steady median | steady P95 |",
        "|---|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for name in ("low-latency", "stable", "aggressive"):
        item = profiles[name]
        raw, steady = item["summary"], item["steady_state"]
        lines.append(
            f"| {name} | {raw['valid_samples']} / {raw['phone_video_frames_analyzed']} | "
            f"{raw['decode_success_rate'] * 100:.2f}% | {raw['median_latency_ms']:.2f} ms | "
            f"{raw['p95_latency_ms']:.2f} ms | {raw['p99_latency_ms']:.2f} ms | "
            f"{steady['median_latency_ms']:.2f} ms | {steady['p95_latency_ms']:.2f} ms |"
        )
    lines.extend(
        [
            "",
            "The steady-state columns exclude the first 1.0 second uniformly; raw statistics remain the primary immutable outputs.",
            "",
            "## Interpretation",
            "",
            f"- 【统计观察】 Stable added {payload['comparisons']['stable_minus_low_median_ms']:.2f} ms to the raw median versus low-latency.",
            "- 【统计观察】 Aggressive had a near-one-second startup tail confined to the first 0.5 seconds; after the uniform warm-up exclusion its median stayed close to low-latency.",
            "- 【实机事实】 The paired ROCK runs recorded no decoder reset, Wayland disconnect, pipeline restart, or client reconnect.",
            "- 【捕获推断】 Stable's synchronized eight-buffer path is the source of its much larger glass latency; this agrees with its higher internal tracer latency.",
            "- 【待验证假设】 A re-recording with reduced LCD colour banding is required before the 90% method gate and a final evidence-based profile selection can be claimed.",
            "",
            "## Decision",
            "",
            "【统计观察】 Keep the existing low-latency default. This is a conservative retention of the already deployed default, not a declaration that the failed method gate has selected a final winner. Stable is clearly too latent; aggressive does not provide a meaningful median improvement and has startup-tail/runtime-drop evidence.",
            "",
        ]
    )
    return "\n".join(lines)


def build_comparison(
    analyses: dict[str, Path],
    previews: dict[str, Path],
    *,
    recording_metadata: Path,
    output: Path,
    warmup_seconds: float = 1.0,
) -> dict:
    if set(analyses) != {"low-latency", "stable", "aggressive"}:
        raise ValueError("comparison requires low-latency, stable, and aggressive analyses")
    if output.exists():
        raise FileExistsError(f"comparison output already exists: {output}")
    sanitized = sanitized_peer(output)
    if sanitized.exists():
        raise FileExistsError(f"sanitized comparison output already exists: {sanitized}")
    profiles = {
        name: profile_metrics(
            directory,
            warmup_seconds=warmup_seconds,
            preview_summary=previews.get(name),
        )
        for name, directory in analyses.items()
    }
    for name, directory in analyses.items():
        profiles[name]["confidence_sensitivity"] = confidence_sensitivity(directory)
    payload = {
        "schema_version": 1,
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "method_gate": {
            "required_raw_decode_success_rate": 0.90,
            "passed_all_profiles": all(
                item["summary"]["decode_success_rate"] >= 0.90
                for item in profiles.values()
            ),
        },
        "recording": _read_json(recording_metadata),
        "profiles": profiles,
        "comparisons": {
            "stable_minus_low_median_ms": (
                profiles["stable"]["summary"]["median_latency_ms"]
                - profiles["low-latency"]["summary"]["median_latency_ms"]
            ),
            "aggressive_minus_low_steady_median_ms": (
                profiles["aggressive"]["steady_state"]["median_latency_ms"]
                - profiles["low-latency"]["steady_state"]["median_latency_ms"]
            ),
        },
        "decision": {
            "current_default": "low-latency",
            "action": "retain-current-default-pending-method-valid-re-recording",
            "final_profile_selection_claimed": False,
        },
    }
    output.mkdir(parents=True)
    sanitized.mkdir(parents=True)
    (output / "comparison.json").write_text(
        json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    report = _report(payload)
    (output / "report.md").write_text(report, encoding="utf-8")
    rows = {name: _read_jsonl(path / "latency-samples.jsonl") for name, path in analyses.items()}
    _write_plots(output, profiles, rows)

    sanitized_payload = json.loads(json.dumps(payload))
    for item in sanitized_payload["profiles"].values():
        item.pop("raw_recording_sha256", None)
        item.pop("pattern_timing_sha256", None)
        runtime = item.get("rock_runtime")
        if runtime:
            runtime.pop("preview_evidence_id", None)
    (sanitized / "comparison.json").write_text(
        json.dumps(sanitized_payload, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    (sanitized / "report.md").write_text(report, encoding="utf-8")
    shutil.copytree(output / "plots", sanitized / "plots")
    for directory in (output, sanitized):
        files = sorted(path for path in directory.rglob("*") if path.is_file())
        with (directory / "checksums.sha256").open("w", encoding="ascii", newline="\n") as stream:
            for path in files:
                if path.name != "checksums.sha256":
                    stream.write(f"{_sha256(path)}  {path.relative_to(directory).as_posix()}\n")
    return payload


def _mapping(values: list[str]) -> dict[str, Path]:
    output = {}
    for value in values:
        name, separator, path = value.partition("=")
        if not separator:
            raise ValueError("profile mapping must be profile=path")
        output[name] = Path(path)
    return output


def main() -> int:
    parser = argparse.ArgumentParser(description="Compare three glass-latency analyses")
    parser.add_argument("--analysis", action="append", required=True)
    parser.add_argument("--preview-summary", action="append", default=[])
    parser.add_argument("--recording-metadata", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--warmup-seconds", type=float, default=1.0)
    args = parser.parse_args()
    payload = build_comparison(
        _mapping(args.analysis),
        _mapping(args.preview_summary),
        recording_metadata=args.recording_metadata,
        output=args.output,
        warmup_seconds=args.warmup_seconds,
    )
    print(json.dumps(payload["decision"], indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
