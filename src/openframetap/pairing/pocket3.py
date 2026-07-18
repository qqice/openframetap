"""Offline pairing proposal generation; active writes require a separate approval."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from openframetap.devices.pocket3 import (
    REFERENCE_PAIRING_IDENTIFIER,
    REFERENCE_PAIRING_PIN,
    build_set_pairing_pin_frame,
    build_pairing_stage1_frame,
)
from openframetap.protocol.duml import decode_duml_frame


def pairing_proposal(*, pin: str = REFERENCE_PAIRING_PIN) -> tuple[bytes, dict]:
    raw = build_set_pairing_pin_frame(pin=pin)
    decoded = decode_duml_frame(raw)
    payload = {
        "status": "proposal_only_not_transmitted",
        "command": "set_pairing_pin",
        "frame_sha256": hashlib.sha256(raw).hexdigest(),
        "frame_hex": raw.hex(),
        "decoded": decoded.to_dict(),
        "payload_fields": [
            {
                "offset": 0,
                "length": 1,
                "meaning": "identifier UTF-8 length",
                "value_hex": f"{len(REFERENCE_PAIRING_IDENTIFIER):02x}",
            },
            {
                "offset": 1,
                "length": len(REFERENCE_PAIRING_IDENTIFIER),
                "meaning": "reference-derived identifier; not device-unique proof",
                "value": REFERENCE_PAIRING_IDENTIFIER,
            },
            {
                "offset": 1 + len(REFERENCE_PAIRING_IDENTIFIER),
                "length": 1,
                "meaning": "PIN UTF-8 length",
                "value_hex": f"{len(pin):02x}",
            },
            {
                "offset": 2 + len(REFERENCE_PAIRING_IDENTIFIER),
                "length": len(pin),
                "meaning": "reference-default PIN",
                "value": pin,
            },
        ],
        "source": {
            "primary": "xaionaro-go/djictl pairing implementation",
            "corroborating": "yigitkonur/lib-osmo-ble derived flow",
            "capture_support": "command/address/response shape only; request payload not captured",
        },
        "confidence": "medium: public payload variants conflict",
        "expected_effect": "request DJI application-layer pairing status; may show Pocket confirmation",
        "risk": "writes FFF5 and may alter DJI application pairing state; no Wi-Fi, camera, video, or gimbal command",
    }
    return raw, payload


def write_pairing_proposal(directory: Path, *, pin: str = REFERENCE_PAIRING_PIN) -> dict:
    raw, payload = pairing_proposal(pin=pin)
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "proposed-pairing-frame.bin").write_bytes(raw)
    (directory / "proposed-pairing-frame.txt").write_text(raw.hex() + "\n", encoding="ascii")
    (directory / "proposed-pairing-frame.json").write_text(
        json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    return payload


def pairing_stage1_proposal(
    *, required_approval_raw: bytes, source: dict
) -> tuple[bytes, dict]:
    approval = decode_duml_frame(required_approval_raw)
    if not (
        approval.crc8_valid
        and approval.crc16_valid
        and approval.sender == 0x07
        and approval.receiver == 0x02
        and approval.flags == 0x40
        and approval.cmd_set == 0x07
        and approval.cmd_id == 0x46
        and approval.payload == b"\x01"
    ):
        raise ValueError("required frame is not an exact Pocket pairing approval")
    raw = build_pairing_stage1_frame(sequence=approval.sequence)
    decoded = decode_duml_frame(raw)
    payload = {
        "status": "proposal_only_not_transmitted",
        "command": "pairing_stage1_ack",
        "frame_sha256": hashlib.sha256(raw).hexdigest(),
        "frame_hex": raw.hex(),
        "decoded": decoded.to_dict(),
        "required_incoming_frame": {
            "sha256": hashlib.sha256(required_approval_raw).hexdigest(),
            "hex": required_approval_raw.hex(),
            "decoded": approval.to_dict(),
        },
        "sequence_basis": (
            "mirror the exact approval request sequence; runtime refuses to write unless "
            "the complete prerequisite frame is reobserved in the new connection"
        ),
        "source": source,
        "reference_sources": [
            "xaionaro/reverse-engineering-dji captured 400746 request followed by mirrored C00746 payload 00",
            "xaionaro-go/djictl GetMessagePairingStage1",
        ],
        "confidence": (
            "high for ACK structure; session-specific sequence guarded by exact live prerequisite"
        ),
        "expected_effect": "acknowledge one repeated Pocket pairing approval request",
        "risk": (
            "changes DJI application pairing state; an absent or changed prerequisite results "
            "in zero writes; no stage2 or other frame follows automatically"
        ),
    }
    return raw, payload


def write_pairing_stage1_proposal(
    directory: Path, *, required_approval_raw: bytes, source: dict
) -> dict:
    raw, payload = pairing_stage1_proposal(
        required_approval_raw=required_approval_raw,
        source=source,
    )
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "proposed-pairing-stage1-frame.bin").write_bytes(raw)
    (directory / "proposed-pairing-stage1-frame.txt").write_text(
        raw.hex() + "\n", encoding="ascii"
    )
    (directory / "proposed-pairing-stage1-frame.json").write_text(
        json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    (directory / "proposed-pairing-stage1-prerequisite.bin").write_bytes(
        required_approval_raw
    )
    return payload
