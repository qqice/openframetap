"""Pocket 3 normal-mode command profile; no recording or gimbal commands."""

from dataclasses import dataclass

from openframetap.protocol.commands import CommandDefinition, CommandRejected
from openframetap.protocol.duml import decode_duml_frame, encode_duml_frame

SOURCE = "Mimo Pocket 3 PCAP SHA256 8e7c7eb62acd32429bd0d176e3da2b7d1f688854a3ee79c4017eb631139a181c"
# Literal payloads below are field-labelled protocol defaults, not complete frames.
NORMAL_FIELDS = {
    "normal_session_open": (0xF0, 0x00, 0x2B, bytes((4, 0))),
    "normal_session_keepalive": (0xF0, 0x00, 0x2B, bytes((1, 1))),
    "normal_get_ssid": (0x07, 0x07, 0x07, b""),
    "normal_get_password": (0x07, 0x07, 0x0E, b""),
    # App-presence record from the first accepted Pocket 3 Mimo datalink session.
    # Version/capability prefix (5 bytes), APP identity, 5 reserved bytes, type 2.
    "normal_app_presence": (0x28, 0x00, 0x88, bytes((0x17,0,0,0x23,0)) + b'APP' + bytes(5) + bytes((2,))),
    # mode=0, profile=4, stream=2, seven reserved bytes; captured receiver=0x41.
    "normal_live_enable": (0x41, 0x09, 0xA8, bytes((0, 4, 2)) + bytes(7)),
}
NORMAL_COMMANDS = {
    name: CommandDefinition(
        name, 0x02, receiver, cmd_set, cmd_id,
        "fixed normal-mode profile payload " + payload.hex(), True, True,
        (SOURCE, "OpenPocketCine e8f272e Commands.swift; Osmosis OsmoCommands.kt"),
        "high" if name != "normal_session_keepalive" else "medium",
    )
    for name, (receiver, cmd_set, cmd_id, payload) in NORMAL_FIELDS.items()
}


def build_normal_frame(name: str, sequence: int) -> bytes:
    receiver, cmd_set, cmd_id, payload = NORMAL_FIELDS[name]
    return encode_duml_frame(sender=2, receiver=receiver, sequence=sequence,
                             flags=0x40, cmd_set=cmd_set, cmd_id=cmd_id, payload=payload)


def validate_normal_frame(name: str, frame) -> None:
    if name not in NORMAL_FIELDS:
        raise CommandRejected("unknown normal-mode command")
    receiver, cmd_set, cmd_id, payload = NORMAL_FIELDS[name]
    if (frame.sender, frame.receiver, frame.cmd_set, frame.cmd_id, frame.flags, frame.payload) != (
        2, receiver, cmd_set, cmd_id, 0x40, payload
    ) or not (frame.crc8_valid and frame.crc16_valid):
        raise CommandRejected("normal-mode frame differs from its fixed profile")


def parse_wifi_string(payload: bytes, *, password: bool = False) -> str:
    if len(payload) < 2 or payload[0] != 0:
        raise ValueError("Pocket refused Wi-Fi credential query")
    length = payload[1]
    if len(payload) != length + 2:
        raise ValueError("Wi-Fi credential response length mismatch")
    value = payload[2:].decode("utf-8", errors="strict")
    if any(ord(c) < 32 or ord(c) == 127 for c in value):
        raise ValueError("Wi-Fi credential contains control characters")
    if password:
        if not (8 <= len(value) <= 63 or (len(value) == 64 and all(c in '0123456789abcdefABCDEF' for c in value))):
            raise ValueError("Wi-Fi password has invalid WPA length")
    elif not 1 <= length <= 32 or not value.lower().startswith('osmopocket3'):
        raise ValueError("SSID is not the selected Pocket 3 hotspot")
    return value


@dataclass(frozen=True, repr=False)
class SoftAPCredentials:
    ssid: str
    password: str

    def __repr__(self):
        return "SoftAPCredentials(<redacted>)"


def registration_reply(request) -> bytes:
    """Answer only captured 48->02 device-info challenges; no unsolicited writes."""
    if (request.sender != 0x48 or request.receiver != 2 or request.cmd_set != 0
            or request.cmd_id not in (0x81,0x82) or request.flags != 0x40
            or len(request.payload) != 64 or not request.crc8_valid or not request.crc16_valid):
        raise CommandRejected('not a Pocket 3 application-registration challenge')
    if request.cmd_id == 0x81:
        payload = bytearray(64)
        payload[1:4] = b'APP'
        payload[34] = 2
        payload[41:43] = bytes((2,8))
    else:
        payload = bytes(1)
    return encode_duml_frame(sender=2, receiver=0x48, sequence=request.sequence,
                             flags=0x80, cmd_set=0, cmd_id=request.cmd_id, payload=payload)
