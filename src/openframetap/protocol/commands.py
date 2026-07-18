"""Command metadata and the mandatory stage-two send policy."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone


class CommandRejected(PermissionError):
    pass


EXPLICIT_DENIED_FRAME_OVERRIDES = {
    # Owner-approved 2026-07-19 recovery stage. Generic 02/8E traffic remains
    # denied; only this reviewed, CRC-valid wire image may cross the override.
    "prepare_stream_transport": (
        "624c92dc2ce9364346e1b5e548f8be260b9f21e35fca20503b38315c90999286"
    ),
}


@dataclass(frozen=True, slots=True)
class CommandDefinition:
    name: str
    sender: int
    receiver: int
    cmd_set: int
    cmd_id: int
    payload_schema: str
    ack_required: bool
    allowed_in_current_phase: bool
    reference_sources: tuple[str, ...]
    confidence: str
    danger: str | None = None

    @property
    def key(self) -> tuple[int, int]:
        return self.cmd_set, self.cmd_id


@dataclass(frozen=True, slots=True)
class SendAuthorization:
    """Explicit, auditable authorization supplied above the transport layer."""

    allowed_command_names: frozenset[str]
    purpose: str
    approval_reference: str
    approved_at: str
    explicitly_approved_denied_commands: frozenset[str] = field(default_factory=frozenset)
    approved_frame_sha256: str | None = None

    @classmethod
    def pairing(cls, *, approval_reference: str) -> "SendAuthorization":
        if not approval_reference.strip():
            raise ValueError("approval_reference cannot be empty")
        return cls(
            allowed_command_names=frozenset(
                {"set_pairing_pin", "pairing_stage1_ack", "pairing_stage2"}
            ),
            purpose="Pocket 3 application-layer pairing only",
            approval_reference=approval_reference,
            approved_at=datetime.now(timezone.utc).isoformat(),
        )

    @classmethod
    def single_command(
        cls, command_name: str, *, purpose: str, approval_reference: str
    ) -> "SendAuthorization":
        if not command_name.strip() or not purpose.strip() or not approval_reference.strip():
            raise ValueError("single-command authorization fields cannot be empty")
        return cls(
            allowed_command_names=frozenset({command_name}),
            purpose=purpose,
            approval_reference=approval_reference,
            approved_at=datetime.now(timezone.utc).isoformat(),
        )

    @classmethod
    def explicit_single_frame(
        cls,
        command_name: str,
        *,
        frame_sha256: str,
        purpose: str,
        approval_reference: str,
        allow_denied_command: bool = False,
    ) -> "SendAuthorization":
        digest = frame_sha256.lower()
        if len(digest) != 64 or any(
            character not in "0123456789abcdef" for character in digest
        ):
            raise ValueError("frame_sha256 must be 64 hexadecimal characters")
        if allow_denied_command and EXPLICIT_DENIED_FRAME_OVERRIDES.get(command_name) != digest:
            raise CommandRejected(
                f"{command_name} does not have an exact-frame denied-command override"
            )
        base = cls.single_command(
            command_name, purpose=purpose, approval_reference=approval_reference
        )
        return cls(
            allowed_command_names=base.allowed_command_names,
            purpose=base.purpose,
            approval_reference=base.approval_reference,
            approved_at=base.approved_at,
            explicitly_approved_denied_commands=(
                frozenset({command_name}) if allow_denied_command else frozenset()
            ),
            approved_frame_sha256=digest,
        )


PAIRING_COMMANDS = {
    "set_pairing_pin": CommandDefinition(
        name="set_pairing_pin",
        sender=0x02,
        receiver=0x07,
        cmd_set=0x07,
        cmd_id=0x45,
        payload_schema="packed identifier string + packed PIN string; conflicting references",
        ack_required=True,
        allowed_in_current_phase=True,
        reference_sources=("djictl", "lib-osmo-ble", "reverse-engineering-dji capture"),
        confidence="medium",
    ),
    "pairing_stage1_ack": CommandDefinition(
        name="pairing_stage1_ack",
        sender=0x02,
        receiver=0x07,
        cmd_set=0x07,
        cmd_id=0x46,
        payload_schema="one status byte (captured value 0x00)",
        ack_required=True,
        allowed_in_current_phase=True,
        reference_sources=("djictl", "reverse-engineering-dji capture"),
        confidence="high",
    ),
    "pairing_stage2": CommandDefinition(
        name="pairing_stage2",
        sender=0x02,
        receiver=0x88,
        cmd_set=0x00,
        cmd_id=0x32,
        payload_schema="captured bytes 31 31 00 00 00",
        ack_required=True,
        allowed_in_current_phase=True,
        reference_sources=("djictl", "reverse-engineering-dji capture"),
        confidence="high",
    ),
}


def _dangerous(
    name: str,
    cmd_id: int,
    danger: str,
    *,
    cmd_set: int = 0x04,
) -> CommandDefinition:
    return CommandDefinition(
        name=name,
        sender=0x02,
        receiver=0x04,
        cmd_set=cmd_set,
        cmd_id=cmd_id,
        payload_schema="not implemented: explicitly prohibited",
        ack_required=True,
        allowed_in_current_phase=False,
        reference_sources=("lib-osmo-ble command map",),
        confidence="reference-only",
        danger=danger,
    )


DANGEROUS_COMMANDS = {
    command.name: command
    for command in (
        _dangerous("gimbal_pwm_control", 0x01, "controls gimbal PWM"),
        _dangerous("gimbal_set_angle", 0x0A, "sets gimbal angle"),
        _dangerous("gimbal_speed_control", 0x0C, "controls gimbal speed"),
        _dangerous("gimbal_absolute_angle", 0x14, "controls absolute gimbal angle"),
        _dangerous("gimbal_incremental_move", 0x15, "moves gimbal incrementally"),
        _dangerous("gimbal_recenter", 0x4C, "recenters gimbal"),
        _dangerous("gimbal_mode_switch", 0x1A, "changes gimbal mode"),
        _dangerous("camera_record", 0x02, "starts or stops recording", cmd_set=0x02),
        _dangerous("video_stream", 0x8E, "starts or stops streaming", cmd_set=0x02),
        _dangerous("wifi_provision", 0x47, "changes camera Wi-Fi", cmd_set=0x07),
    )
}

COMMANDS_BY_NAME = {**PAIRING_COMMANDS, **DANGEROUS_COMMANDS}


def get_command_definition(name: str) -> CommandDefinition:
    if name in COMMANDS_BY_NAME:
        return COMMANDS_BY_NAME[name]
    from openframetap.protocol.livestream_commands import LIVESTREAM_COMMANDS

    try:
        return LIVESTREAM_COMMANDS[name]
    except KeyError as exc:
        raise KeyError(name) from exc


def assert_send_allowed(command: CommandDefinition, authorization: SendAuthorization | None) -> None:
    """Fail closed: no authorization means no characteristic write."""

    if authorization is None:
        raise CommandRejected(f"{command.name} requires explicit user authorization")
    if (command.danger or not command.allowed_in_current_phase) and command.name not in (
        authorization.explicitly_approved_denied_commands
    ):
        raise CommandRejected(f"{command.name} is denied in the current phase: {command.danger}")
    if command.name not in authorization.allowed_command_names:
        raise CommandRejected(
            f"authorization {authorization.approval_reference!r} does not allow {command.name}"
        )


def validate_command_frame(command: CommandDefinition, frame) -> None:
    """Validate phase-specific flags and payload before a transport is opened."""

    if frame.encryption != 0:
        raise CommandRejected("encrypted command candidates are not allowed in this phase")
    if command.ack_required != bool(frame.flags & 0x40):
        raise CommandRejected(f"{command.name} ACK flag does not match its definition")
    if command.name == "set_pairing_pin":
        if frame.flags != 0x40:
            raise CommandRejected("set_pairing_pin must use request flags 0x40")
        payload = frame.payload
        if not payload or payload[0] != 15 or len(payload) < 18:
            raise CommandRejected("set_pairing_pin identifier packing is invalid")
        identifier_end = 1 + payload[0]
        if payload[1:identifier_end] != b"001749319286102":
            raise CommandRejected("set_pairing_pin identifier is not the reviewed Pocket 3 value")
        pin_length = payload[identifier_end]
        pin = payload[identifier_end + 1 :]
        if pin_length != len(pin) or not 4 <= pin_length <= 8:
            raise CommandRejected("set_pairing_pin PIN packing or length is invalid")
        if not all(0x20 <= value < 0x7F for value in pin):
            raise CommandRejected("set_pairing_pin PIN must be printable ASCII")
    elif command.name == "pairing_stage1_ack":
        if frame.flags != 0xC0 or frame.payload != b"\x00":
            raise CommandRejected("pairing_stage1_ack must be C00746 with payload 00")
    elif command.name == "pairing_stage2":
        if frame.flags != 0x40 or frame.payload != b"11\x00\x00\x00":
            raise CommandRejected("pairing_stage2 must be 400032 with payload 3131000000")
    elif command.name == "prepare_to_live_stream":
        if frame.flags != 0x40 or frame.payload != b"\x1A":
            raise CommandRejected("prepare_to_live_stream must be 4002E1 with payload 1A")
    elif command.name == "prepare_stream_transport":
        if frame.flags != 0x40 or frame.payload != b"\x00\x01\x1C\x00":
            raise CommandRejected(
                "prepare_stream_transport must be 40028E with payload 00011C00"
            )
    elif command.name == "wifi_connect":
        payload = frame.payload
        if len(payload) < 4:
            raise CommandRejected("wifi_connect packed-string payload is truncated")
        ssid_length = payload[0]
        psk_length_offset = 1 + ssid_length
        if ssid_length == 0 or psk_length_offset >= len(payload):
            raise CommandRejected("wifi_connect SSID packing is invalid")
        psk_length = payload[psk_length_offset]
        if psk_length_offset + 1 + psk_length != len(payload) or not 8 <= psk_length <= 63:
            raise CommandRejected("wifi_connect PSK packing or length is invalid")
    elif command.name == "configure_live_stream":
        payload = frame.payload
        if len(payload) < 15:
            raise CommandRejected("configure_live_stream payload is truncated")
        if payload[:4] not in {
            bytes((0x00, 0x2E, 0x00, 0x47)),
            bytes((0x00, 0x2E, 0x00, 0x04)),
            bytes((0x00, 0x2E, 0x00, 0x0A)),
        }:
            raise CommandRejected("configure_live_stream fixed bytes or resolution are invalid")
        bitrate_kbps = int.from_bytes(payload[4:6], "little")
        if not 500 <= bitrate_kbps <= 20_000 or payload[6:8] != b"\x02\x00":
            raise CommandRejected("configure_live_stream bitrate or fixed bytes are invalid")
        if payload[8] not in {0x02, 0x03} or payload[9:12] != b"\x00\x00\x00":
            raise CommandRejected("configure_live_stream FPS or reserved bytes are invalid")
        url_length = int.from_bytes(payload[12:14], "little")
        url_bytes = payload[14:]
        if url_length != len(url_bytes):
            raise CommandRejected("configure_live_stream RTMP URL packing is invalid")
        try:
            rtmp_url = url_bytes.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise CommandRejected("configure_live_stream RTMP URL is not UTF-8") from exc
        from urllib.parse import urlsplit
        from openframetap.network.interfaces import is_rfc1918

        parsed = urlsplit(rtmp_url)
        try:
            port = parsed.port
        except ValueError as exc:
            raise CommandRejected("configure_live_stream RTMP port is invalid") from exc
        try:
            lan_target = bool(parsed.hostname and is_rfc1918(parsed.hostname))
        except ValueError:
            lan_target = False
        if (
            parsed.scheme != "rtmp"
            or not lan_target
            or port != 1935
            or not parsed.path.startswith("/live/")
            or len(parsed.path.split("/")) != 3
            or not parsed.path.rsplit("/", 1)[-1]
            or parsed.query
            or parsed.fragment
            or parsed.username
            or parsed.password
        ):
            raise CommandRejected("configure_live_stream RTMP URL violates LAN policy")
