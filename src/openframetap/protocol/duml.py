"""Encoding and decoding for the captured DJI DUML wire format."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from openframetap.protocol.crc import crc8_dji, crc16_dji

MAGIC = 0x55
MIN_FRAME_LENGTH = 13
MAX_FRAME_LENGTH = 0x3FF


class DumlDecodeError(ValueError):
    """Raised when bytes cannot be represented as a complete DUML frame."""


def _hex(value: int, width: int = 2) -> str:
    return f"0x{value:0{width}X}"


@dataclass(frozen=True, slots=True)
class DumlFrame:
    total_length: int
    version: int
    length_version_raw: int
    sender: int
    receiver: int
    sequence: int
    flags: int
    cmd_set: int
    cmd_id: int
    payload: bytes
    crc8_expected: int
    crc8_actual: int
    crc8_valid: bool
    crc16_expected: int
    crc16_actual: int
    crc16_valid: bool
    raw: bytes

    @property
    def ack_type(self) -> str:
        if self.flags & 0x80:
            return "response_ack" if self.flags & 0x40 else "response"
        return "request_ack_required" if self.flags & 0x40 else "request_or_notification"

    @property
    def encryption(self) -> int:
        """Preserve the low three flag bits customarily used for encryption."""

        return self.flags & 0x07

    @property
    def unknown_flag_bits(self) -> int:
        return self.flags & 0x38

    def to_dict(self) -> dict[str, Any]:
        return {
            "total_length": self.total_length,
            "version": self.version,
            "length_version_raw": _hex(self.length_version_raw),
            "sender": _hex(self.sender),
            "receiver": _hex(self.receiver),
            "sequence": self.sequence,
            "sequence_hex": _hex(self.sequence, 4),
            "flags": _hex(self.flags),
            "ack_type": self.ack_type,
            "encryption": self.encryption,
            "unknown_flag_bits": _hex(self.unknown_flag_bits),
            "cmd_set": _hex(self.cmd_set),
            "cmd_id": _hex(self.cmd_id),
            "payload_hex": self.payload.hex(),
            "crc8": {
                "expected": _hex(self.crc8_expected),
                "actual": _hex(self.crc8_actual),
                "valid": self.crc8_valid,
            },
            "crc16": {
                "expected": _hex(self.crc16_expected, 4),
                "actual": _hex(self.crc16_actual, 4),
                "valid": self.crc16_valid,
            },
            "raw_hex": self.raw.hex(),
        }


def encode_duml_frame(
    *,
    sender: int,
    receiver: int,
    sequence: int,
    flags: int,
    cmd_set: int,
    cmd_id: int,
    payload: bytes = b"",
    version: int = 1,
) -> bytes:
    """Encode structured values; the sequence field is big-endian on the wire."""

    for name, value, maximum in (
        ("sender", sender, 0xFF),
        ("receiver", receiver, 0xFF),
        ("sequence", sequence, 0xFFFF),
        ("flags", flags, 0xFF),
        ("cmd_set", cmd_set, 0xFF),
        ("cmd_id", cmd_id, 0xFF),
        ("version", version, 0x3F),
    ):
        if not 0 <= value <= maximum:
            raise ValueError(f"{name} outside 0..{maximum}")
    payload = bytes(payload)
    total_length = MIN_FRAME_LENGTH + len(payload)
    if total_length > MAX_FRAME_LENGTH:
        raise ValueError(f"DUML frame exceeds {MAX_FRAME_LENGTH} bytes")

    header = bytearray((MAGIC, total_length & 0xFF, (version << 2) | (total_length >> 8)))
    header.append(crc8_dji(bytes(header)))
    body = bytes(
        (
            sender,
            receiver,
            (sequence >> 8) & 0xFF,
            sequence & 0xFF,
            flags,
            cmd_set,
            cmd_id,
        )
    ) + payload
    without_crc16 = bytes(header) + body
    return without_crc16 + crc16_dji(without_crc16).to_bytes(2, "little")


def decode_duml_frame(data: bytes, *, allow_trailing: bool = False) -> DumlFrame:
    raw_input = bytes(data)
    if len(raw_input) < 4:
        raise DumlDecodeError("truncated DUML header")
    if raw_input[0] != MAGIC:
        raise DumlDecodeError(f"invalid DUML magic: {_hex(raw_input[0])}")
    total_length = raw_input[1] | ((raw_input[2] & 0x03) << 8)
    if not MIN_FRAME_LENGTH <= total_length <= MAX_FRAME_LENGTH:
        raise DumlDecodeError(f"invalid DUML total length: {total_length}")
    if len(raw_input) < total_length:
        raise DumlDecodeError(f"truncated DUML frame: expected {total_length}, got {len(raw_input)}")
    if len(raw_input) > total_length and not allow_trailing:
        raise DumlDecodeError(f"trailing bytes after DUML frame: {len(raw_input) - total_length}")

    raw = raw_input[:total_length]
    crc8_expected = raw[3]
    crc8_actual = crc8_dji(raw[:3])
    crc16_expected = int.from_bytes(raw[-2:], "little")
    crc16_actual = crc16_dji(raw[:-2])
    return DumlFrame(
        total_length=total_length,
        version=raw[2] >> 2,
        length_version_raw=raw[2],
        sender=raw[4],
        receiver=raw[5],
        sequence=int.from_bytes(raw[6:8], "big"),
        flags=raw[8],
        cmd_set=raw[9],
        cmd_id=raw[10],
        payload=raw[11:-2],
        crc8_expected=crc8_expected,
        crc8_actual=crc8_actual,
        crc8_valid=crc8_expected == crc8_actual,
        crc16_expected=crc16_expected,
        crc16_actual=crc16_actual,
        crc16_valid=crc16_expected == crc16_actual,
        raw=raw,
    )
