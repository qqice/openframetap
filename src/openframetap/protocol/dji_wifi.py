"""Structured DJI Wi-Fi/UDP envelope used around DUML datagrams.

The names in this module deliberately distinguish observed structure from
unconfirmed semantics.  In particular, ``delivery_flags`` is preserved and
validated, but OpenFrameTap only emits the capture-proven zero value.
"""

from __future__ import annotations

from dataclasses import dataclass


DJI_WIFI_HEADER_LENGTH = 20
DJI_WIFI_BASIC_HEADER_LENGTH = 8
DJI_WIFI_FORMAT_NIBBLE = 0x8
DJI_WIFI_OPERATOR_COMMAND = 0x05
DJI_WIFI_HANDSHAKE = 0x00
DJI_WIFI_TARGET_PORT = 9004


class DjiWifiEnvelopeError(ValueError):
    """Raised when a Wi-Fi envelope is truncated or internally inconsistent."""


def header_xor_checksum(first_seven_bytes: bytes) -> int:
    """Return byte 7 so XOR(header[0:8]) is zero."""

    if len(first_seven_bytes) != 7:
        raise ValueError("DJI Wi-Fi checksum input must be exactly seven bytes")
    value = 0
    for item in first_seven_bytes:
        value ^= item
    return value


@dataclass(frozen=True, slots=True)
class DjiWifiBasicHeader:
    total_length: int
    format_nibble: int
    session_id: int
    transport_sequence: int
    wh_type: int
    checksum: int
    checksum_valid: bool

    @classmethod
    def parse(cls, data: bytes) -> "DjiWifiBasicHeader":
        if len(data) < DJI_WIFI_BASIC_HEADER_LENGTH:
            raise DjiWifiEnvelopeError("truncated DJI Wi-Fi basic header")
        length_and_format = int.from_bytes(data[0:2], "little")
        total_length = length_and_format & 0x0FFF
        if total_length < DJI_WIFI_BASIC_HEADER_LENGTH:
            raise DjiWifiEnvelopeError(f"invalid DJI Wi-Fi length: {total_length}")
        return cls(
            total_length=total_length,
            format_nibble=length_and_format >> 12,
            session_id=int.from_bytes(data[2:4], "little"),
            transport_sequence=int.from_bytes(data[4:6], "little"),
            wh_type=data[6],
            checksum=data[7],
            checksum_valid=header_xor_checksum(data[:7]) == data[7],
        )

    def encode(self) -> bytes:
        for name, value, maximum in (
            ("total_length", self.total_length, 0x0FFF),
            ("format_nibble", self.format_nibble, 0x0F),
            ("session_id", self.session_id, 0xFFFF),
            ("transport_sequence", self.transport_sequence, 0xFFFF),
            ("wh_type", self.wh_type, 0xFF),
        ):
            if not 0 <= value <= maximum:
                raise DjiWifiEnvelopeError(f"{name} outside 0..{maximum}")
        first_seven = (
            ((self.format_nibble << 12) | self.total_length).to_bytes(2, "little")
            + self.session_id.to_bytes(2, "little")
            + self.transport_sequence.to_bytes(2, "little")
            + bytes((self.wh_type,))
        )
        return first_seven + bytes((header_xor_checksum(first_seven),))


@dataclass(frozen=True, slots=True)
class DjiWifiEnvelope:
    """The 20-byte standard envelope observed around Mimo DUML messages."""

    total_length: int
    format_nibble: int
    session_id: int
    transport_sequence: int
    wh_type: int
    checksum: int
    checksum_valid: bool
    peer_sequence: int
    sequence_echo: int
    reserved_12_15: bytes
    message_sequence: int
    delivery_flags: int
    reserved_19: int
    payload: bytes

    @classmethod
    def parse(cls, data: bytes, *, allow_trailing: bool = False) -> "DjiWifiEnvelope":
        raw = bytes(data)
        basic = DjiWifiBasicHeader.parse(raw)
        if basic.total_length < DJI_WIFI_HEADER_LENGTH:
            raise DjiWifiEnvelopeError(
                f"standard envelope length is below {DJI_WIFI_HEADER_LENGTH}"
            )
        if len(raw) < basic.total_length:
            raise DjiWifiEnvelopeError(
                f"truncated DJI Wi-Fi envelope: expected {basic.total_length}, got {len(raw)}"
            )
        if len(raw) > basic.total_length and not allow_trailing:
            raise DjiWifiEnvelopeError(
                f"trailing bytes after DJI Wi-Fi envelope: {len(raw) - basic.total_length}"
            )
        raw = raw[: basic.total_length]
        return cls(
            total_length=basic.total_length,
            format_nibble=basic.format_nibble,
            session_id=basic.session_id,
            transport_sequence=basic.transport_sequence,
            wh_type=basic.wh_type,
            checksum=basic.checksum,
            checksum_valid=basic.checksum_valid,
            peer_sequence=int.from_bytes(raw[8:10], "little"),
            sequence_echo=int.from_bytes(raw[10:12], "little"),
            reserved_12_15=raw[12:16],
            message_sequence=int.from_bytes(raw[16:18], "little"),
            delivery_flags=raw[18],
            reserved_19=raw[19],
            payload=raw[20:],
        )

    @classmethod
    def operator_command(
        cls,
        payload: bytes,
        *,
        session_id: int,
        transport_sequence: int,
        peer_sequence: int,
        message_sequence: int,
    ) -> "DjiWifiEnvelope":
        """Build the only outbound standard form permitted in this phase.

        The four reserved bytes, capture-optional delivery flag, and final
        reserved byte are policy fixed to their dominant captured values.
        Callers cannot supply or mutate them.
        """

        payload = bytes(payload)
        total_length = DJI_WIFI_HEADER_LENGTH + len(payload)
        if total_length > 0x0FFF:
            raise DjiWifiEnvelopeError("DJI Wi-Fi envelope exceeds 12-bit length")
        return cls(
            total_length=total_length,
            format_nibble=DJI_WIFI_FORMAT_NIBBLE,
            session_id=session_id,
            transport_sequence=transport_sequence,
            wh_type=DJI_WIFI_OPERATOR_COMMAND,
            checksum=0,
            checksum_valid=True,
            peer_sequence=peer_sequence,
            sequence_echo=transport_sequence,
            reserved_12_15=b"\x00" * 4,
            message_sequence=message_sequence,
            delivery_flags=0,
            reserved_19=0,
            payload=payload,
        )

    def encode(self) -> bytes:
        if self.total_length != DJI_WIFI_HEADER_LENGTH + len(self.payload):
            raise DjiWifiEnvelopeError("envelope total length does not match payload")
        if len(self.reserved_12_15) != 4:
            raise DjiWifiEnvelopeError("reserved_12_15 must be exactly four bytes")
        for name, value in (
            ("peer_sequence", self.peer_sequence),
            ("sequence_echo", self.sequence_echo),
            ("message_sequence", self.message_sequence),
        ):
            if not 0 <= value <= 0xFFFF:
                raise DjiWifiEnvelopeError(f"{name} outside uint16")
        for name, value in (
            ("delivery_flags", self.delivery_flags),
            ("reserved_19", self.reserved_19),
        ):
            if not 0 <= value <= 0xFF:
                raise DjiWifiEnvelopeError(f"{name} outside uint8")
        basic = DjiWifiBasicHeader(
            self.total_length,
            self.format_nibble,
            self.session_id,
            self.transport_sequence,
            self.wh_type,
            0,
            True,
        ).encode()
        return (
            basic
            + self.peer_sequence.to_bytes(2, "little")
            + self.sequence_echo.to_bytes(2, "little")
            + self.reserved_12_15
            + self.message_sequence.to_bytes(2, "little")
            + bytes((self.delivery_flags, self.reserved_19))
            + self.payload
        )

    def validate_operator_policy(self) -> None:
        """Fail closed unless this is the exact reviewed outbound form."""

        if self.format_nibble != DJI_WIFI_FORMAT_NIBBLE:
            raise DjiWifiEnvelopeError("unreviewed Wi-Fi format nibble")
        if self.wh_type != DJI_WIFI_OPERATOR_COMMAND:
            raise DjiWifiEnvelopeError("only operator-command WhType 05 is allowed")
        if self.sequence_echo != self.transport_sequence:
            raise DjiWifiEnvelopeError("transport sequence echo mismatch")
        if self.reserved_12_15 != b"\x00" * 4:
            raise DjiWifiEnvelopeError("reserved bytes 12..15 are not capture-verified zeros")
        if self.delivery_flags != 0:
            raise DjiWifiEnvelopeError("nonzero delivery flags are not allowed for generation")
        if self.reserved_19 != 0:
            raise DjiWifiEnvelopeError("reserved byte 19 must remain zero")

    def to_dict(self) -> dict:
        return {
            "total_length": self.total_length,
            "format_nibble": self.format_nibble,
            "session_id": self.session_id,
            "transport_sequence": self.transport_sequence,
            "wh_type": self.wh_type,
            "checksum": self.checksum,
            "checksum_valid": self.checksum_valid,
            "peer_sequence": self.peer_sequence,
            "sequence_echo": self.sequence_echo,
            "reserved_12_15_hex": self.reserved_12_15.hex(),
            "message_sequence": self.message_sequence,
            "delivery_flags": self.delivery_flags,
            "reserved_19": self.reserved_19,
            "payload_length": len(self.payload),
            "payload_hex": self.payload.hex(),
        }


@dataclass(slots=True)
class DjiWifiOperatorSequencer:
    """Generate capture-conformant transport and message sequences."""

    session_id: int
    next_transport_sequence: int
    peer_sequence: int
    next_message_counter: int = 1

    def build(self, payload: bytes) -> DjiWifiEnvelope:
        if not 0 <= self.next_message_counter <= 0xFF:
            raise DjiWifiEnvelopeError("message counter outside uint8")
        envelope = DjiWifiEnvelope.operator_command(
            payload,
            session_id=self.session_id,
            transport_sequence=self.next_transport_sequence,
            peer_sequence=self.peer_sequence,
            message_sequence=0x0100 | self.next_message_counter,
        )
        self.next_transport_sequence = (self.next_transport_sequence + 8) & 0xFFFF
        self.next_message_counter = (self.next_message_counter + 1) & 0xFF
        return envelope

    def observe_peer_sequence(self, sequence: int) -> bool:
        if not 0 <= sequence <= 0xFFFF:
            raise DjiWifiEnvelopeError("peer sequence outside uint16")
        delta = (sequence - self.peer_sequence) & 0xFFFF
        if delta == 0 or delta >= 0x8000:
            return False
        self.peer_sequence = sequence
        return True
