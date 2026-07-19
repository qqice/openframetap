"""Capture-derived DJI Wi-Fi media fragment structures.

The names in this module intentionally stop at observed framing.  In
particular, ``frame_flags`` and ``metadata_raw`` are preserved without
assigning an undocumented camera semantic.
"""

from __future__ import annotations

from dataclasses import dataclass

from openframetap.protocol.dji_wifi import (
    DJI_WIFI_FORMAT_NIBBLE,
    DjiWifiBasicHeader,
    DjiWifiEnvelopeError,
)


DJI_WIFI_MEDIA_TYPE = 0x02
DJI_WIFI_MEDIA_OUTER_HEADER_LENGTH = 20
DJI_WIFI_MEDIA_ACCESS_UNIT_HEADER_LENGTH = 16
DJI_WIFI_MEDIA_MAGIC = b"\x00\x00\x01\xff"


class DjiWifiMediaError(DjiWifiEnvelopeError):
    """Raised when a captured Wi-Fi media fragment is structurally invalid."""


@dataclass(frozen=True, slots=True)
class DjiWifiMediaFragment:
    """One WhType 02 UDP media fragment.

    Captures show the fragment number split between bit 7 of byte 17 and the
    low five bits of byte 18.  The low three bits of the transport sequence
    distinguish the original send (0) from byte-identical retransmissions
    (2/4/6), but are exposed as an observed generation rather than a protocol
    promise.
    """

    basic: DjiWifiBasicHeader
    transport_metadata: bytes
    frame_id: int
    fragment_count: int
    fragment_index: int
    frame_flags: int
    reserved_19: int
    payload: bytes

    @classmethod
    def parse(cls, data: bytes) -> "DjiWifiMediaFragment":
        raw = bytes(data)
        basic = DjiWifiBasicHeader.parse(raw)
        if basic.wh_type != DJI_WIFI_MEDIA_TYPE:
            raise DjiWifiMediaError("DJI Wi-Fi packet is not WhType 02 media")
        if basic.format_nibble != DJI_WIFI_FORMAT_NIBBLE:
            raise DjiWifiMediaError(
                f"unexpected DJI Wi-Fi media format nibble: {basic.format_nibble}"
            )
        if not basic.checksum_valid:
            raise DjiWifiMediaError("invalid DJI Wi-Fi media header checksum")
        if basic.total_length < DJI_WIFI_MEDIA_OUTER_HEADER_LENGTH:
            raise DjiWifiMediaError("truncated DJI Wi-Fi media header")
        if len(raw) != basic.total_length:
            raise DjiWifiMediaError(
                "DJI Wi-Fi media UDP length does not match its basic header"
            )
        fragment_count = raw[17] & 0x7F
        if fragment_count == 0:
            raise DjiWifiMediaError("DJI Wi-Fi media fragment count is zero")
        if fragment_count > 64:
            raise DjiWifiMediaError(
                "DJI Wi-Fi media fragment count exceeds the observed 6-bit index space"
            )
        fragment_index = 2 * (raw[18] & 0x1F) + (raw[17] >> 7)
        if fragment_index >= fragment_count:
            raise DjiWifiMediaError(
                f"media fragment index {fragment_index} outside count {fragment_count}"
            )
        return cls(
            basic=basic,
            transport_metadata=raw[8:16],
            frame_id=raw[16],
            fragment_count=fragment_count,
            fragment_index=fragment_index,
            frame_flags=raw[18] & 0xE0,
            reserved_19=raw[19],
            payload=raw[DJI_WIFI_MEDIA_OUTER_HEADER_LENGTH:],
        )

    @property
    def retransmission_generation(self) -> int:
        """Return the capture-observed transport-sequence low three bits."""

        return self.basic.transport_sequence & 0x07

    @property
    def is_access_unit_start(self) -> bool:
        return (
            self.fragment_index == 0
            and len(self.payload) >= DJI_WIFI_MEDIA_ACCESS_UNIT_HEADER_LENGTH
            and self.payload.startswith(DJI_WIFI_MEDIA_MAGIC)
        )


@dataclass(frozen=True, slots=True)
class DjiWifiMediaAccessUnitHeader:
    magic: bytes
    declared_length: int
    metadata_raw: bytes
    timestamp_ms_candidate: int

    @classmethod
    def parse(cls, data: bytes) -> "DjiWifiMediaAccessUnitHeader":
        raw = bytes(data)
        if len(raw) < DJI_WIFI_MEDIA_ACCESS_UNIT_HEADER_LENGTH:
            raise DjiWifiMediaError("truncated DJI Wi-Fi media access-unit header")
        if raw[:4] != DJI_WIFI_MEDIA_MAGIC:
            raise DjiWifiMediaError("invalid DJI Wi-Fi media access-unit magic")
        declared_length = int.from_bytes(raw[4:8], "little")
        if declared_length == 0:
            raise DjiWifiMediaError("DJI Wi-Fi media access unit has zero length")
        return cls(
            magic=raw[:4],
            declared_length=declared_length,
            metadata_raw=raw[8:12],
            timestamp_ms_candidate=int.from_bytes(raw[12:16], "little"),
        )
