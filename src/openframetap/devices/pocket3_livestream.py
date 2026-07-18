"""Pocket 3 livestream proposal generation with private/sanitized split."""

from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path

from openframetap.network.secrets import require_private_directory
from openframetap.protocol.commands import validate_command_frame
from openframetap.protocol.duml import decode_duml_frame
from openframetap.protocol.livestream_commands import (
    LIVESTREAM_COMMANDS,
    build_prepare_to_live_stream_frame,
)


def _mask_address(address: str) -> str:
    parts = address.split(":")
    return f"{parts[0]}:***:{parts[-1]}" if len(parts) == 6 else "<redacted>"


def _write_private(path: Path, data: bytes) -> None:
    path.write_bytes(data)
    if os.name != "nt":
        path.chmod(0o600)


def write_prepare_proposal(
    *,
    address: str,
    private_root: Path,
    sanitized_root: Path,
    sequence: int = 0x8C12,
) -> dict:
    command = LIVESTREAM_COMMANDS["prepare_to_live_stream"]
    frame = build_prepare_to_live_stream_frame(sequence=sequence)
    decoded = decode_duml_frame(frame)
    if not (decoded.crc8_valid and decoded.crc16_valid) or decoded.raw != frame:
        raise RuntimeError("offline prepare proposal round-trip validation failed")
    validate_command_frame(command, decoded)
    digest = hashlib.sha256(frame).hexdigest()
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    private_dir = require_private_directory(private_root / f"prepare-{stamp}")
    sanitized_dir = (sanitized_root / f"prepare-{stamp}").resolve()
    if "sanitized" not in {part.lower() for part in sanitized_dir.parts}:
        raise ValueError("sanitized proposal must be under artifacts/sanitized")
    sanitized_dir.mkdir(parents=True, exist_ok=True)
    private_payload = {
        "schema_version": 1,
        "stage": "prepare",
        "command": command.name,
        "target_address": address,
        "frame_hex": frame.hex(),
        "frame_sha256": digest,
        "max_send_count": 1,
        "automatic_retry": False,
        "automatic_follow_up": False,
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "decoded": decoded.to_dict(),
    }
    private_json = private_dir / "proposal-private.json"
    private_bin = private_dir / "proposal.bin"
    _write_private(
        private_json,
        (json.dumps(private_payload, indent=2, ensure_ascii=False) + "\n").encode(),
    )
    _write_private(private_bin, frame)
    sanitized = {
        "schema_version": 1,
        "stage": "prepare",
        "command": command.name,
        "target_address_masked": _mask_address(address),
        "target_address_sha256": hashlib.sha256(address.upper().encode()).hexdigest(),
        "source_component": f"0x{command.sender:02X}",
        "target_component": f"0x{command.receiver:02X}",
        "cmd_set": f"0x{command.cmd_set:02X}",
        "cmd_id": f"0x{command.cmd_id:02X}",
        "sequence": f"0x{sequence:04X}",
        "flags": "0x40",
        "payload_schema": command.payload_schema,
        "payload_hex": decoded.payload.hex(),
        "frame_hex": frame.hex(),
        "frame_sha256": digest,
        "total_length": decoded.total_length,
        "crc8_valid": decoded.crc8_valid,
        "crc16_valid": decoded.crc16_valid,
        "round_trip_valid": decoded.raw == frame,
        "reference_sources": list(command.reference_sources),
        "confidence": command.confidence,
        "locally_sent": False,
        "contains_sensitive_data": False,
        "max_send_count": 1,
        "automatic_retry": False,
        "automatic_follow_up": False,
        "expected_response": "same-sequence C0/02/E1 payload 00 (reference-derived)",
        "risk": "may change the Pocket application into livestream preparation state",
    }
    sanitized_json = sanitized_dir / "proposal.json"
    sanitized_json.write_text(
        json.dumps(sanitized, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    (sanitized_dir / "proposal.sha256").write_text(
        f"{digest}  proposal-frame\n", encoding="ascii"
    )
    return {
        **sanitized,
        "private_proposal": str(private_json),
        "sanitized_proposal": str(sanitized_json),
    }


def load_fixed_proposal(path: Path, *, expected_address: str) -> tuple[dict, bytes]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    required = {"schema_version", "command", "target_address", "frame_hex", "frame_sha256"}
    if not required.issubset(payload) or payload.get("max_send_count") != 1:
        raise PermissionError("proposal schema or single-send limit is invalid")
    if payload["target_address"].upper() != expected_address.upper():
        raise PermissionError("proposal Pocket address does not match the selected target")
    raw = bytes.fromhex(payload["frame_hex"])
    if hashlib.sha256(raw).hexdigest() != payload["frame_sha256"].lower():
        raise PermissionError("proposal frame SHA-256 mismatch")
    command = LIVESTREAM_COMMANDS.get(payload["command"])
    if command is None or command.name != "prepare_to_live_stream":
        raise PermissionError("proposal command is not in the current single-send allowlist")
    decoded = decode_duml_frame(raw)
    validate_command_frame(command, decoded)
    return payload, raw

