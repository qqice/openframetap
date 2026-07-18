from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from openframetap.protocol.duml import encode_duml_frame
from openframetap.protocol.livestream_commands import build_prepare_to_live_stream_frame
from openframetap.workflows.prepare_analysis import (
    PrepareAnalysisError,
    analyze_prepare_capture,
    write_sanitized_prepare_analysis,
)


def _write_json(path: Path, payload: dict) -> None:
    path.write_text(json.dumps(payload) + "\n", encoding="utf-8")


def _write_jsonl(path: Path, rows: list[dict]) -> None:
    path.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")


def _fixture(tmp_path: Path, *, response_payload: bytes = b"\x00") -> tuple[Path, str]:
    capture = tmp_path / "artifacts" / "private" / "capture"
    capture.mkdir(parents=True)
    outgoing = build_prepare_to_live_stream_frame()
    outgoing_sha = hashlib.sha256(outgoing).hexdigest()
    response = encode_duml_frame(
        sender=0x08,
        receiver=0x02,
        sequence=0x8C12,
        flags=0xC0,
        cmd_set=0x02,
        cmd_id=0xE1,
        payload=response_payload,
    )
    _write_json(
        capture / "transmission.json",
        {
            "status": "single_frame_written",
            "command": "prepare_to_live_stream",
            "frame_sha256": outgoing_sha,
            "decoded": {
                "sender": "0x02",
                "receiver": "0x08",
                "sequence": 0x8C12,
                "flags": "0x40",
                "cmd_set": "0x02",
                "cmd_id": "0xE1",
                "payload_hex": "1a",
                "crc8": {"valid": True},
                "crc16": {"valid": True},
            },
        },
    )
    _write_json(
        capture / "summary.json",
        {
            "writes_attempted": 1,
            "automatic_follow_up_frames": 0,
            "error": None,
            "notification_count": 1,
            "duml_frame_count": 1,
            "connection_interruptions": 0,
            "crc8_failure_count": 0,
            "crc16_failure_count": 0,
            "reassembly_failure_count": 0,
        },
    )
    _write_jsonl(
        capture / "events.jsonl",
        [
            {
                "event": "fff5_chunk_write_attempt",
                "frame_sha256": outgoing_sha,
                "monotonic_ns": 1_000_000_000,
            },
            {
                "event": "fff5_frame_write_complete",
                "frame_sha256": outgoing_sha,
                "monotonic_ns": 1_010_000_000,
            },
        ],
    )
    _write_jsonl(
        capture / "duml-frames.jsonl",
        [
            {
                "sender": "0x08",
                "receiver": "0x02",
                "sequence": 0x8C12,
                "flags": "0xC0",
                "cmd_set": "0x02",
                "cmd_id": "0xE1",
                "payload_hex": response_payload.hex(),
                "raw_hex": response.hex(),
                "crc8": {"valid": True},
                "crc16": {"valid": True},
                "monotonic_ns": 1_110_000_000,
                "wall_timestamp": "2026-07-18T00:00:01Z",
            }
        ],
    )
    for name in ("capture.btsnoop", "btmon.txt", "notifications.jsonl"):
        (capture / name).write_bytes(name.encode())
    names = [
        "capture.btsnoop",
        "btmon.txt",
        "notifications.jsonl",
        "duml-frames.jsonl",
        "events.jsonl",
        "summary.json",
        "transmission.json",
    ]
    (capture / "checksums.sha256").write_text(
        "".join(
            f"{hashlib.sha256((capture / name).read_bytes()).hexdigest()}  {name}\n"
            for name in names
        ),
        encoding="ascii",
    )
    return capture, outgoing_sha


def test_prepare_analysis_proves_exact_ack_and_preserves_raw_files(tmp_path: Path) -> None:
    capture, outgoing_sha = _fixture(tmp_path)
    before = {path.name: path.read_bytes() for path in capture.iterdir()}
    result = analyze_prepare_capture(capture, expected_frame_sha256=outgoing_sha)
    assert result["status"] == "prepare_acknowledged"
    assert result["response_latency_ms"] == 100.0
    assert result["writes_attempted"] == 1
    assert result["raw_files_unchanged"] is True
    assert {path.name: path.read_bytes() for path in capture.iterdir()} == before


def test_prepare_analysis_rejects_unexpected_payload(tmp_path: Path) -> None:
    capture, outgoing_sha = _fixture(tmp_path, response_payload=b"\x01")
    with pytest.raises(PrepareAnalysisError, match="payload 00"):
        analyze_prepare_capture(capture, expected_frame_sha256=outgoing_sha)


def test_prepare_analysis_rejects_more_than_one_write(tmp_path: Path) -> None:
    capture, outgoing_sha = _fixture(tmp_path)
    summary = json.loads((capture / "summary.json").read_text())
    summary["writes_attempted"] = 2
    _write_json(capture / "summary.json", summary)
    manifest = capture / "checksums.sha256"
    manifest.write_text(
        "".join(
            (
                f"{hashlib.sha256((capture / 'summary.json').read_bytes()).hexdigest()}  summary.json\n"
                if line.endswith("  summary.json\n")
                else line
            )
            for line in manifest.read_text(encoding="ascii").splitlines(keepends=True)
        ),
        encoding="ascii",
    )
    with pytest.raises(PrepareAnalysisError, match="exactly one"):
        analyze_prepare_capture(capture, expected_frame_sha256=outgoing_sha)


def test_sanitized_prepare_result_contains_no_private_address(tmp_path: Path) -> None:
    capture, outgoing_sha = _fixture(tmp_path)
    result = analyze_prepare_capture(capture, expected_frame_sha256=outgoing_sha)
    output = tmp_path / "artifacts" / "sanitized" / "result"
    checksums = write_sanitized_prepare_analysis(result, output)
    text = (output / "prepare-result.json").read_text()
    assert "00:11:22:33:44:55" not in text
    assert set(checksums) == {"prepare-result.json", "report.md"}
