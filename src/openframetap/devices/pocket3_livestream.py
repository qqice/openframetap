"""Pocket 3 livestream proposal generation with private/sanitized split."""

from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path

from openframetap.network.secrets import WifiProvisioningSecrets, require_private_directory
from openframetap.protocol.commands import validate_command_frame
from openframetap.protocol.duml import decode_duml_frame
from openframetap.protocol.livestream_commands import (
    LIVESTREAM_COMMANDS,
    build_configure_live_stream_frame,
    build_prepare_to_live_stream_frame,
    build_prepare_stream_stage2_frame,
    build_start_live_stream_transport_frame,
    build_wifi_connect_frame,
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
    server_evidence_sha256: str,
    pairing_evidence: str,
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
        "evidence": {
            "server_selftest_sha256": server_evidence_sha256,
            "prior_pairing_evidence": pairing_evidence,
        },
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
        "evidence": {
            "server_selftest_sha256": server_evidence_sha256,
            "prior_pairing_evidence": pairing_evidence,
        },
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
    required = {
        "schema_version",
        "command",
        "target_address",
        "frame_hex",
        "frame_sha256",
        "evidence",
    }
    if not required.issubset(payload) or payload.get("max_send_count") != 1:
        raise PermissionError("proposal schema or single-send limit is invalid")
    if payload["target_address"].upper() != expected_address.upper():
        raise PermissionError("proposal Pocket address does not match the selected target")
    raw = bytes.fromhex(payload["frame_hex"])
    if hashlib.sha256(raw).hexdigest() != payload["frame_sha256"].lower():
        raise PermissionError("proposal frame SHA-256 mismatch")
    evidence = payload.get("evidence") or {}
    server_sha = evidence.get("server_selftest_sha256", "")
    pairing_reference = evidence.get("prior_pairing_evidence", "")
    if len(server_sha) != 64 or not pairing_reference.rsplit(":", 1)[-1]:
        raise PermissionError("proposal prerequisite evidence is missing or invalid")
    command = LIVESTREAM_COMMANDS.get(payload["command"])
    if command is None or command.name != "prepare_to_live_stream":
        raise PermissionError("proposal command is not in the current single-send allowlist")
    decoded = decode_duml_frame(raw)
    validate_command_frame(command, decoded)
    return payload, raw


def load_fixed_wifi_proposal(
    path: Path, *, expected_address: str, expected_sequence: int = 0x8C19
) -> tuple[dict, bytes]:
    """Load one sensitive Wi-Fi proposal without exposing its payload."""

    payload = json.loads(path.read_text(encoding="utf-8"))
    required = {
        "schema_version",
        "stage",
        "command",
        "target_address",
        "frame_hex",
        "frame_sha256",
        "evidence",
        "secret_fingerprints",
    }
    if not required.issubset(payload):
        raise PermissionError("Wi-Fi proposal schema is incomplete")
    if (
        payload.get("stage") != "wifi"
        or payload.get("command") != "wifi_connect"
        or payload.get("max_send_count") != 1
        or payload.get("automatic_retry") is not False
        or payload.get("automatic_follow_up") is not False
        or payload.get("contains_sensitive_data") is not True
    ):
        raise PermissionError("Wi-Fi proposal single-send policy is invalid")
    if payload["target_address"].upper() != expected_address.upper():
        raise PermissionError("Wi-Fi proposal Pocket address does not match the selected target")
    raw = bytes.fromhex(payload["frame_hex"])
    digest = hashlib.sha256(raw).hexdigest()
    if digest != payload["frame_sha256"].lower():
        raise PermissionError("Wi-Fi proposal frame SHA-256 mismatch")
    prepare_result_sha = (payload.get("evidence") or {}).get(
        "prepare_result_sha256", ""
    )
    if len(prepare_result_sha) != 64 or any(
        character not in "0123456789abcdef" for character in prepare_result_sha.lower()
    ):
        raise PermissionError("Wi-Fi proposal prepare evidence is missing")
    command = LIVESTREAM_COMMANDS["wifi_connect"]
    decoded = decode_duml_frame(raw)
    validate_command_frame(command, decoded)
    if not decoded.crc8_valid or not decoded.crc16_valid or decoded.raw != raw:
        raise PermissionError("Wi-Fi proposal failed CRC or round-trip validation")
    if (
        decoded.sender,
        decoded.receiver,
        decoded.sequence,
        decoded.flags,
        decoded.cmd_set,
        decoded.cmd_id,
    ) != (0x02, 0x07, expected_sequence, 0x40, 0x07, 0x47):
        raise PermissionError("Wi-Fi proposal wire fields are not the approved candidate")
    ssid_length = decoded.payload[0]
    psk_length_offset = 1 + ssid_length
    psk_length = decoded.payload[psk_length_offset]
    ssid = decoded.payload[1:psk_length_offset]
    psk = decoded.payload[psk_length_offset + 1 :]
    fingerprints = payload["secret_fingerprints"]
    if (
        len(ssid) != fingerprints.get("ssid_encoded_length")
        or len(psk) != psk_length
        or len(psk) != fingerprints.get("psk_length")
        or hashlib.sha256(ssid).hexdigest() != fingerprints.get("ssid_sha256")
        or hashlib.sha256(psk).hexdigest() != fingerprints.get("psk_sha256")
    ):
        raise PermissionError("Wi-Fi proposal secret fingerprints do not match its frame")
    return payload, raw


def write_wifi_proposal(
    *,
    address: str,
    secrets: WifiProvisioningSecrets,
    private_root: Path,
    sanitized_root: Path,
    prepare_result_sha256: str,
    sequence: int = 0x8C19,
) -> dict:
    """Write one sensitive 07/47 proposal; never print its frame or payload."""

    command = LIVESTREAM_COMMANDS["wifi_connect"]
    frame = build_wifi_connect_frame(
        ssid=secrets.ssid, psk=secrets.psk, sequence=sequence
    )
    decoded = decode_duml_frame(frame)
    if not (decoded.crc8_valid and decoded.crc16_valid) or decoded.raw != frame:
        raise RuntimeError("offline Wi-Fi proposal round-trip validation failed")
    validate_command_frame(command, decoded)
    digest = hashlib.sha256(frame).hexdigest()
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    private_dir = require_private_directory(private_root / f"wifi-{stamp}")
    sanitized_dir = (sanitized_root / f"wifi-{stamp}").resolve()
    if "sanitized" not in {part.lower() for part in sanitized_dir.parts}:
        raise ValueError("sanitized proposal must be under artifacts/sanitized")
    sanitized_dir.mkdir(parents=True, exist_ok=True)
    private_payload = {
        "schema_version": 1,
        "stage": "wifi",
        "command": command.name,
        "target_address": address,
        "frame_hex": frame.hex(),
        "frame_sha256": digest,
        "max_send_count": 1,
        "automatic_retry": False,
        "automatic_follow_up": False,
        "contains_sensitive_data": True,
        "evidence": {"prepare_result_sha256": prepare_result_sha256},
        "secret_fingerprints": secrets.sanitized(),
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
    secret_summary = secrets.sanitized()
    sanitized = {
        "schema_version": 1,
        "stage": "wifi",
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
        "payload_length": len(decoded.payload),
        "ssid_masked": secret_summary["ssid_masked"],
        "ssid_sha256": secret_summary["ssid_sha256"],
        "ssid_encoded_length": secret_summary["ssid_encoded_length"],
        "psk_length": secret_summary["psk_length"],
        "psk_sha256": secret_summary["psk_sha256"],
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
        "evidence": {"prepare_result_sha256": prepare_result_sha256},
        "expected_response": (
            "same-sequence C0/07/47; public implementations conflict between "
            "two-byte and three-byte zero success payloads"
        ),
        "risk": "instructs Pocket to join the named external Wi-Fi network",
    }
    sanitized_json = sanitized_dir / "proposal.json"
    sanitized_json.write_text(
        json.dumps(sanitized, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    (sanitized_dir / "proposal.sha256").write_text(
        f"{digest}  private-proposal-frame\n", encoding="ascii"
    )
    return {
        **sanitized,
        "private_proposal": str(private_json),
        "sanitized_proposal": str(sanitized_json),
    }


def write_stream_proposal(
    *,
    address: str,
    rtmp_url: str,
    private_root: Path,
    sanitized_root: Path,
    wifi_result_sha256: str,
    server_status_sha256: str,
    sequence: int = 0x8C2C,
) -> dict:
    """Write one private 08/78 proposal without exposing its RTMP key."""

    from urllib.parse import urlsplit

    command = LIVESTREAM_COMMANDS["configure_live_stream"]
    frame = build_configure_live_stream_frame(rtmp_url=rtmp_url, sequence=sequence)
    decoded = decode_duml_frame(frame)
    validate_command_frame(command, decoded)
    if not (decoded.crc8_valid and decoded.crc16_valid) or decoded.raw != frame:
        raise RuntimeError("offline stream proposal round-trip validation failed")
    for name, digest in (
        ("wifi result", wifi_result_sha256),
        ("server status", server_status_sha256),
    ):
        if len(digest) != 64 or any(
            character not in "0123456789abcdef" for character in digest.lower()
        ):
            raise ValueError(f"{name} SHA-256 is invalid")
    parsed = urlsplit(rtmp_url)
    stream_key = parsed.path.rsplit("/", 1)[-1]
    digest = hashlib.sha256(frame).hexdigest()
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    private_dir = require_private_directory(private_root / f"stream-{stamp}")
    sanitized_dir = (sanitized_root / f"stream-{stamp}").resolve()
    if "sanitized" not in {part.lower() for part in sanitized_dir.parts}:
        raise ValueError("sanitized stream proposal must be under artifacts/sanitized")
    sanitized_dir.mkdir(parents=True, exist_ok=True)
    evidence = {
        "wifi_result_sha256": wifi_result_sha256.lower(),
        "server_status_sha256": server_status_sha256.lower(),
    }
    private_payload = {
        "schema_version": 1,
        "stage": "stream",
        "command": command.name,
        "target_address": address,
        "frame_hex": frame.hex(),
        "frame_sha256": digest,
        "max_send_count": 1,
        "automatic_retry": False,
        "automatic_follow_up": False,
        "contains_sensitive_data": True,
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "evidence": evidence,
        "stream_key_sha256": hashlib.sha256(stream_key.encode()).hexdigest(),
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
        "stage": "stream",
        "command": command.name,
        "target_address_masked": _mask_address(address),
        "target_address_sha256": hashlib.sha256(address.upper().encode()).hexdigest(),
        "source_component": "0x02",
        "target_component": "0x08",
        "sequence": f"0x{sequence:04X}",
        "flags": "0x40",
        "cmd_set": "0x08",
        "cmd_id": "0x78",
        "resolution": 720,
        "fps": 30,
        "bitrate_kbps": 4000,
        "pocket3_fixed_byte": "0x2E",
        "rtmp_target": f"rtmp://{parsed.hostname}:{parsed.port}/live/<redacted>",
        "stream_key_length": len(stream_key.encode("utf-8")),
        "stream_key_sha256": hashlib.sha256(stream_key.encode()).hexdigest(),
        "frame_sha256": digest,
        "total_length": decoded.total_length,
        "payload_length": len(decoded.payload),
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
        "evidence": evidence,
        "expected_response": "same-sequence C0/08/78 or first RTMP TCP connection",
        "risk": "may start Pocket 3 RTMP publishing to the fixed LAN endpoint",
    }
    sanitized_json = sanitized_dir / "proposal.json"
    sanitized_json.write_text(
        json.dumps(sanitized, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    (sanitized_dir / "proposal.sha256").write_text(
        f"{digest}  private-proposal-frame\n", encoding="ascii"
    )
    return {
        **sanitized,
        "private_proposal": str(private_json),
        "sanitized_proposal": str(sanitized_json),
    }


def load_fixed_stream_proposal(path: Path, *, expected_address: str) -> tuple[dict, bytes]:
    """Validate one private 08/78 proposal without rendering its URL."""

    payload = json.loads(path.read_text(encoding="utf-8"))
    required = {
        "schema_version",
        "stage",
        "command",
        "target_address",
        "frame_hex",
        "frame_sha256",
        "evidence",
        "stream_key_sha256",
    }
    if not required.issubset(payload):
        raise PermissionError("stream proposal schema is incomplete")
    if (
        payload.get("stage") != "stream"
        or payload.get("command") != "configure_live_stream"
        or payload.get("max_send_count") != 1
        or payload.get("automatic_retry") is not False
        or payload.get("automatic_follow_up") is not False
        or payload.get("contains_sensitive_data") is not True
    ):
        raise PermissionError("stream proposal single-send policy is invalid")
    if payload["target_address"].upper() != expected_address.upper():
        raise PermissionError("stream proposal Pocket address mismatch")
    raw = bytes.fromhex(payload["frame_hex"])
    if hashlib.sha256(raw).hexdigest() != payload["frame_sha256"].lower():
        raise PermissionError("stream proposal frame SHA-256 mismatch")
    for name in ("wifi_result_sha256", "server_status_sha256"):
        value = (payload.get("evidence") or {}).get(name, "").lower()
        if len(value) != 64 or any(character not in "0123456789abcdef" for character in value):
            raise PermissionError(f"stream proposal {name} is invalid")
    decoded = decode_duml_frame(raw)
    command = LIVESTREAM_COMMANDS["configure_live_stream"]
    validate_command_frame(command, decoded)
    if (
        (decoded.sender, decoded.receiver, decoded.sequence, decoded.flags)
        != (0x02, 0x08, 0x8C2C, 0x40)
        or (decoded.cmd_set, decoded.cmd_id) != (0x08, 0x78)
        or not decoded.crc8_valid
        or not decoded.crc16_valid
        or decoded.raw != raw
    ):
        raise PermissionError("stream proposal wire fields are invalid")
    url_length = int.from_bytes(decoded.payload[12:14], "little")
    url = decoded.payload[14 : 14 + url_length].decode("utf-8")
    stream_key = url.rsplit("/", 1)[-1]
    if hashlib.sha256(stream_key.encode()).hexdigest() != payload["stream_key_sha256"]:
        raise PermissionError("stream proposal key fingerprint mismatch")
    return payload, raw


def load_fixed_stream_url(path: Path, *, expected_address: str) -> str:
    """Return the private RTMP URL only after full fixed-proposal validation."""

    _payload, raw = load_fixed_stream_proposal(path, expected_address=expected_address)
    decoded = decode_duml_frame(raw)
    url_length = int.from_bytes(decoded.payload[12:14], "little")
    url_bytes = decoded.payload[14 : 14 + url_length]
    if len(url_bytes) != url_length or len(decoded.payload) != 14 + url_length:
        raise PermissionError("stream proposal URL packing is invalid")
    return url_bytes.decode("utf-8")


def write_start_transport_proposal(
    *,
    address: str,
    private_root: Path,
    sanitized_root: Path,
    configure_result_sha256: str,
    sequence: int = 0xB4BB,
) -> dict:
    """Write the fixed reversible 02/8E start candidate; never send it."""

    if len(configure_result_sha256) != 64 or any(
        character not in "0123456789abcdef" for character in configure_result_sha256.lower()
    ):
        raise ValueError("configure result SHA-256 is invalid")
    command = LIVESTREAM_COMMANDS["start_live_stream_transport"]
    frame = build_start_live_stream_transport_frame(sequence=sequence)
    decoded = decode_duml_frame(frame)
    validate_command_frame(command, decoded)
    if not (decoded.crc8_valid and decoded.crc16_valid) or decoded.raw != frame:
        raise RuntimeError("offline start-transport proposal round-trip validation failed")
    digest = hashlib.sha256(frame).hexdigest()
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    private_dir = require_private_directory(private_root / f"stream-start-{stamp}")
    sanitized_dir = (sanitized_root / f"stream-start-{stamp}").resolve()
    if "sanitized" not in {part.lower() for part in sanitized_dir.parts}:
        raise ValueError("sanitized start proposal must be under artifacts/sanitized")
    sanitized_dir.mkdir(parents=True, exist_ok=True)
    proposal = {
        "schema_version": 1,
        "stage": "stream-start",
        "command": command.name,
        "target_address": address,
        "frame_hex": frame.hex(),
        "frame_sha256": digest,
        "max_send_count": 1,
        "automatic_retry": False,
        "automatic_follow_up": False,
        "contains_sensitive_data": False,
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "evidence": {"configure_result_sha256": configure_result_sha256.lower()},
        "decoded": decoded.to_dict(),
    }
    private_json = private_dir / "proposal-private.json"
    _write_private(
        private_json,
        (json.dumps(proposal, indent=2, ensure_ascii=False) + "\n").encode(),
    )
    _write_private(private_dir / "proposal.bin", frame)
    sanitized = {
        **proposal,
        "target_address": None,
        "target_address_masked": _mask_address(address),
        "target_address_sha256": hashlib.sha256(address.upper().encode()).hexdigest(),
        "locally_sent": False,
        "reference_sources": list(command.reference_sources),
        "confidence": command.confidence,
        "risk": "requests start of the already configured RTMP transport",
    }
    sanitized.pop("target_address")
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


def load_fixed_start_transport_proposal(
    path: Path, *, expected_address: str
) -> tuple[dict, bytes]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if (
        payload.get("stage") != "stream-start"
        or payload.get("command") != "start_live_stream_transport"
        or payload.get("target_address", "").upper() != expected_address.upper()
        or payload.get("max_send_count") != 1
        or payload.get("automatic_retry") is not False
        or payload.get("automatic_follow_up") is not False
    ):
        raise PermissionError("start-transport proposal policy or address is invalid")
    raw = bytes.fromhex(payload.get("frame_hex", ""))
    if hashlib.sha256(raw).hexdigest() != payload.get("frame_sha256", "").lower():
        raise PermissionError("start-transport proposal SHA-256 mismatch")
    evidence_sha = (payload.get("evidence") or {}).get("configure_result_sha256", "")
    if len(evidence_sha) != 64:
        raise PermissionError("start-transport configure evidence is invalid")
    frame = decode_duml_frame(raw)
    command = LIVESTREAM_COMMANDS["start_live_stream_transport"]
    validate_command_frame(command, frame)
    if (
        (frame.sender, frame.receiver, frame.sequence, frame.flags)
        != (0x02, 0x08, 0xB4BB, 0x40)
        or (frame.cmd_set, frame.cmd_id, frame.payload)
        != (0x02, 0x8E, bytes.fromhex("01011a000101"))
        or not frame.crc8_valid
        or not frame.crc16_valid
    ):
        raise PermissionError("start-transport proposal wire frame is invalid")
    return payload, raw


def write_prepare_recovery_proposal(
    *,
    address: str,
    private_root: Path,
    sanitized_root: Path,
    wifi_result_sha256: str,
) -> dict:
    """Propose a two-prompt same-connection prepare recovery; never send it."""

    stage1_command = LIVESTREAM_COMMANDS["prepare_to_live_stream"]
    stage2_command = LIVESTREAM_COMMANDS["prepare_stream_transport"]
    stage1 = build_prepare_to_live_stream_frame(sequence=0xFEAB)
    stage2 = build_prepare_stream_stage2_frame(sequence=0xFFAB)
    decoded1 = decode_duml_frame(stage1)
    decoded2 = decode_duml_frame(stage2)
    for command, decoded, raw in (
        (stage1_command, decoded1, stage1),
        (stage2_command, decoded2, stage2),
    ):
        validate_command_frame(command, decoded)
        if not decoded.crc8_valid or not decoded.crc16_valid or decoded.raw != raw:
            raise RuntimeError("offline prepare-recovery round-trip validation failed")
    stage1_sha = hashlib.sha256(stage1).hexdigest()
    stage2_sha = hashlib.sha256(stage2).hexdigest()
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    private_dir = require_private_directory(private_root / f"prepare-recovery-{stamp}")
    sanitized_dir = (sanitized_root / f"prepare-recovery-{stamp}").resolve()
    if "sanitized" not in {part.lower() for part in sanitized_dir.parts}:
        raise ValueError("sanitized recovery proposal must be under artifacts/sanitized")
    sanitized_dir.mkdir(parents=True, exist_ok=True)
    stages = [
        {
            "stage": "prepare_reentry",
            "command": stage1_command.name,
            "frame_hex": stage1.hex(),
            "frame_sha256": stage1_sha,
            "decoded": decoded1.to_dict(),
            "expected_response": "same-sequence C0/02/E1 payload 00",
            "max_send_count": 1,
        },
        {
            "stage": "prepare_stream_stage2",
            "command": stage2_command.name,
            "frame_hex": stage2.hex(),
            "frame_sha256": stage2_sha,
            "decoded": decoded2.to_dict(),
            "expected_response": (
                "same-sequence 80/02/8E payload beginning 0000011C00 "
                "as recorded in the public Pocket 3 Mimo capture"
            ),
            "max_send_count": 1,
            "conditional_on_stage1_ack": True,
        },
    ]
    private_payload = {
        "schema_version": 1,
        "proposal_type": "prepare_recovery_same_connection",
        "target_address": address,
        "automatic_retry": False,
        "automatic_follow_up": False,
        "human_confirmation_required_per_frame": True,
        "wifi_retry_included": False,
        "evidence": {"prior_wifi_result_sha256": wifi_result_sha256},
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "stages": stages,
    }
    private_json = private_dir / "proposal-private.json"
    _write_private(
        private_json,
        (json.dumps(private_payload, indent=2, ensure_ascii=False) + "\n").encode(),
    )
    _write_private(private_dir / "stage1.bin", stage1)
    _write_private(private_dir / "stage2.bin", stage2)
    sanitized_stages = []
    for command, decoded, raw, stage in (
        (stage1_command, decoded1, stage1, stages[0]),
        (stage2_command, decoded2, stage2, stages[1]),
    ):
        sanitized_stages.append(
            {
                "stage": stage["stage"],
                "command": command.name,
                "source_component": f"0x{command.sender:02X}",
                "target_component": f"0x{command.receiver:02X}",
                "sequence": f"0x{decoded.sequence:04X}",
                "flags": f"0x{decoded.flags:02X}",
                "cmd_set": f"0x{command.cmd_set:02X}",
                "cmd_id": f"0x{command.cmd_id:02X}",
                "payload_hex": decoded.payload.hex(),
                "frame_hex": raw.hex(),
                "frame_sha256": stage["frame_sha256"],
                "total_length": decoded.total_length,
                "crc8_valid": decoded.crc8_valid,
                "crc16_valid": decoded.crc16_valid,
                "round_trip_valid": decoded.raw == raw,
                "expected_response": stage["expected_response"],
                "reference_sources": list(command.reference_sources),
                "confidence": command.confidence,
                "max_send_count": 1,
                "conditional_on_stage1_ack": stage.get(
                    "conditional_on_stage1_ack", False
                ),
                "locally_sent": False,
            }
        )
    sanitized = {
        "schema_version": 1,
        "proposal_type": "prepare_recovery_same_connection",
        "target_address_masked": _mask_address(address),
        "target_address_sha256": hashlib.sha256(address.upper().encode()).hexdigest(),
        "automatic_retry": False,
        "automatic_follow_up": False,
        "human_confirmation_required_per_frame": True,
        "wifi_retry_included": False,
        "locally_sent": False,
        "evidence": {"prior_wifi_result_sha256": wifi_result_sha256},
        "diagnosis": (
            "stage2 is present in djictl and a public Pocket 3 Mimo capture but "
            "absent from the earlier OpenFrameTap sequence"
        ),
        "risk": "stage2 may enter or alter the Pocket livestream-preparation state",
        "stages": sanitized_stages,
    }
    sanitized_json = sanitized_dir / "proposal.json"
    sanitized_json.write_text(
        json.dumps(sanitized, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    (sanitized_dir / "checksums.sha256").write_text(
        f"{stage1_sha}  stage1-frame\n{stage2_sha}  stage2-frame\n",
        encoding="ascii",
    )
    return {
        **sanitized,
        "private_proposal": str(private_json),
        "sanitized_proposal": str(sanitized_json),
    }
