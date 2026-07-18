"""Offline pairing proposal generation; active writes require a separate approval."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from openframetap.devices.pocket3 import (
    REFERENCE_PAIRING_IDENTIFIER,
    REFERENCE_PAIRING_PIN,
    build_set_pairing_pin_frame,
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
