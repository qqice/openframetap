"""Last-line validation for gimbal frames presented to a control sink."""

from __future__ import annotations

import struct

from openframetap.protocol.commands import CommandRejected
from openframetap.protocol.duml import decode_duml_frame
from openframetap.protocol.gimbal_commands import (
    POCKET3_SPEED_PROFILE,
    Pocket3GimbalSpeedProfile,
)


def validate_gimbal_control_frame(
    raw: bytes,
    *,
    profile: Pocket3GimbalSpeedProfile = POCKET3_SPEED_PROFILE,
    require_live_validated: bool,
) -> dict[str, int | bool]:
    """Reject everything except the exact bounded 04/0C profile."""

    try:
        frame = decode_duml_frame(raw)
    except Exception as exc:
        raise CommandRejected(f"invalid control frame: {exc}") from exc
    if not frame.crc8_valid or not frame.crc16_valid:
        raise CommandRejected("control frame CRC validation failed")
    if (frame.sender, frame.receiver) != (profile.sender, profile.receiver):
        raise CommandRejected("control frame endpoint mismatch")
    if frame.flags != profile.request_flags or frame.encryption != 0:
        raise CommandRejected("control frame flags are not the reviewed request form")
    if frame.cmd_set != profile.cmd_set or frame.cmd_id != profile.cmd_id:
        if frame.cmd_set == 0x04 and frame.cmd_id == 0x01:
            raise CommandRejected("raw PWM is prohibited")
        raise CommandRejected("unknown or non-allowlisted gimbal command")
    if require_live_validated and not profile.pocket3_hardware_validated:
        raise CommandRejected("live control requires a Pocket 3 validated profile")
    if len(frame.payload) != 7:
        raise CommandRejected("04/0C payload must be exactly seven bytes")
    pitch_units, roll_units, yaw_units, flag = struct.unpack("<hhhB", frame.payload)
    if roll_units != 0:
        raise CommandRejected("roll control is not allowed in this phase")
    limit_units = round(profile.reference_speed_dps * 10 * profile.max_live_axis)
    if abs(pitch_units) > limit_units or abs(yaw_units) > limit_units:
        raise CommandRejected("gimbal rate exceeds the live safety envelope")
    if flag != profile.control_flag:
        raise CommandRejected("unreviewed 04/0C control flag")
    return {
        "sequence": frame.sequence,
        "pitch_units": pitch_units,
        "roll_units": roll_units,
        "yaw_units": yaw_units,
        "is_zero": pitch_units == 0 and yaw_units == 0,
    }

