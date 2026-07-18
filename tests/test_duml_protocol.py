from __future__ import annotations

import json
from pathlib import Path

import pytest

from openframetap.devices.pocket3 import build_pairing_stage2_frame
from openframetap.protocol.commands import (
    COMMANDS_BY_NAME,
    PAIRING_COMMANDS,
    CommandRejected,
    SendAuthorization,
    assert_send_allowed,
    validate_command_frame,
)
from openframetap.protocol.crc import crc8_dji, crc16_dji
from openframetap.protocol.duml import DumlDecodeError, decode_duml_frame, encode_duml_frame
from openframetap.protocol.reassembly import DumlStreamReassembler
from openframetap.protocol.sequence import SequenceCounter

FIXTURE = Path(__file__).parent / "fixtures" / "duml_frames.json"


def fixture_frames() -> list[dict]:
    return json.loads(FIXTURE.read_text(encoding="utf-8"))["frames"]


def test_crc_known_capture_vectors() -> None:
    first = bytes.fromhex(fixture_frames()[0]["hex"])
    assert crc8_dji(first[:3]) == 0x93
    assert crc16_dji(first[:-2]) == 0xFC17


@pytest.mark.parametrize("entry", fixture_frames(), ids=lambda entry: entry["name"])
def test_saved_capture_frames_parse(entry: dict) -> None:
    frame = decode_duml_frame(bytes.fromhex(entry["hex"]))
    expected = entry["expected"]
    assert frame.total_length == expected["length"]
    assert frame.sender == expected["sender"]
    assert frame.receiver == expected["receiver"]
    assert frame.sequence == expected["sequence"]
    assert frame.flags == expected["flags"]
    assert frame.cmd_set == expected["cmd_set"]
    assert frame.cmd_id == expected["cmd_id"]
    assert frame.crc8_valid and frame.crc16_valid


def test_legal_frame_round_trip() -> None:
    raw = encode_duml_frame(
        sender=2,
        receiver=7,
        sequence=0x72AA,
        flags=0x40,
        cmd_set=7,
        cmd_id=0x45,
        payload=b"\x04test",
    )
    decoded = decode_duml_frame(raw)
    assert decoded.sequence == 0x72AA
    assert decoded.payload == b"\x04test"
    assert decoded.raw == raw


def test_stage2_matches_published_capture() -> None:
    assert build_pairing_stage2_frame().hex() == "551204c7028874aa4000323131000000426a"


def test_wrong_crc_is_reported_not_hidden() -> None:
    raw = bytearray(bytes.fromhex(fixture_frames()[2]["hex"]))
    raw[-1] ^= 0x80
    decoded = decode_duml_frame(raw)
    assert decoded.crc8_valid
    assert not decoded.crc16_valid


def test_truncated_frame_rejected() -> None:
    raw = bytes.fromhex(fixture_frames()[0]["hex"])
    with pytest.raises(DumlDecodeError, match="truncated"):
        decode_duml_frame(raw[:-1])


def test_one_notification_can_contain_multiple_frames() -> None:
    raw1 = bytes.fromhex(fixture_frames()[1]["hex"])
    raw2 = bytes.fromhex(fixture_frames()[2]["hex"])
    events = DumlStreamReassembler().feed(raw1 + raw2)
    assert [event.frame.cmd_id for event in events if event.frame] == [0x05, 0x1C]


def test_frame_can_span_notifications() -> None:
    raw = bytes.fromhex(fixture_frames()[0]["hex"])
    reassembler = DumlStreamReassembler()
    assert not reassembler.feed(raw[:7])
    assert not reassembler.feed(raw[7:31])
    events = reassembler.feed(raw[31:])
    assert len(events) == 1 and events[0].frame is not None
    assert events[0].frame.raw == raw


def test_resynchronizes_after_garbage_and_bad_crc() -> None:
    valid = bytes.fromhex(fixture_frames()[2]["hex"])
    corrupt = bytearray(valid)
    corrupt[-1] ^= 1
    reassembler = DumlStreamReassembler()
    events = reassembler.feed(b"garbage" + bytes(corrupt) + valid)
    frames = [event.frame for event in events if event.kind == "frame"]
    assert [frame.raw for frame in frames if frame] == [valid]
    assert reassembler.stats.crc16_failures == 1
    assert reassembler.stats.discarded_bytes >= len(b"garbage")


def test_unknown_command_survives_json_serialization() -> None:
    frame = decode_duml_frame(bytes.fromhex(fixture_frames()[2]["hex"]))
    serialized = json.dumps(frame.to_dict())
    assert '"cmd_set": "0x04"' in serialized
    assert '"cmd_id": "0x1C"' in serialized
    assert frame.payload.hex() in serialized


def test_sequence_wraps_at_uint16() -> None:
    sequence = SequenceCounter(0xFFFF)
    assert sequence.take() == 0xFFFF
    assert sequence.take() == 0
    assert sequence.take() == 1


def test_send_policy_is_fail_closed() -> None:
    with pytest.raises(CommandRejected, match="explicit user authorization"):
        assert_send_allowed(PAIRING_COMMANDS["set_pairing_pin"], None)


def test_pairing_authorization_is_narrow() -> None:
    authorization = SendAuthorization.pairing(approval_reference="user-message-fixture")
    assert_send_allowed(PAIRING_COMMANDS["set_pairing_pin"], authorization)
    with pytest.raises(CommandRejected, match="denied"):
        assert_send_allowed(COMMANDS_BY_NAME["gimbal_speed_control"], authorization)


def test_pairing_payload_schema_is_enforced() -> None:
    command = PAIRING_COMMANDS["set_pairing_pin"]
    malformed = decode_duml_frame(
        encode_duml_frame(
            sender=command.sender,
            receiver=command.receiver,
            sequence=1,
            flags=0x40,
            cmd_set=command.cmd_set,
            cmd_id=command.cmd_id,
            payload=b"\x04guess\x04love",
        )
    )
    with pytest.raises(CommandRejected, match="identifier"):
        validate_command_frame(command, malformed)


def test_stage2_payload_must_match_reviewed_capture() -> None:
    command = PAIRING_COMMANDS["pairing_stage2"]
    malformed = decode_duml_frame(
        encode_duml_frame(
            sender=command.sender,
            receiver=command.receiver,
            sequence=0x74AA,
            flags=0x40,
            cmd_set=command.cmd_set,
            cmd_id=command.cmd_id,
            payload=b"guess",
        )
    )
    with pytest.raises(CommandRejected, match="3131000000"):
        validate_command_frame(command, malformed)


@pytest.mark.parametrize(
    "name",
    [
        "gimbal_pwm_control",
        "gimbal_set_angle",
        "gimbal_speed_control",
        "gimbal_absolute_angle",
        "gimbal_incremental_move",
        "gimbal_recenter",
        "gimbal_mode_switch",
    ],
)
def test_all_required_gimbal_commands_are_denied(name: str) -> None:
    authorization = SendAuthorization.pairing(approval_reference="user-message-fixture")
    with pytest.raises(CommandRejected):
        assert_send_allowed(COMMANDS_BY_NAME[name], authorization)
