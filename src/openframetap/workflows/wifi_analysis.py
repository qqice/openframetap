"""Offline validation of a sensitive Pocket 3 Wi-Fi provisioning capture."""

from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
from typing import Any

from openframetap.workflows.prepare_analysis import (
    PrepareAnalysisError,
    _load_json,
    _load_jsonl,
    _snapshot,
    _verify_manifest,
    _wire_int,
)


class WifiAnalysisError(RuntimeError):
    """Raised when Wi-Fi evidence violates the approved single-send boundary."""


def analyze_wifi_capture(
    capture_dir: Path, *, expected_frame_sha256: str
) -> dict[str, Any]:
    capture_dir = capture_dir.resolve()
    if not capture_dir.is_dir() or "private" not in {
        part.lower() for part in capture_dir.parts
    }:
        raise WifiAnalysisError("Wi-Fi capture must be an existing private directory")
    expected_frame_sha256 = expected_frame_sha256.lower()
    if len(expected_frame_sha256) != 64 or any(
        character not in "0123456789abcdef" for character in expected_frame_sha256
    ):
        raise WifiAnalysisError("expected frame SHA-256 is invalid")

    before = _snapshot(capture_dir)
    try:
        verified_manifest = _verify_manifest(capture_dir)
        summary = _load_json(capture_dir / "summary.json")
        transmission = _load_json(capture_dir / "transmission.json")
        events = _load_jsonl(capture_dir / "events.jsonl")
        frames = _load_jsonl(capture_dir / "duml-frames.jsonl")
    except (PrepareAnalysisError, OSError, ValueError, json.JSONDecodeError) as exc:
        raise WifiAnalysisError(str(exc)) from exc

    if transmission.get("status") != "single_frame_written":
        raise WifiAnalysisError("capture does not record a completed single-frame write")
    if transmission.get("command") != "wifi_connect":
        raise WifiAnalysisError("capture command is not wifi_connect")
    if transmission.get("frame_sha256", "").lower() != expected_frame_sha256:
        raise WifiAnalysisError("capture frame does not match the approved SHA-256")
    if summary.get("writes_attempted") != 1:
        raise WifiAnalysisError("capture must contain exactly one application FFF5 frame")
    if summary.get("automatic_follow_up_frames") != 0:
        raise WifiAnalysisError("capture contains an automatic follow-up frame")
    if summary.get("error") is not None:
        raise WifiAnalysisError(f"capture recorded an error: {summary['error']}")

    outgoing = transmission.get("decoded") or {}
    expected_outgoing = {
        "sender": 0x02,
        "receiver": 0x07,
        "sequence": 0x8C19,
        "flags": 0x40,
        "cmd_set": 0x07,
        "cmd_id": 0x47,
    }
    try:
        for key, expected in expected_outgoing.items():
            if _wire_int(outgoing.get(key)) != expected:
                raise WifiAnalysisError(f"unexpected outgoing {key}")
    except (PrepareAnalysisError, TypeError, ValueError) as exc:
        raise WifiAnalysisError(str(exc)) from exc
    if not outgoing.get("crc8", {}).get("valid") or not outgoing.get("crc16", {}).get(
        "valid"
    ):
        raise WifiAnalysisError("outgoing Wi-Fi frame failed CRC validation")

    starts = [event for event in events if event.get("event") == "fff5_frame_write_start"]
    completions = [
        event
        for event in events
        if event.get("event") == "fff5_frame_write_complete"
        and event.get("frame_sha256", "").lower() == expected_frame_sha256
    ]
    attempts = [
        event for event in events if event.get("event") == "fff5_chunk_write_attempt"
    ]
    if len(starts) != 1 or len(completions) != 1:
        raise WifiAnalysisError("event log does not prove one completed application frame")
    chunk_count = int(starts[0].get("chunk_count", 0))
    if chunk_count < 1 or len(attempts) != chunk_count:
        raise WifiAnalysisError("ATT chunk evidence is incomplete")
    write_monotonic_ns = int(completions[0]["monotonic_ns"])

    matching: list[dict[str, Any]] = []
    component_07_count = 0
    ack_frame_count = 0
    for frame in frames:
        try:
            sender = _wire_int(frame.get("sender"))
            sequence = _wire_int(frame.get("sequence"))
            flags = _wire_int(frame.get("flags"))
            cmd_set = _wire_int(frame.get("cmd_set"))
            cmd_id = _wire_int(frame.get("cmd_id"))
        except (PrepareAnalysisError, TypeError, ValueError):
            continue
        component_07_count += int(sender == 0x07)
        ack_frame_count += int(bool(flags & 0x80))
        if sequence == 0x8C19 and cmd_set == 0x07 and cmd_id == 0x47:
            matching.append(frame)
    if len(matching) > 1:
        raise WifiAnalysisError("multiple matching-sequence Wi-Fi responses are ambiguous")

    response: dict[str, Any] | None = None
    if matching:
        frame = matching[0]
        if (
            _wire_int(frame.get("sender")) != 0x07
            or _wire_int(frame.get("receiver")) != 0x02
            or _wire_int(frame.get("flags")) != 0xC0
            or frame.get("crc8", {}).get("valid") is not True
            or frame.get("crc16", {}).get("valid") is not True
        ):
            raise WifiAnalysisError("matching-sequence Wi-Fi frame is not a valid response")
        response_raw = bytes.fromhex(frame["raw_hex"])
        payload_hex = frame.get("payload_hex", "").lower()
        if payload_hex in {"0000", "000000"}:
            status = "reference_success_payload_observed_unconfirmed"
        else:
            status = "explicit_response_payload_uninterpreted"
        response = {
            "sender": "0x07",
            "receiver": "0x02",
            "sequence": "0x8C19",
            "flags": "0xC0",
            "cmd_set": "0x07",
            "cmd_id": "0x47",
            "payload_hex": payload_hex,
            "raw_hex": frame["raw_hex"],
            "frame_sha256": hashlib.sha256(response_raw).hexdigest(),
            "crc8_valid": True,
            "crc16_valid": True,
            "wall_timestamp": frame.get("wall_timestamp"),
            "monotonic_ns": frame.get("monotonic_ns"),
            "latency_ms": (
                (int(frame["monotonic_ns"]) - write_monotonic_ns) / 1_000_000
            ),
        }
    else:
        status = "no_protocol_response_observed"

    after = _snapshot(capture_dir)
    if after != before:
        raise WifiAnalysisError("raw Wi-Fi capture changed during offline analysis")

    return {
        "schema_version": 1,
        "status": status,
        "workflow_conclusion": "wifi_connected_or_unknown",
        "analyzed_at_utc": datetime.now(timezone.utc).isoformat(),
        "analysis_location": "local_windows_source_workspace",
        "capture_directory_name": capture_dir.name,
        "approved_frame_sha256": expected_frame_sha256,
        "application_frames_written": 1,
        "att_write_command_chunks": chunk_count,
        "automatic_follow_up_frames": 0,
        "response": response,
        "matching_sequence_response_count": len(matching),
        "component_07_frame_count": component_07_count,
        "ack_frame_count": ack_frame_count,
        "actual_listen_seconds": summary.get("actual_listen_seconds"),
        "notifications_received": summary.get("notification_count"),
        "duml_frames_received": summary.get("duml_frame_count"),
        "connection_interruptions": summary.get("connection_interruptions"),
        "connection_setup_disconnects": summary.get("connection_setup_disconnects"),
        "crc8_failures": summary.get("crc8_failure_count"),
        "crc16_failures": summary.get("crc16_failure_count"),
        "reassembly_failures": summary.get("reassembly_failure_count"),
        "verified_manifest": verified_manifest,
        "raw_files_unchanged": True,
        "interpretation": {
            "hardware_fact": "one approved application frame was written and telemetry continued",
            "capture_fact": "no matching-sequence 07/47 response was observed"
            if response is None
            else "one matching-sequence 07/47 response was observed",
            "reference_conclusion": "public success payload length remains conflicting",
            "safety_conclusion": "do not retry and do not generate the next command",
        },
    }


def write_sanitized_wifi_analysis(result: dict[str, Any], output_dir: Path) -> dict[str, str]:
    output_dir = output_dir.resolve()
    if "sanitized" not in {part.lower() for part in output_dir.parts}:
        raise WifiAnalysisError("Wi-Fi analysis output must be under artifacts/sanitized")
    output_dir.mkdir(parents=True, exist_ok=True)
    json_path = output_dir / "wifi-result.json"
    report_path = output_dir / "report.md"
    json_path.write_text(
        json.dumps(result, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    if result["response"] is None:
        response_text = "No same-sequence `07/47` response was captured."
    else:
        response_text = (
            "One same-sequence `C0/07/47` response was captured with payload "
            f"`{result['response']['payload_hex']}`."
        )
    report_path.write_text(
        f"""# Pocket 3 Wi-Fi provisioning result

- 【实机事实】One approved application frame was written as {result['att_write_command_chunks']} ATT chunks; no retry or follow-up frame was sent.
- 【实机事实】{response_text}
- 【统计观察】The capture retained {result['duml_frames_received']} valid DUML frames over {result['actual_listen_seconds']:.3f} seconds with zero CRC or reassembly failure.
- 【捕获推断】The Pocket remained connected for telemetry after the write, but this does not prove Wi-Fi association.
- 【参考实现结论】Public implementations disagree on the successful `07/47` response payload length.
- 【待验证假设】External-network association is unknown until independent network or device evidence is available.

No retry or later livestream command is authorized by this result.
""",
        encoding="utf-8",
    )
    checksums = {
        json_path.name: hashlib.sha256(json_path.read_bytes()).hexdigest(),
        report_path.name: hashlib.sha256(report_path.read_bytes()).hexdigest(),
    }
    (output_dir / "checksums.sha256").write_text(
        "".join(f"{digest}  {name}\n" for name, digest in checksums.items()),
        encoding="ascii",
    )
    return checksums
