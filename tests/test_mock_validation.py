from __future__ import annotations

import json
from pathlib import Path

from openframetap.control.mock_validation import run_mock_validation


def test_mock_failure_matrix_is_zero_and_has_checksums(tmp_path: Path) -> None:
    output = tmp_path / "artifacts" / "private" / "mock-control"
    summary = run_mock_validation(output, software_git_head="fixture")
    assert summary["scenario_count"] == 11
    assert summary["all_final_output_zero"] is True
    assert summary["fff5_write_count"] == 0
    assert summary["fail_closed_scenarios"] == ["send_failure"]
    records = [json.loads(line) for line in (output / "mock-validation.jsonl").read_text().splitlines()]
    assert all(item["final_output_zero"] for item in records)
    assert (output / "checksums.sha256").is_file()

