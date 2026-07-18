"""Command metadata and the mandatory stage-two send policy."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone


class CommandRejected(PermissionError):
    pass


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


def assert_send_allowed(command: CommandDefinition, authorization: SendAuthorization | None) -> None:
    """Fail closed: no authorization means no characteristic write."""

    if command.danger or not command.allowed_in_current_phase:
        raise CommandRejected(f"{command.name} is denied in the current phase: {command.danger}")
    if authorization is None:
        raise CommandRejected(f"{command.name} requires explicit user authorization")
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
