"""Conservative decoder: unknown and low-confidence payloads stay intact."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from openframetap.protocol.duml import DumlFrame


@dataclass(frozen=True, slots=True)
class TelemetryDecode:
    message_type: str
    known: bool
    confidence: str
    evidence_class: str
    fields: dict[str, Any]
    raw_payload_hex: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "message_type": self.message_type,
            "known": self.known,
            "confidence": self.confidence,
            "evidence_class": self.evidence_class,
            "fields": self.fields,
            "raw_payload_hex": self.raw_payload_hex,
        }


def _ascii_prefix(payload: bytes) -> str | None:
    prefix = payload.split(b"\x00", 1)[0]
    if prefix and all(0x20 <= item < 0x7F for item in prefix):
        return prefix.decode("ascii")
    return None


def decode_telemetry(frame: DumlFrame) -> TelemetryDecode:
    raw = frame.payload.hex()
    key = (frame.cmd_set, frame.cmd_id)
    if key == (0x07, 0x45) and frame.flags == 0xC0 and frame.payload in {
        b"\x00\x01",
        b"\x00\x02",
    }:
        return TelemetryDecode(
            "pairing_status",
            True,
            "high",
            "local hardware fact",
            {
                "status": (
                    "already_paired" if frame.payload[1] == 1 else "confirmation_required"
                )
            },
            raw,
        )
    if key == (0x07, 0x46) and frame.flags == 0x40 and frame.payload == b"\x01":
        return TelemetryDecode(
            "pairing_approval_request",
            True,
            "high",
            "local hardware fact",
            {"pocket_screen_confirmation_observed": True},
            raw,
        )
    if key == (0x02, 0x80):
        return TelemetryDecode(
            "camera_status_02_80_candidate",
            True,
            "low",
            "local capture correction",
            {
                "semantic_fields_parsed": False,
                "pairing_started_interpretation_rejected": True,
            },
            raw,
        )
    if key == (0x00, 0x81):
        return TelemetryDecode(
            "device_info_candidate",
            True,
            "medium",
            "capture inference",
            {"model_or_product_ascii": _ascii_prefix(frame.payload)},
            raw,
        )
    if key == (0x0D, 0x02) and len(frame.payload) >= 21:
        percentage = frame.payload[20]
        plausible = percentage <= 100
        return TelemetryDecode(
            "battery_status_candidate",
            plausible,
            "medium" if plausible else "low",
            "reference implementation conclusion",
            {"battery_percent_candidate": percentage, "plausible": plausible},
            raw,
        )
    if key == (0x04, 0x05):
        return TelemetryDecode(
            "gimbal_status_candidate",
            True,
            "low",
            "capture inference",
            {"semantic_fields_parsed": False},
            raw,
        )
    return TelemetryDecode(
        f"unknown_{frame.cmd_set:02x}_{frame.cmd_id:02x}",
        False,
        "unknown",
        "unclassified",
        {},
        raw,
    )
