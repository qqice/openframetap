"""Offline, immutable-evidence validation of the Pocket 3 prepare response."""

from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
from typing import Any


class PrepareAnalysisError(RuntimeError):
    """Raised when captured evidence does not prove the expected prepare ACK."""


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _snapshot(directory: Path) -> dict[str, str]:
    return {
        path.name: _sha256(path)
        for path in sorted(directory.iterdir())
        if path.is_file()
    }


def _load_json(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise PrepareAnalysisError(f"{path.name} must contain a JSON object")
    return payload


def _load_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    with path.open(encoding="utf-8") as stream:
        for line_number, line in enumerate(stream, 1):
            if not line.strip():
                continue
            payload = json.loads(line)
            if not isinstance(payload, dict):
                raise PrepareAnalysisError(
                    f"{path.name}:{line_number} must contain a JSON object"
                )
            rows.append(payload)
    return rows


def _wire_int(value: Any) -> int:
    if isinstance(value, int):
        return value
    if isinstance(value, str):
        return int(value, 0)
    raise PrepareAnalysisError(f"unsupported wire integer: {value!r}")


def _verify_manifest(directory: Path) -> dict[str, str]:
    manifest = directory / "checksums.sha256"
    if not manifest.is_file():
        raise PrepareAnalysisError("checksums.sha256 is missing")
    verified: dict[str, str] = {}
    for line_number, line in enumerate(manifest.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        parts = line.split(maxsplit=1)
        if len(parts) != 2:
            raise PrepareAnalysisError(f"invalid checksum manifest line {line_number}")
        expected, name = parts
        name = name.lstrip("*")
        if Path(name).name != name:
            raise PrepareAnalysisError("checksum manifest contains a non-local path")
        path = directory / name
        if not path.is_file() or _sha256(path) != expected.lower():
            raise PrepareAnalysisError(f"checksum mismatch: {name}")
        verified[name] = expected.lower()
    return verified


def analyze_prepare_capture(
    capture_dir: Path, *, expected_frame_sha256: str
) -> dict[str, Any]:
    """Validate a prepare capture without modifying any source evidence file."""

    capture_dir = capture_dir.resolve()
    if not capture_dir.is_dir() or "private" not in {
        part.lower() for part in capture_dir.parts
    }:
        raise PrepareAnalysisError("prepare capture must be an existing private directory")
    expected_frame_sha256 = expected_frame_sha256.lower()
    if len(expected_frame_sha256) != 64 or any(
        character not in "0123456789abcdef" for character in expected_frame_sha256
    ):
        raise PrepareAnalysisError("expected frame SHA-256 is invalid")

    before = _snapshot(capture_dir)
    verified_manifest = _verify_manifest(capture_dir)
    summary = _load_json(capture_dir / "summary.json")
    transmission = _load_json(capture_dir / "transmission.json")
    events = _load_jsonl(capture_dir / "events.jsonl")
    frames = _load_jsonl(capture_dir / "duml-frames.jsonl")

    if transmission.get("status") != "single_frame_written":
        raise PrepareAnalysisError("capture does not record a completed single-frame write")
    if transmission.get("command") != "prepare_to_live_stream":
        raise PrepareAnalysisError("capture command is not prepare_to_live_stream")
    if transmission.get("frame_sha256", "").lower() != expected_frame_sha256:
        raise PrepareAnalysisError("capture frame does not match the approved SHA-256")
    if summary.get("writes_attempted") != 1:
        raise PrepareAnalysisError("capture must contain exactly one attempted FFF5 frame")
    if summary.get("automatic_follow_up_frames") != 0:
        raise PrepareAnalysisError("capture contains an automatic follow-up frame")
    if summary.get("error") is not None:
        raise PrepareAnalysisError(f"capture recorded an error: {summary['error']}")

    outgoing = transmission.get("decoded") or {}
    expected_outgoing = {
        "sender": 0x02,
        "receiver": 0x08,
        "sequence": 0x8C12,
        "flags": 0x40,
        "cmd_set": 0x02,
        "cmd_id": 0xE1,
    }
    for key, expected in expected_outgoing.items():
        if _wire_int(outgoing.get(key)) != expected:
            raise PrepareAnalysisError(f"unexpected outgoing {key}")
    if outgoing.get("payload_hex", "").lower() != "1a":
        raise PrepareAnalysisError("unexpected outgoing prepare payload")
    if not outgoing.get("crc8", {}).get("valid") or not outgoing.get("crc16", {}).get(
        "valid"
    ):
        raise PrepareAnalysisError("outgoing prepare frame failed CRC validation")

    write_attempts = [
        event
        for event in events
        if event.get("event") == "fff5_chunk_write_attempt"
        and event.get("frame_sha256", expected_frame_sha256).lower()
        == expected_frame_sha256
    ]
    write_completions = [
        event
        for event in events
        if event.get("event") == "fff5_frame_write_complete"
        and event.get("frame_sha256", "").lower() == expected_frame_sha256
    ]
    if len(write_attempts) != 1 or len(write_completions) != 1:
        raise PrepareAnalysisError("event log does not prove one completed FFF5 write")
    write_monotonic_ns = int(write_completions[0]["monotonic_ns"])

    matching_sequence = []
    for frame in frames:
        try:
            is_candidate = (
                _wire_int(frame.get("sequence")) == expected_outgoing["sequence"]
                and _wire_int(frame.get("cmd_set")) == expected_outgoing["cmd_set"]
                and _wire_int(frame.get("cmd_id")) == expected_outgoing["cmd_id"]
            )
        except (PrepareAnalysisError, TypeError, ValueError):
            is_candidate = False
        if is_candidate:
            matching_sequence.append(frame)

    expected_responses = [
        frame
        for frame in matching_sequence
        if _wire_int(frame.get("sender")) == 0x08
        and _wire_int(frame.get("receiver")) == 0x02
        and _wire_int(frame.get("flags")) == 0xC0
        and frame.get("payload_hex", "").lower() == "00"
        and frame.get("crc8", {}).get("valid") is True
        and frame.get("crc16", {}).get("valid") is True
    ]
    if len(expected_responses) != 1 or len(matching_sequence) != 1:
        raise PrepareAnalysisError(
            "expected exactly one matching-sequence C0/02/E1 payload 00 response"
        )
    response = expected_responses[0]
    response_monotonic_ns = int(response["monotonic_ns"])
    if response_monotonic_ns <= write_monotonic_ns:
        raise PrepareAnalysisError("prepare response timestamp precedes the write")

    after = _snapshot(capture_dir)
    if after != before:
        raise PrepareAnalysisError("raw capture changed during offline analysis")

    response_raw = bytes.fromhex(response["raw_hex"])
    return {
        "schema_version": 1,
        "status": "prepare_acknowledged",
        "analyzed_at_utc": datetime.now(timezone.utc).isoformat(),
        "analysis_location": "local_windows_source_workspace",
        "capture_directory_name": capture_dir.name,
        "approved_frame_sha256": expected_frame_sha256,
        "writes_attempted": 1,
        "automatic_follow_up_frames": 0,
        "response": {
            "sender": "0x08",
            "receiver": "0x02",
            "sequence": "0x8C12",
            "flags": "0xC0",
            "cmd_set": "0x02",
            "cmd_id": "0xE1",
            "payload_hex": "00",
            "raw_hex": response["raw_hex"],
            "frame_sha256": hashlib.sha256(response_raw).hexdigest(),
            "crc8_valid": True,
            "crc16_valid": True,
            "wall_timestamp": response.get("wall_timestamp"),
            "monotonic_ns": response_monotonic_ns,
        },
        "write_completed_monotonic_ns": write_monotonic_ns,
        "response_latency_ms": (response_monotonic_ns - write_monotonic_ns) / 1_000_000,
        "notifications_received": summary.get("notification_count"),
        "duml_frames_received": summary.get("duml_frame_count"),
        "connection_interruptions": summary.get("connection_interruptions"),
        "crc8_failures": summary.get("crc8_failure_count"),
        "crc16_failures": summary.get("crc16_failure_count"),
        "reassembly_failures": summary.get("reassembly_failure_count"),
        "verified_manifest": verified_manifest,
        "raw_files_unchanged": True,
        "interpretation": {
            "hardware_fact": "one exact matching-sequence response was captured",
            "reference_conclusion": "payload 00 is the expected prepare success result",
            "workflow_conclusion": "prepare acknowledged; no later command is authorized",
        },
    }


def write_sanitized_prepare_analysis(result: dict[str, Any], output_dir: Path) -> dict[str, str]:
    output_dir = output_dir.resolve()
    if "sanitized" not in {part.lower() for part in output_dir.parts}:
        raise PrepareAnalysisError("prepare analysis output must be under artifacts/sanitized")
    output_dir.mkdir(parents=True, exist_ok=True)
    json_path = output_dir / "prepare-result.json"
    report_path = output_dir / "report.md"
    json_path.write_text(
        json.dumps(result, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    response = result["response"]
    report = f"""# Pocket 3 prepare result

- 【实机事实】The owner-confirmed prepare frame was written exactly once; automatic follow-up count was zero.
- 【实机事实】Pocket returned `08 -> 02`, `C0/02/E1`, sequence `{response['sequence']}`, payload `00`; CRC8 and CRC16 validate.
- 【统计观察】Response latency from local write completion was `{result['response_latency_ms']:.3f} ms`.
- 【捕获推断】The exact reverse direction, matching sequence, ACK flags, and payload identify this as the response to the approved request.
- 【参考实现结论】Reviewed public implementations/capture evidence interpret payload `00` as successful livestream preparation.
- 【待验证假设】No Wi-Fi, RTMP configuration, start, or stop command has been validated by this result.

No subsequent command is authorized by this analysis.
"""
    report_path.write_text(report, encoding="utf-8")
    checksums = {
        json_path.name: _sha256(json_path),
        report_path.name: _sha256(report_path),
    }
    (output_dir / "checksums.sha256").write_text(
        "".join(f"{digest}  {name}\n" for name, digest in checksums.items()),
        encoding="ascii",
    )
    return checksums
