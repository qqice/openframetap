from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from openframetap.analysis.alignment import nearest_neighbor_alignment
from openframetap.analysis.correlation import (
    event_field_metrics,
    first_difference,
    pearson_correlation,
    population_standard_deviation,
    spearman_correlation,
    wraparound_candidate,
)
from openframetap.analysis.field_candidates import (
    decode_field,
    generate_field_candidates,
)
from openframetap.analysis.report import analyze_experiment, verify_raw_checksums
from openframetap.analysis.state_model import Pocket3State
from openframetap.telemetry.schemas import ObservationProvenance


def _jsonl(path: Path, records: list[dict]) -> None:
    path.write_text(
        "".join(json.dumps(record, sort_keys=True) + "\n" for record in records),
        encoding="utf-8",
    )


def _frame(timestamp: int, command: str, payload: bytes) -> dict:
    cmd_set, cmd_id = (int(part, 16) for part in command.split("/"))
    return {
        "wall_time_utc": "2026-07-18T00:00:00+00:00",
        "monotonic_ns": timestamp,
        "cmd_set": f"0x{cmd_set:02X}",
        "cmd_id": f"0x{cmd_id:02X}",
        "payload_hex": payload.hex(),
    }


def _event(base: int, seconds: float, name: str, phase: str, value=None) -> dict:
    return {
        "wall_time_utc": "2026-07-18T00:00:00+00:00",
        "monotonic_ns": base + int(seconds * 1_000_000_000),
        "event_code": name,
        "event_name": name,
        "phase": phase,
        "optional_note": None,
        "observed_value": value,
    }


def _axis_values(second: int) -> tuple[int, int, int]:
    yaw = 120 if 5 <= second < 8 else -120 if 9 <= second < 12 else 0
    pitch = 80 if 13 <= second < 16 else -80 if 17 <= second < 20 else 0
    roll = 60 if 21 <= second < 24 else -60 if 25 <= second < 28 else 0
    return yaw, pitch, roll


def make_experiment(
    root: Path,
    *,
    include_targets: bool = True,
    include_events: bool = True,
    conflicting_battery: bool = False,
    fff5_write_count: int = 0,
) -> Path:
    root.mkdir(parents=True)
    base = 1_000_000_000_000
    frames = []
    if include_targets:
        for second in range(31):
            timestamp = base + second * 1_000_000_000
            yaw, pitch, roll = _axis_values(second)
            payload_05 = (
                yaw.to_bytes(2, "little", signed=True)
                + pitch.to_bytes(2, "little", signed=True)
                + roll.to_bytes(2, "little", signed=True)
                + (3).to_bytes(2, "little")
            )
            payload_27 = (
                (-2 * yaw).to_bytes(2, "little", signed=True)
                + (-2 * pitch).to_bytes(2, "little", signed=True)
                + (-2 * roll).to_bytes(2, "little", signed=True)
                + (7).to_bytes(2, "little")
            )
            battery = bytearray(34)
            battery[20] = 88
            frames.extend(
                [
                    _frame(timestamp, "04/05", payload_05),
                    _frame(timestamp + 5_000_000, "04/27", payload_27),
                    _frame(timestamp + 10_000_000, "04/1C", b"\x01\x00"),
                    _frame(timestamp + 15_000_000, "04/38", b"\x03\x00\x00\x00"),
                    _frame(timestamp + 20_000_000, "0D/02", bytes(battery)),
                    _frame(timestamp + 25_000_000, "00/81", b"hg212"),
                    _frame(timestamp + 30_000_000, "02/80", b"\x01\x02\x80\x00"),
                ]
            )
    else:
        frames.append(_frame(base, "FE/DC", b"unknown"))

    events = []
    if include_events:
        events = [
            _event(base, 0.0, "experiment_started", "session"),
            _event(base, 1.0, "baseline_static_start", "baseline"),
            _event(base, 4.0, "end_current_action", "end"),
            _event(base, 5.0, "yaw_left_start", "yaw"),
            _event(base, 8.0, "return_to_neutral", "recovery"),
            _event(base, 9.0, "yaw_right_start", "yaw"),
            _event(base, 12.0, "return_to_neutral", "recovery"),
            _event(base, 13.0, "pitch_up_start", "pitch"),
            _event(base, 16.0, "return_to_neutral", "recovery"),
            _event(base, 17.0, "pitch_down_start", "pitch"),
            _event(base, 20.0, "return_to_neutral", "recovery"),
            _event(base, 21.0, "roll_clockwise_start", "roll"),
            _event(base, 24.0, "return_to_neutral", "recovery"),
            _event(base, 25.0, "roll_counter_clockwise_start", "roll"),
            _event(base, 28.0, "return_to_neutral", "recovery"),
            _event(base, 0.2, "displayed_battery_percent", "observation", 88),
            _event(
                base,
                29.2,
                "displayed_battery_percent",
                "observation",
                89 if conflicting_battery else 88,
            ),
            _event(base, 30.5, "experiment_finished", "session"),
        ]
    _jsonl(root / "duml-frames.jsonl", frames)
    _jsonl(root / "events.jsonl", events)
    _jsonl(root / "notifications.jsonl", [])
    (root / "capture.btsnoop").write_bytes(b"fixture btsnoop")
    (root / "btmon.txt").write_text("fixture btmon\n", encoding="utf-8")
    session = {
        "device_address": "fixture",
        "notifications_received": len(frames),
        "duml_frames_received": len(frames),
        "fff5_write_count": fff5_write_count,
        "cccd_write_count": 2,
        "message_counts": {},
    }
    (root / "session.json").write_text(json.dumps(session), encoding="utf-8")
    lines = []
    for name in (
        "capture.btsnoop",
        "btmon.txt",
        "notifications.jsonl",
        "duml-frames.jsonl",
        "events.jsonl",
    ):
        digest = hashlib.sha256((root / name).read_bytes()).hexdigest()
        lines.append(f"{digest}  {name}\n")
    (root / "checksums.sha256").write_text("".join(lines), encoding="utf-8")
    return root


def test_int16_endianness_and_bounds() -> None:
    payload = bytes.fromhex("3412")
    assert decode_field(payload, 0, 2, "uint16_le") == 0x1234
    assert decode_field(payload, 0, 2, "uint16_be") == 0x3412
    assert decode_field(bytes.fromhex("ffff"), 0, 2, "int16_le") == -1
    with pytest.raises(ValueError, match="outside"):
        decode_field(payload, 1, 2, "uint16_le")


def test_payload_lengths_are_grouped_and_empty_targets_are_safe() -> None:
    records = [
        _frame(1, "04/05", b"\x01\x00"),
        _frame(2, "04/05", b"\x01\x00\x00\x00"),
    ]
    candidates = generate_field_candidates(records)
    assert {candidate["payload_length"] for candidate in candidates} == {2, 4}
    assert generate_field_candidates([_frame(1, "FE/DC", b"unknown")]) == []


def test_static_variance_and_action_direction_consistency(tmp_path: Path) -> None:
    experiment = make_experiment(tmp_path / "experiment")
    frames = [json.loads(line) for line in (experiment / "duml-frames.jsonl").read_text().splitlines()]
    events = [json.loads(line) for line in (experiment / "events.jsonl").read_text().splitlines()]
    candidates = generate_field_candidates(frames, events)
    yaw = next(
        item
        for item in candidates
        if item["command"] == "04/05"
        and item["offset"] == 0
        and item["encoding"] == "int16_le"
    )
    assert yaw["static_variance"] == 0
    assert yaw["event_correlations"]["axis_scores"]["yaw"]["score"] == 1.0
    assert yaw["event_correlations"]["axis_scores"]["yaw"]["opposite_direction"] is True


def test_static_variance_is_within_interval_not_between_poses() -> None:
    second = 1_000_000_000
    times = [0, second, 2 * second, 10 * second, 11 * second, 12 * second]
    values = [0.0, 0.0, 0.0, 100.0, 100.0, 100.0]
    events = [
        {"monotonic_ns": 0, "event_name": "baseline_static_start", "phase": "baseline"},
        {"monotonic_ns": 2 * second, "event_name": "end_current_action", "phase": "end"},
        {"monotonic_ns": 10 * second, "event_name": "stable_interval", "phase": "stable"},
        {"monotonic_ns": 12 * second, "event_name": "end_current_action", "phase": "end"},
    ]
    metrics = event_field_metrics(times, values, events)
    assert metrics["static_variance"] == 0.0


def test_alignment_windows_and_correlations() -> None:
    source = [{"monotonic_ns": 0}, {"monotonic_ns": 100_000_000}]
    target = [{"monotonic_ns": 40_000_000}, {"monotonic_ns": 170_000_000}]
    narrow = nearest_neighbor_alignment(source, target, max_window_ms=30)
    wide = nearest_neighbor_alignment(source, target, max_window_ms=100)
    assert narrow["matched_count"] == 0
    assert wide["matched_count"] == 2
    assert pearson_correlation([1, 2, 3], [2, 4, 6]) == pytest.approx(1.0)
    assert spearman_correlation([1, 3, 2], [10, 30, 20]) == pytest.approx(1.0)
    assert first_difference([0, 1, 3], [0, 1_000_000_000, 2_000_000_000]) == [1, 2]
    assert population_standard_deviation([1, 2, 3]) == pytest.approx((2 / 3) ** 0.5)


def test_wraparound_candidate_detection() -> None:
    assert wraparound_candidate([65530, 65535, 2, 5], width=2, signed=False)
    assert not wraparound_candidate([1, 2, 3], width=2, signed=False)
    assert not wraparound_candidate([32760, -32760], width=2, signed=True)


def test_complete_analysis_keeps_raw_files_immutable(tmp_path: Path) -> None:
    experiment = make_experiment(tmp_path / "pocket3-experiment-20260718-000000")
    before = {name: hashlib.sha256((experiment / name).read_bytes()).hexdigest() for name in (
        "capture.btsnoop",
        "btmon.txt",
        "notifications.jsonl",
        "duml-frames.jsonl",
        "events.jsonl",
    )}
    result = analyze_experiment(
        experiment,
        analysis_git_head="fixture-head",
        analysis_location="pytest",
    )
    after = {name: hashlib.sha256((experiment / name).read_bytes()).hexdigest() for name in before}
    assert before == after
    assert result["checksum_before"]["all_match"]
    assert result["checksum_after"]["all_match"]
    assert set(result["axis_candidates"]) == {"yaw", "pitch", "roll"}
    assert result["axis_candidates"]["roll"]["minimum_signal_to_static_noise"] > 0
    assert result["battery_comparison"]["status"] == "screen_correlated_but_no_transition_observed"
    observations = result["message_observations"]
    assert observations["04/27"]["payload_value_counts"]
    assert observations["04/27"]["payload_transitions"]
    analysis = experiment / "analysis"
    for name in (
        "field-candidates.csv",
        "field-candidates.json",
        "event-alignment.json",
        "correlations.csv",
        "message-pair-analysis.json",
        "message-observations.json",
        "telemetry-report.md",
        "state-model.json",
    ):
        assert (analysis / name).exists()
    for name in (
        "04_05_fields_vs_time.png",
        "04_27_fields_vs_time.png",
        "04_05_04_27_candidate_pair.png",
        "yaw_event_aligned_candidates.png",
        "pitch_event_aligned_candidates.png",
        "roll_event_aligned_candidates.png",
        "battery_candidate_vs_observation.png",
    ):
        assert (analysis / "plots" / name).read_bytes().startswith(b"\x89PNG")
    state = json.loads((analysis / "state-model.json").read_text())
    assert state["battery_percent"] == 88
    assert state["provenance"]["battery_percent"]["source_offset"] == 20
    assert state["provenance"]["battery_percent"]["evidence_session"] == experiment.name


def test_analysis_refuses_fff5_or_modified_raw_evidence(tmp_path: Path) -> None:
    unsafe = make_experiment(tmp_path / "unsafe", fff5_write_count=1)
    with pytest.raises(RuntimeError, match="fff5_write_count=1"):
        analyze_experiment(unsafe)
    modified = make_experiment(tmp_path / "modified")
    (modified / "events.jsonl").write_text("modified\n", encoding="utf-8")
    with pytest.raises(RuntimeError, match="checksum"):
        analyze_experiment(modified)


def test_empty_event_and_no_target_session_still_reports(tmp_path: Path) -> None:
    experiment = make_experiment(
        tmp_path / "empty", include_targets=False, include_events=False
    )
    result = analyze_experiment(experiment)
    assert result["field_candidate_count"] == 0
    assert result["axis_candidates"] == {}
    assert result["battery_comparison"]["status"] == "candidate_missing"


def test_conflicting_battery_observation_is_rejected(tmp_path: Path) -> None:
    experiment = make_experiment(
        tmp_path / "battery-conflict", conflicting_battery=True
    )
    result = analyze_experiment(experiment)
    assert result["battery_comparison"]["status"] == "screen_value_mismatch"
    assert result["battery_comparison"]["confidence"] == "rejected"


def test_state_model_preserves_unknown_field_provenance() -> None:
    state = Pocket3State()
    provenance = ObservationProvenance(
        source_cmd_set=4,
        source_cmd_id=0x38,
        source_offset=2,
        raw_value="7f",
        decoded_value=127,
        confidence="unknown",
        last_updated=123,
        evidence_session="fixture-session",
        encoding="uint8",
    )
    state.apply("gimbal_mode_candidate", 127, provenance)
    payload = state.to_dict()
    assert payload["gimbal_mode_candidate"] == 127
    assert payload["provenance"]["gimbal_mode_candidate"]["raw_value"] == "7f"
    assert payload["confidence"]["gimbal_mode_candidate"] == "unknown"


def test_checksum_manifest_requires_all_raw_files(tmp_path: Path) -> None:
    experiment = make_experiment(tmp_path / "manifest")
    lines = (experiment / "checksums.sha256").read_text().splitlines()
    (experiment / "checksums.sha256").write_text("\n".join(lines[:-1]) + "\n")
    result = verify_raw_checksums(experiment)
    assert not result["all_match"]
    assert "events.jsonl" in result["missing_manifest_entries"]
