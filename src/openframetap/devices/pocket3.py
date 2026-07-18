"""Pocket 3 profile and pairing-frame proposals.

Nothing in this module sends bytes.  The identifier/PIN payload is intentionally
labelled reference-derived because public implementations conflict.
"""

from __future__ import annotations

from openframetap.devices.base import DeviceProfile
from openframetap.protocol.commands import PAIRING_COMMANDS
from openframetap.protocol.duml import encode_duml_frame

POCKET3_PROFILE = DeviceProfile(
    name="DJI Osmo Pocket 3",
    service_uuid="0000fff0-0000-1000-8000-00805f9b34fb",
    notification_uuid="0000fff4-0000-1000-8000-00805f9b34fb",
    write_uuid="0000fff5-0000-1000-8000-00805f9b34fb",
    default_address="E4:7A:2C:36:DC:FC",
)

REFERENCE_PAIRING_IDENTIFIER = "001749319286102"
REFERENCE_PAIRING_PIN = "5160"


def pack_reference_string(value: str) -> bytes:
    """DJI packed-string form used by djictl: uint8 length then UTF-8 bytes."""

    encoded = value.encode("utf-8")
    if len(encoded) > 0xFF:
        raise ValueError("packed string exceeds 255 bytes")
    return bytes((len(encoded),)) + encoded


def build_set_pairing_pin_frame(
    *,
    sequence: int = 0x72AA,
    pin: str = REFERENCE_PAIRING_PIN,
    identifier: str = REFERENCE_PAIRING_IDENTIFIER,
) -> bytes:
    """Build, but never transmit, the medium-confidence reference proposal."""

    command = PAIRING_COMMANDS["set_pairing_pin"]
    payload = pack_reference_string(identifier) + pack_reference_string(pin)
    return encode_duml_frame(
        sender=command.sender,
        receiver=command.receiver,
        sequence=sequence,
        flags=0x40,
        cmd_set=command.cmd_set,
        cmd_id=command.cmd_id,
        payload=payload,
    )


def build_pairing_stage1_frame(*, sequence: int = 0x0400) -> bytes:
    command = PAIRING_COMMANDS["pairing_stage1_ack"]
    return encode_duml_frame(
        sender=command.sender,
        receiver=command.receiver,
        sequence=sequence,
        flags=0xC0,
        cmd_set=command.cmd_set,
        cmd_id=command.cmd_id,
        payload=b"\x00",
    )


def build_pairing_stage2_frame(*, sequence: int = 0x74AA) -> bytes:
    command = PAIRING_COMMANDS["pairing_stage2"]
    return encode_duml_frame(
        sender=command.sender,
        receiver=command.receiver,
        sequence=sequence,
        flags=0x40,
        cmd_set=command.cmd_set,
        cmd_id=command.cmd_id,
        payload=b"11\x00\x00\x00",
    )
