"""Reference-derived Pocket 3 livestream commands; this module never sends."""

from __future__ import annotations

from openframetap.protocol.commands import CommandDefinition
from openframetap.protocol.duml import encode_duml_frame


LIVESTREAM_COMMANDS = {
    "prepare_to_live_stream": CommandDefinition(
        name="prepare_to_live_stream",
        sender=0x02,
        receiver=0x08,
        cmd_set=0x02,
        cmd_id=0xE1,
        payload_schema="one fixed byte 1A",
        ack_required=True,
        allowed_in_current_phase=True,
        reference_sources=(
            "node-osmo DjiPreparingToLivestreamMessagePayload",
            "Moblin DjiPreparingToLivestreamMessagePayload",
            "djictl GetMessagePayloadPrepareToLiveStreamStage1",
            "reverse-engineering-dji captured C0/02/E1 payload 00 response",
        ),
        confidence="high-reference-not-local-hardware",
    ),
    "prepare_stream_transport": CommandDefinition(
        name="prepare_stream_transport",
        sender=0x02,
        receiver=0x08,
        cmd_set=0x02,
        cmd_id=0x8E,
        payload_schema="fixed bytes 00 01 1C 00",
        ack_required=True,
        allowed_in_current_phase=False,
        reference_sources=(
            "djictl PrepareToLiveStream stage2",
            "reverse-engineering-dji captured 40/02/8E payload 00011C00",
        ),
        confidence="medium-reference-flow-conflict",
        danger="separate unapproved prepare-stage2 command type",
    ),
    "wifi_connect": CommandDefinition(
        name="wifi_connect",
        sender=0x02,
        receiver=0x07,
        cmd_set=0x07,
        cmd_id=0x47,
        payload_schema="packed SSID string + packed PSK string",
        ack_required=True,
        allowed_in_current_phase=True,
        reference_sources=(
            "node-osmo DjiSetupWifiMessagePayload",
            "Moblin DjiSetupWifiMessagePayload",
            "djictl GetMessagePayloadConnectToWiFi",
            "reverse-engineering-dji captured C0/07/47 response",
        ),
        confidence="high-reference-not-local-hardware",
        danger=None,
    ),
    "configure_live_stream": CommandDefinition(
        name="configure_live_stream",
        sender=0x02,
        receiver=0x08,
        cmd_set=0x08,
        cmd_id=0x78,
        payload_schema="quality fields + packed RTMP URL; contains stream key",
        ack_required=True,
        allowed_in_current_phase=True,
        reference_sources=(
            "node-osmo DjiStartStreamingMessagePayload",
            "Moblin DjiStartStreamingMessagePayload",
            "djictl GetMessagePayloadConfigureLiveStream (fixed-byte conflict)",
        ),
        confidence="medium-reference-flow-conflict",
    ),
    "start_live_stream_transport": CommandDefinition(
        name="start_live_stream_transport",
        sender=0x02,
        receiver=0x08,
        cmd_set=0x02,
        cmd_id=0x8E,
        payload_schema="fixed bytes 01 01 1A 00 01 01",
        ack_required=True,
        allowed_in_current_phase=False,
        reference_sources=("djictl RequestStartLiveStream",),
        confidence="low-flow-conflict",
        danger="separate unapproved start command absent from Pocket 3 node-osmo/Moblin flow",
    ),
    "stop_live_stream": CommandDefinition(
        name="stop_live_stream",
        sender=0x02,
        receiver=0x08,
        cmd_set=0x02,
        cmd_id=0x8E,
        payload_schema="fixed bytes 01 01 1A 00 01 02",
        ack_required=True,
        allowed_in_current_phase=False,
        reference_sources=("node-osmo", "Moblin"),
        confidence="medium-reference-not-local-hardware",
        danger="stop is separately user-gated and not needed yet",
    ),
}


def build_prepare_to_live_stream_frame(*, sequence: int = 0x8C12) -> bytes:
    command = LIVESTREAM_COMMANDS["prepare_to_live_stream"]
    return encode_duml_frame(
        sender=command.sender,
        receiver=command.receiver,
        sequence=sequence,
        flags=0x40,
        cmd_set=command.cmd_set,
        cmd_id=command.cmd_id,
        payload=b"\x1A",
    )


def _pack_dji_string(value: str, *, field_name: str) -> bytes:
    encoded = value.encode("utf-8")
    if not encoded or len(encoded) > 0xFF:
        raise ValueError(f"{field_name} encoded length must be 1..255 bytes")
    return bytes((len(encoded),)) + encoded


def build_wifi_connect_frame(
    *, ssid: str, psk: str, sequence: int = 0x8C19
) -> bytes:
    """Build the reference-derived 07/47 frame without logging its payload."""

    ssid_bytes = ssid.encode("utf-8")
    psk_bytes = psk.encode("utf-8")
    if not 1 <= len(ssid_bytes) <= 32:
        raise ValueError("SSID encoded length must be 1..32 bytes")
    if not 8 <= len(psk_bytes) <= 63:
        raise ValueError("Wi-Fi PSK encoded length must be 8..63 bytes")
    command = LIVESTREAM_COMMANDS["wifi_connect"]
    payload = _pack_dji_string(ssid, field_name="SSID") + _pack_dji_string(
        psk, field_name="Wi-Fi PSK"
    )
    return encode_duml_frame(
        sender=command.sender,
        receiver=command.receiver,
        sequence=sequence,
        flags=0x40,
        cmd_set=command.cmd_set,
        cmd_id=command.cmd_id,
        payload=payload,
    )


def build_prepare_stream_stage2_frame(*, sequence: int = 0xFFAB) -> bytes:
    """Build the Mimo-captured prepare stage2 frame; this does not authorize it."""

    command = LIVESTREAM_COMMANDS["prepare_stream_transport"]
    return encode_duml_frame(
        sender=command.sender,
        receiver=command.receiver,
        sequence=sequence,
        flags=0x40,
        cmd_set=command.cmd_set,
        cmd_id=command.cmd_id,
        payload=bytes.fromhex("00011c00"),
    )


def build_configure_live_stream_frame(
    *,
    rtmp_url: str,
    sequence: int = 0x8C2C,
    resolution: int = 720,
    fps: int = 30,
    bitrate_kbps: int = 4000,
) -> bytes:
    """Build the node-osmo/Moblin Pocket 3 08/78 configuration request."""

    resolution_byte = {480: 0x47, 720: 0x04, 1080: 0x0A}.get(resolution)
    fps_byte = {25: 0x02, 30: 0x03}.get(fps)
    if resolution_byte is None or fps_byte is None:
        raise ValueError("unsupported Pocket 3 livestream resolution or frame rate")
    if not 500 <= bitrate_kbps <= 20_000:
        raise ValueError("livestream bitrate must be 500..20000 Kbps")
    encoded_url = rtmp_url.encode("utf-8")
    if not encoded_url or len(encoded_url) > 0xFFFF:
        raise ValueError("RTMP URL encoded length must be 1..65535 bytes")
    payload = (
        bytes((0x00, 0x2E, 0x00, resolution_byte))
        + bitrate_kbps.to_bytes(2, "little")
        + bytes((0x02, 0x00, fps_byte, 0x00, 0x00, 0x00))
        + len(encoded_url).to_bytes(2, "little")
        + encoded_url
    )
    command = LIVESTREAM_COMMANDS["configure_live_stream"]
    return encode_duml_frame(
        sender=command.sender,
        receiver=command.receiver,
        sequence=sequence,
        flags=0x40,
        cmd_set=command.cmd_set,
        cmd_id=command.cmd_id,
        payload=payload,
    )


def build_start_live_stream_transport_frame(*, sequence: int = 0xB4BB) -> bytes:
    """Build the fixed djictl/node-osmo/Moblin start-transport payload."""

    command = LIVESTREAM_COMMANDS["start_live_stream_transport"]
    return encode_duml_frame(
        sender=command.sender,
        receiver=command.receiver,
        sequence=sequence,
        flags=0x40,
        cmd_set=command.cmd_set,
        cmd_id=command.cmd_id,
        payload=bytes.fromhex("01011a000101"),
    )
