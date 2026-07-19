from __future__ import annotations

from dataclasses import replace

import pytest

from openframetap.protocol.dji_wifi import (
    DjiWifiEnvelope,
    DjiWifiEnvelopeError,
    DjiWifiOperatorSequencer,
)


CAPTURED_CENTER = bytes.fromhex(
    "2b805570a89005b3a090a89000000000c0010000"
    "55170438020442b70004010004000000040080420005b8"
)


def test_parse_and_reencode_captured_envelope_exactly() -> None:
    envelope = DjiWifiEnvelope.parse(CAPTURED_CENTER)
    assert envelope.total_length == len(CAPTURED_CENTER)
    assert envelope.format_nibble == 8
    assert envelope.session_id == 0x7055
    assert envelope.transport_sequence == 0x90A8
    assert envelope.wh_type == 5
    assert envelope.checksum_valid
    assert envelope.peer_sequence == 0x90A0
    assert envelope.sequence_echo == envelope.transport_sequence
    assert envelope.reserved_12_15 == b"\0" * 4
    assert envelope.message_sequence == 0x01C0
    assert envelope.delivery_flags == 0
    assert envelope.reserved_19 == 0
    assert envelope.payload.startswith(b"\x55")
    assert envelope.encode() == CAPTURED_CENTER


def test_length_and_checksum_corruption_are_visible() -> None:
    corrupted = bytearray(CAPTURED_CENTER)
    corrupted[7] ^= 1
    assert not DjiWifiEnvelope.parse(corrupted).checksum_valid
    with pytest.raises(DjiWifiEnvelopeError, match="truncated"):
        DjiWifiEnvelope.parse(CAPTURED_CENTER[:-1])


def test_operator_sequence_and_message_counter_wrap() -> None:
    sequencer = DjiWifiOperatorSequencer(
        session_id=0x7055,
        next_transport_sequence=0xFFF8,
        peer_sequence=0xFFF0,
        next_message_counter=0xFF,
    )
    first = sequencer.build(b"one")
    second = sequencer.build(b"two")
    assert (first.transport_sequence, second.transport_sequence) == (0xFFF8, 0)
    assert (first.message_sequence, second.message_sequence) == (0x01FF, 0x0100)
    assert DjiWifiEnvelope.parse(first.encode()).encode() == first.encode()


def test_peer_ack_observation_advances_and_ignores_old_or_duplicate_values() -> None:
    sequencer = DjiWifiOperatorSequencer(1, 0, 0xFFF0)
    assert sequencer.observe_peer_sequence(0xFFF8)
    assert sequencer.observe_peer_sequence(0x0000)
    assert not sequencer.observe_peer_sequence(0x0000)
    assert not sequencer.observe_peer_sequence(0xFFF8)
    assert sequencer.build(b"next").peer_sequence == 0x0000


def test_generation_rejects_unreviewed_dynamic_or_reserved_fields() -> None:
    envelope = DjiWifiOperatorSequencer(1, 8, 0).build(b"payload")
    envelope.validate_operator_policy()
    with pytest.raises(DjiWifiEnvelopeError, match="delivery flags"):
        replace(envelope, delivery_flags=0x60).validate_operator_policy()
    with pytest.raises(DjiWifiEnvelopeError, match="reserved bytes"):
        replace(envelope, reserved_12_15=b"\0\0\0\1").validate_operator_policy()


def test_payload_length_must_match_envelope() -> None:
    envelope = DjiWifiOperatorSequencer(1, 8, 0).build(b"payload")
    with pytest.raises(DjiWifiEnvelopeError, match="total length"):
        replace(envelope, total_length=envelope.total_length + 1).encode()
