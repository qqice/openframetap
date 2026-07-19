from __future__ import annotations

import hashlib
import json
from pathlib import Path

from openframetap.analysis.glass_latency.comparison import (
    profile_metrics,
    verify_manifest,
)


def _write_json(path: Path, payload: dict) -> None:
    path.write_text(json.dumps(payload) + "\n", encoding="utf-8")


def _manifest(directory: Path) -> None:
    files = sorted(path for path in directory.iterdir() if path.name != "checksums.sha256")
    with (directory / "checksums.sha256").open("w", encoding="ascii") as stream:
        for path in files:
            stream.write(f"{hashlib.sha256(path.read_bytes()).hexdigest()}  {path.name}\n")


def test_comparison_verifies_inputs_and_computes_uniform_warmup(tmp_path: Path) -> None:
    directory = tmp_path / "analysis"
    directory.mkdir()
    _write_json(
        directory / "summary.json",
        {"pipeline_profile": "low-latency", "valid_samples": 3},
    )
    _write_json(
        directory / "input-metadata.json",
        {
            "input_sha256_before": "abc",
            "input_sha256_after": "abc",
            "pattern_log_file": str(directory / "pattern.jsonl"),
        },
    )
    (directory / "pattern.jsonl").write_text("fixture", encoding="utf-8")
    (directory / "latency-samples.jsonl").write_text(
        "\n".join(
            json.dumps(
                {
                    "phone_capture_timestamp": timestamp,
                    "glass_to_glass_latency_ms": latency,
                }
            )
            for timestamp, latency in ((0.1, 900.0), (1.0, 100.0), (2.0, 120.0))
        )
        + "\n",
        encoding="utf-8",
    )
    (directory / "invalid-samples.jsonl").write_text(
        json.dumps({"reason": "ambiguous_bit_luminance"}) + "\n",
        encoding="utf-8",
    )
    _manifest(directory)
    metrics = profile_metrics(
        directory, warmup_seconds=1.0, preview_summary=None
    )
    assert metrics["steady_state"]["valid_samples"] == 2
    assert metrics["steady_state"]["median_latency_ms"] == 110.0
    assert metrics["invalid_reason_counts"] == {"ambiguous_bit_luminance": 1}
    assert verify_manifest(directory)["verified"]


def test_manifest_mismatch_fails_closed(tmp_path: Path) -> None:
    directory = tmp_path / "analysis"
    directory.mkdir()
    (directory / "sample.txt").write_text("before", encoding="utf-8")
    _manifest(directory)
    (directory / "sample.txt").write_text("after", encoding="utf-8")
    try:
        verify_manifest(directory)
    except RuntimeError as exc:
        assert "checksum mismatch" in str(exc)
    else:
        raise AssertionError("modified evidence was accepted")
