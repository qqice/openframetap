"""Capture-verified, constrained Pocket 3 Wi-Fi stick profile."""

from __future__ import annotations

from dataclasses import dataclass
import struct

from openframetap.protocol.duml import decode_duml_frame, encode_duml_frame


STICK_CENTER = 1024
CAPTURED_PITCH_MIN = 836
CAPTURED_PITCH_MAX = 1256
CAPTURED_YAW_MIN = 810
CAPTURED_YAW_MAX = 1295
INITIAL_TEST_MAX_OFFSET = 16
# Common symmetric envelope observed in the Mimo capture.  Pitch-negative is
# the limiting direction: 1024 - 836 = 188.
LIVE_PROTOTYPE_MIN_OFFSET = 16
LIVE_PROTOTYPE_DEFAULT_OFFSET = 96
LIVE_PROTOTYPE_MAX_OFFSET = 188


@dataclass(frozen=True, slots=True)
class Pocket3StickCommand:
    pitch: int = STICK_CENTER
    yaw: int = STICK_CENTER
    roll: int = 0
    fixed_8000: int = 0x8000
    fixed_0042: int = 0x0042

    def __post_init__(self) -> None:
        if self.roll != 0:
            raise ValueError("roll control is prohibited")
        if self.fixed_8000 != 0x8000 or self.fixed_0042 != 0x0042:
            raise ValueError("Pocket 3 stick fixed fields may not be changed")
        if not CAPTURED_PITCH_MIN <= self.pitch <= CAPTURED_PITCH_MAX:
            raise ValueError("pitch value is outside the Mimo-captured range")
        if not CAPTURED_YAW_MIN <= self.yaw <= CAPTURED_YAW_MAX:
            raise ValueError("yaw value is outside the Mimo-captured range")

    @property
    def is_center(self) -> bool:
        return self.pitch == STICK_CENTER and self.yaw == STICK_CENTER

    def encode_payload(self) -> bytes:
        return struct.pack(
            "<5H", self.pitch, self.roll, self.yaw, self.fixed_8000, self.fixed_0042
        )

    def encode_duml(self, *, sequence: int) -> bytes:
        frame = encode_duml_frame(
            sender=0x02,
            receiver=0x04,
            sequence=sequence,
            flags=0x00,
            cmd_set=0x04,
            cmd_id=0x01,
            payload=self.encode_payload(),
        )
        decoded = decode_duml_frame(frame)
        if not decoded.crc8_valid or not decoded.crc16_valid:
            raise RuntimeError("internally generated 04/01 frame failed CRC validation")
        return frame

    @classmethod
    def from_axes(
        cls,
        *,
        yaw_axis: float,
        pitch_axis: float,
        max_offset: int = INITIAL_TEST_MAX_OFFSET,
    ) -> "Pocket3StickCommand":
        for name, value in (("yaw_axis", yaw_axis), ("pitch_axis", pitch_axis)):
            if not -1.0 <= float(value) <= 1.0:
                raise ValueError(f"{name} must be within -1.0..1.0")
        if not 1 <= int(max_offset) <= LIVE_PROTOTYPE_MAX_OFFSET:
            raise ValueError(f"max_offset must be within 1..{LIVE_PROTOTYPE_MAX_OFFSET}")
        return cls(
            pitch=STICK_CENTER + round(float(pitch_axis) * int(max_offset)),
            yaw=STICK_CENTER + round(float(yaw_axis) * int(max_offset)),
        )


CENTER_STICK_COMMAND = Pocket3StickCommand()


def validate_stick_duml(raw: bytes) -> Pocket3StickCommand:
    """Last-line byte validation; 04/50 and every other command are rejected."""

    frame = decode_duml_frame(raw)
    if not frame.crc8_valid or not frame.crc16_valid:
        raise ValueError("stick DUML CRC validation failed")
    if (frame.sender, frame.receiver, frame.flags, frame.cmd_set, frame.cmd_id) != (
        0x02,
        0x04,
        0x00,
        0x04,
        0x01,
    ):
        if (frame.cmd_set, frame.cmd_id) == (0x04, 0x50):
            raise ValueError("04/50 sending is prohibited in this phase")
        raise ValueError("unknown or non-allowlisted Wi-Fi gimbal command")
    if len(frame.payload) != 10:
        raise ValueError("04/01 stick payload must be exactly ten bytes")
    pitch, roll, yaw, fixed_8000, fixed_0042 = struct.unpack("<5H", frame.payload)
    return Pocket3StickCommand(pitch, yaw, roll, fixed_8000, fixed_0042)
