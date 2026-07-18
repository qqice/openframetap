from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from openframetap.protocol.duml import encode_duml_frame
from openframetap.protocol.livestream_commands import build_wifi_connect_frame
from openframetap.workflows.wifi_analysis import (
    WifiAnalysisError,
    analyze_wifi_capture,
    write_sanitized_wifi_analysis,
)


def _json(path: Path, payload: dict) -> None:
    path.write_text(json.dumps(payload) + "\n", encoding="utf-8")


def _jsonl(path: Path, rows: list[dict]) -> None:
    path.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")


def _fixture(tmp_path: Path, *, response_payload: bytes | None = None) -> tuple[Path, str]:
    capture = tmp_path / "artifacts" / "private" / "wifi-capture"
    capture.mkdir(parents=True)
    outgoing = build_wifi_connect_frame(ssid="Fixture5G", psk="fixture-password")
    digest = hashlib.sha256(outgoing).hexdigest()
    decoded = {
        "sender": "0x02",
        "receiver": "0x07",
        "sequence": 0x8C19,
        "flags": "0x40",
        "cmd_set": "0x07",
        "cmd_id": "0x47",
        "crc8": {"valid": True},
        "crc16": {"valid": True},
    }
    _json(
        capture / "transmission.json",
        {
            "status": "single_frame_written",
            "command": "wifi_connect",
            "frame_sha256": digest,
            "decoded": decoded,
        },
    )
    _json(
        capture / "summary.json",
        {
            "writes_attempted": 1,
            "automatic_follow_up_frames": 0,
            "error": None,
            "actual_listen_seconds": 30.0,
            "notification_count": 1 if response_payload is not None else 0,
            "duml_frame_count": 1 if response_payload is not None else 0,
            "connection_interruptions": 0,
            "connection_setup_disconnects": 0,
            "crc8_failure_count": 0,
            "crc16_failure_count": 0,
            "reassembly_failure_count": 0,
        },
    )
    _jsonl(
        capture / "events.jsonl",
        [
            {
                "event": "fff5_frame_write_start",
                "frame_sha256": digest,
                "chunk_count": 3,
                "monotonic_ns": 1_000_000_000,
            },
            *(
                {
                    "event": "fff5_chunk_write_attempt",
                    "frame_sha256": digest,
                    "chunk_index": index,
                    "monotonic_ns": 1_001_000_000 + index,
                }
                for index in range(3)
            ),
            {
                "event": "fff5_frame_write_complete",
                "frame_sha256": digest,
                "monotonic_ns": 1_010_000_000,
            },
        ],
    )
    frames: list[dict] = []
    if response_payload is not None:
        response = encode_duml_frame(
            sender=0x07,
            receiver=0x02,
            sequence=0x8C19,
            flags=0xC0,
            cmd_set=0x07,
            cmd_id=0x47,
            payload=response_payload,
        )
        frames.append(
            {
                "sender": "0x07",
                "receiver": "0x02",
                "sequence": 0x8C19,
                "flags": "0xC0",
                "cmd_set": "0x07",
                "cmd_id": "0x47",
                "payload_hex": response_payload.hex(),
                "raw_hex": response.hex(),
                "crc8": {"valid": True},
                "crc16": {"valid": True},
                "monotonic_ns": 1_110_000_000,
                "wall_timestamp": "2026-07-18T00:00:01Z",
            }
        )
    _jsonl(capture / "duml-frames.jsonl", frames)
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
    return capture, digest


def test_wifi_analysis_preserves_no_response_as_unknown(tmp_path: Path) -> None:
    capture, digest = _fixture(tmp_path)
    before = {path.name: path.read_bytes() for path in capture.iterdir()}
    result = analyze_wifi_capture(capture, expected_frame_sha256=digest)
    assert result["status"] == "no_protocol_response_observed"
    assert result["workflow_conclusion"] == "wifi_connected_or_unknown"
    assert result["application_frames_written"] == 1
    assert result["att_write_command_chunks"] == 3
    assert result["response"] is None
    assert {path.name: path.read_bytes() for path in capture.iterdir()} == before


def test_wifi_analysis_preserves_reference_zero_response_without_confirming(
    tmp_path: Path,
) -> None:
    capture, digest = _fixture(tmp_path, response_payload=b"\x00\x00")
    result = analyze_wifi_capture(capture, expected_frame_sha256=digest)
    assert result["status"] == "reference_success_payload_observed_unconfirmed"
    assert result["response"]["payload_hex"] == "0000"
    assert result["response"]["latency_ms"] == 100.0


def test_wifi_analysis_rejects_wrong_approved_hash(tmp_path: Path) -> None:
    capture, _ = _fixture(tmp_path)
    with pytest.raises(WifiAnalysisError, match="approved SHA"):
        analyze_wifi_capture(capture, expected_frame_sha256="0" * 64)


def test_sanitized_wifi_analysis_contains_no_outgoing_payload(tmp_path: Path) -> None:
    capture, digest = _fixture(tmp_path)
    result = analyze_wifi_capture(capture, expected_frame_sha256=digest)
    output = tmp_path / "artifacts" / "sanitized" / "wifi-result"
    write_sanitized_wifi_analysis(result, output)
    text = (output / "wifi-result.json").read_text(encoding="utf-8")
    assert "Fixture5G" not in text
    assert "fixture-password" not in text
    assert "frame_hex" not in text
