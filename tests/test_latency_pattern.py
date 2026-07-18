from __future__ import annotations

import json
from pathlib import Path

import pytest

from openframetap.tools.latency_pattern import (
    binary_to_gray,
    finalize_pattern_evidence,
    gray_bit_values,
    gray_to_binary,
    pattern_layout,
    pattern_state,
)


@pytest.mark.parametrize("value", [0, 1, 2, 3, 15, 4095, 32768, 65535])
def test_binary_gray_round_trip(value: int) -> None:
    assert gray_to_binary(binary_to_gray(value)) == value


def test_gray_bit_order_is_msb_first_and_bounded() -> None:
    bits = gray_bit_values(3, 12)
    assert len(bits) == 12
    reconstructed = 0
    for bit in bits:
        reconstructed = (reconstructed << 1) | bit
    assert reconstructed == binary_to_gray(3)


def test_pattern_layout_has_two_complete_banks_and_large_cells() -> None:
    layout = pattern_layout(16)
    assert len(layout.top_bits) == len(layout.bottom_bits) == 16
    assert all(rect.y1 - rect.y0 > 0.25 for rect in layout.top_bits)
    assert layout.top_bits[-1].x1 < layout.top_black_reference.x0
    assert layout.bottom_bits[0].y0 > layout.top_bits[0].y1


def test_pattern_state_wraps_at_selected_width() -> None:
    state = pattern_state(65536, 123_000_000, bits=16)
    assert state.frame_id == 0
    assert state.gray_code == 0


def test_ctrl_c_finalization_keeps_timing_and_marks_incomplete(tmp_path: Path) -> None:
    timing = tmp_path / "pattern-timing.jsonl"
    config = tmp_path / "pattern-config.json"
    runtime = tmp_path / "runtime.log"
    timing.write_text('{"frame_id": 0, "monotonic_ns": 1}\n', encoding="utf-8")
    runtime.write_text("started\n", encoding="utf-8")
    payload = finalize_pattern_evidence(
        tmp_path,
        timing_path=timing,
        config_path=config,
        runtime_path=runtime,
        config={"actual_present_timestamp_available": False},
        records=1,
        stopped_by="ctrl-c",
        screenshot_path=tmp_path / "screenshot.png",
        screenshot_error="not captured",
    )
    assert payload["completed"] is False
    assert payload["records_written"] == 1
    assert timing.read_text(encoding="utf-8").strip()
    assert json.loads(config.read_text(encoding="utf-8"))["stopped_by"] == "ctrl-c"
    assert "pattern-timing.jsonl" in (tmp_path / "checksums.sha256").read_text()
