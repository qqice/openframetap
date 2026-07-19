"""Offline construction for the narrowly reviewed Pocket 3 speed candidate.

Nothing in this module performs I/O.  Live authorization is intentionally kept
outside the codec so mock tests can exercise the exact wire image without
creating a path around the transport policy.
"""

from __future__ import annotations

from dataclasses import dataclass
import struct

from openframetap.protocol.duml import decode_duml_frame, encode_duml_frame


@dataclass(frozen=True, slots=True)
class Pocket3GimbalSpeedProfile:
    name: str = "pocket3_speed_04_0c_v0"
    cmd_set: int = 0x04
    cmd_id: int = 0x0C
    sender: int = 0x02
    receiver: int = 0x04
    request_flags: int = 0x40
    control_flag: int = 0x01
    reference_speed_dps: float = 30.0
    max_live_axis: float = 0.15
    axis_order: str = "pitch_roll_yaw"
    confidence: str = "medium"
    pocket3_hardware_validated: bool = False

    def validate_axis(self, value: float, *, live: bool = False) -> float:
        value = float(value)
        limit = self.max_live_axis if live else 1.0
        if not -limit <= value <= limit:
            raise ValueError(f"axis {value} exceeds {'live ' if live else ''}limit {limit}")
        return value


POCKET3_SPEED_PROFILE = Pocket3GimbalSpeedProfile()


def speed_payload(
    *,
    yaw: float,
    pitch: float,
    profile: Pocket3GimbalSpeedProfile = POCKET3_SPEED_PROFILE,
    live: bool = False,
    release: bool = False,
) -> bytes:
    """Build the 7-byte 04/0C candidate payload from normalized axes.

    The Pocket-specific public implementation places pitch/roll/yaw at offsets
    0/2/4.  A real RS 3 capture uses yaw/roll/pitch, so this ordering remains a
    Pocket 3 hypothesis until the one-shot tests resolve it.  Roll is always
    zero in this phase.
    """

    yaw = profile.validate_axis(yaw, live=live)
    pitch = profile.validate_axis(pitch, live=live)
    units_per_normalized = profile.reference_speed_dps * 10.0
    pitch_units = round(pitch * units_per_normalized)
    yaw_units = round(yaw * units_per_normalized)
    flag = 0 if release else profile.control_flag
    return struct.pack("<hhhB", pitch_units, 0, yaw_units, flag)


def build_speed_frame(
    *,
    sequence: int,
    yaw: float,
    pitch: float,
    profile: Pocket3GimbalSpeedProfile = POCKET3_SPEED_PROFILE,
    live: bool = False,
    release: bool = False,
) -> bytes:
    frame = encode_duml_frame(
        sender=profile.sender,
        receiver=profile.receiver,
        sequence=sequence,
        flags=profile.request_flags,
        cmd_set=profile.cmd_set,
        cmd_id=profile.cmd_id,
        payload=speed_payload(
            yaw=yaw, pitch=pitch, profile=profile, live=live, release=release
        ),
    )
    decoded = decode_duml_frame(frame)
    if not decoded.crc8_valid or not decoded.crc16_valid:
        raise RuntimeError("internally generated gimbal frame failed CRC validation")
    return frame


def build_zero_frame(
    *, sequence: int, profile: Pocket3GimbalSpeedProfile = POCKET3_SPEED_PROFILE
) -> bytes:
    """Build zero rate while retaining the candidate's enable/control flag."""

    return build_speed_frame(sequence=sequence, yaw=0.0, pitch=0.0, profile=profile)

