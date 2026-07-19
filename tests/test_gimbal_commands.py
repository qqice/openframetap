from __future__ import annotations

import pytest

from openframetap.protocol.duml import decode_duml_frame, encode_duml_frame
from openframetap.protocol.commands import (
    CommandRejected,
    DANGEROUS_COMMANDS,
    SendAuthorization,
    assert_send_allowed,
    validate_command_frame,
)
from openframetap.protocol.gimbal_commands import (
    POCKET3_SPEED_PROFILE,
    build_speed_frame,
    build_zero_frame,
    speed_payload,
)


def test_speed_payload_uses_reviewed_pocket3_axis_order() -> None:
    assert speed_payload(yaw=0.05, pitch=0.0) == bytes.fromhex("000000000f0001")
    assert speed_payload(yaw=0.0, pitch=-0.05) == bytes.fromhex("f1ff0000000001")


def test_zero_payload_is_explicit_zero_rate() -> None:
    frame = decode_duml_frame(build_zero_frame(sequence=0x1234))
    assert (frame.sender, frame.receiver, frame.flags) == (0x02, 0x04, 0x40)
    assert (frame.cmd_set, frame.cmd_id) == (0x04, 0x0C)
    assert frame.payload == bytes.fromhex("00000000000001")
    assert frame.crc8_valid and frame.crc16_valid


def test_speed_frame_round_trip_and_sequence_wrap() -> None:
    for sequence in (0, 0xFFFF):
        decoded = decode_duml_frame(
            build_speed_frame(sequence=sequence, yaw=-0.1, pitch=0.1)
        )
        assert decoded.sequence == sequence
        assert decoded.payload == bytes.fromhex("1e000000e2ff01")


def test_live_axis_limit_is_fail_closed() -> None:
    build_speed_frame(sequence=1, yaw=0.15, pitch=-0.15, live=True)
    with pytest.raises(ValueError, match="live limit"):
        build_speed_frame(sequence=1, yaw=0.151, pitch=0.0, live=True)


def test_profile_is_not_prematurely_hardware_validated() -> None:
    assert POCKET3_SPEED_PROFILE.pocket3_hardware_validated is False
    assert POCKET3_SPEED_PROFILE.axis_order == "pitch_roll_yaw"


def test_bounded_authorization_is_only_for_speed_control() -> None:
    authorization = SendAuthorization.bounded_gimbal_test(approval_reference="fixture")
    speed = DANGEROUS_COMMANDS["gimbal_speed_control"]
    assert_send_allowed(speed, authorization)
    with pytest.raises(CommandRejected):
        assert_send_allowed(DANGEROUS_COMMANDS["gimbal_pwm_control"], authorization)


def test_transport_policy_revalidates_bounded_speed_payload() -> None:
    command = DANGEROUS_COMMANDS["gimbal_speed_control"]
    valid = decode_duml_frame(build_speed_frame(sequence=1, yaw=0.15, pitch=0.0, live=True))
    validate_command_frame(command, valid)
    invalid_flag = decode_duml_frame(
        encode_duml_frame(
            sender=2,
            receiver=4,
            sequence=1,
            flags=0x40,
            cmd_set=4,
            cmd_id=0x0C,
            payload=bytes.fromhex("00000000000080"),
        )
    )
    with pytest.raises(CommandRejected, match="flag"):
        validate_command_frame(command, invalid_flag)
