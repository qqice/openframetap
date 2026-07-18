from __future__ import annotations

import asyncio
import hashlib
from pathlib import Path

import pytest

from openframetap.devices.pocket3 import POCKET3_PROFILE
from openframetap.devices.pocket3_livestream import write_prepare_recovery_proposal
from openframetap.protocol.commands import (
    CommandRejected,
    SendAuthorization,
    assert_send_allowed,
    get_command_definition,
)
from openframetap.protocol.duml import encode_duml_frame
from openframetap.protocol.livestream_commands import build_prepare_stream_stage2_frame
from openframetap.transport.bluez_ble import BluezBleTransport, NotificationRecord
from openframetap.workflows.prepare_recovery_session import run_prepare_recovery_session


ADDRESS = "00:11:22:33:44:55"


def _proposal(tmp_path: Path) -> Path:
    payload = write_prepare_recovery_proposal(
        address=ADDRESS,
        private_root=tmp_path / "artifacts" / "private" / "proposals",
        sanitized_root=tmp_path / "artifacts" / "sanitized" / "proposals",
        wifi_result_sha256="4" * 64,
    )
    return Path(payload["private_proposal"])


def _frame(*, sequence: int, flags: int, cmd_id: int, payload: bytes) -> bytes:
    return encode_duml_frame(
        sender=0x08,
        receiver=0x02,
        sequence=sequence,
        flags=flags,
        cmd_set=0x02,
        cmd_id=cmd_id,
        payload=payload,
    )


READY = _frame(sequence=1, flags=0, cmd_id=0x80, payload=b"ready")
STAGE1_ACK = _frame(sequence=0xFEAB, flags=0xC0, cmd_id=0xE1, payload=b"\x00")
STAGE2_RESPONSE = _frame(
    sequence=0xFFAB,
    flags=0x80,
    cmd_id=0x8E,
    payload=bytes.fromhex("0000011c000102"),
)


def _transport_class(*, stage1_response: bytes = STAGE1_ACK, stage2_response: bytes = STAGE2_RESPONSE):
    class FakeTransport:
        instances = []

        def __init__(self, _address, _profile, *, event_handler, **_kwargs) -> None:
            self.handler = None
            self.is_connected = False
            self.active_disconnect_count = 0
            self.setup_disconnect_count = 0
            self.disconnect_count = 0
            self.sent = []
            self.__class__.instances.append(self)

        async def connect(self) -> None:
            self.is_connected = True

        async def subscribe(self, handler) -> None:
            self.handler = handler
            await self._notify(READY, 1)

        async def _notify(self, raw: bytes, index: int) -> None:
            await self.handler(
                NotificationRecord(
                    wall_timestamp=f"2026-07-19T00:00:0{index}+00:00",
                    monotonic_ns=index,
                    characteristic_uuid=POCKET3_PROFILE.notification_uuid,
                    characteristic_handle=44,
                    data=raw,
                )
            )

        async def send_frame(self, raw, *, command, authorization) -> None:
            self.sent.append((bytes(raw), command, authorization))
            if command.name == "prepare_to_live_stream":
                await self._notify(stage1_response, 2)
            elif command.name == "prepare_stream_transport":
                await self._notify(stage2_response, 3)

        async def disconnect(self) -> None:
            self.is_connected = False

    return FakeTransport


def test_same_connection_recovery_sends_two_sha_bound_frames_after_exact_ack(tmp_path) -> None:
    transport_class = _transport_class()
    proposed = []

    summary, ok = asyncio.run(
        run_prepare_recovery_session(
            ADDRESS,
            proposal_path=_proposal(tmp_path),
            output_dir=tmp_path / "artifacts" / "private" / "capture",
            confirmation_callback=lambda candidate: proposed.append(candidate) or True,
            response_timeout=0.2,
            passive_seconds=0,
            transport_factory=transport_class,
        )
    )

    assert ok
    assert len(transport_class.instances) == 1
    sent = transport_class.instances[0].sent
    assert [command.name for _raw, command, _authorization in sent] == [
        "prepare_to_live_stream",
        "prepare_stream_transport",
    ]
    assert [candidate["stage"] for candidate in proposed] == [
        "prepare_reentry",
        "prepare_stream_stage2",
    ]
    for raw, command, authorization in sent:
        assert authorization.approved_frame_sha256 == hashlib.sha256(raw).hexdigest()
        if command.name == "prepare_stream_transport":
            assert command.name in authorization.explicitly_approved_denied_commands
    assert summary["writes_attempted"] == 2
    assert summary["automatic_follow_up_frames"] == 0
    assert summary["wifi_frames_sent"] == 0
    assert summary["recovery_result"] == "prepare_stage2_response_validated"


def test_unexpected_stage1_response_stops_before_stage2_is_proposed(tmp_path) -> None:
    bad_stage1 = _frame(sequence=0xFEAB, flags=0xC0, cmd_id=0xE1, payload=b"\x01")
    transport_class = _transport_class(stage1_response=bad_stage1)
    proposed = []
    summary, ok = asyncio.run(
        run_prepare_recovery_session(
            ADDRESS,
            proposal_path=_proposal(tmp_path),
            output_dir=tmp_path / "artifacts" / "private" / "capture",
            confirmation_callback=lambda candidate: proposed.append(candidate) or True,
            response_timeout=0.1,
            passive_seconds=0,
            transport_factory=transport_class,
        )
    )
    assert not ok
    assert [candidate["stage"] for candidate in proposed] == ["prepare_reentry"]
    assert [command.name for _raw, command, _authorization in transport_class.instances[0].sent] == [
        "prepare_to_live_stream"
    ]
    assert summary["recovery_result"] == "failed"
    assert "unexpected matching-sequence" in summary["error"]


def test_declined_stage2_stops_after_stage1(tmp_path) -> None:
    transport_class = _transport_class()
    confirmations = 0

    def confirm(_candidate: dict) -> bool:
        nonlocal confirmations
        confirmations += 1
        return confirmations == 1

    summary, ok = asyncio.run(
        run_prepare_recovery_session(
            ADDRESS,
            proposal_path=_proposal(tmp_path),
            output_dir=tmp_path / "artifacts" / "private" / "capture",
            confirmation_callback=confirm,
            response_timeout=0.1,
            passive_seconds=0,
            transport_factory=transport_class,
        )
    )
    assert not ok
    assert confirmations == 2
    assert summary["writes_attempted"] == 1
    assert summary["recovery_result"] == "cancelled_before_stage2"


def test_general_authorization_still_rejects_stage2_but_exact_override_is_bounded() -> None:
    command = get_command_definition("prepare_stream_transport")
    raw = build_prepare_stream_stage2_frame()
    with pytest.raises(CommandRejected, match="denied"):
        assert_send_allowed(
            command,
            SendAuthorization.single_command(
                command.name, purpose="fixture", approval_reference="fixture"
            ),
        )
    authorization = SendAuthorization.explicit_single_frame(
        command.name,
        frame_sha256=hashlib.sha256(raw).hexdigest(),
        purpose="fixture exact frame",
        approval_reference="fixture",
        allow_denied_command=True,
    )
    assert_send_allowed(command, authorization)
    with pytest.raises(CommandRejected, match="exact-frame"):
        SendAuthorization.explicit_single_frame(
            "stop_live_stream",
            frame_sha256="1" * 64,
            purpose="fixture",
            approval_reference="fixture",
            allow_denied_command=True,
        )


def test_transport_rejects_wrong_sha_before_touching_ble_client() -> None:
    command = get_command_definition("prepare_stream_transport")
    approved_raw = build_prepare_stream_stage2_frame()
    different_raw = build_prepare_stream_stage2_frame(sequence=0xFFAC)
    authorization = SendAuthorization.explicit_single_frame(
        command.name,
        frame_sha256=hashlib.sha256(approved_raw).hexdigest(),
        purpose="fixture exact frame",
        approval_reference="fixture",
        allow_denied_command=True,
    )
    transport = BluezBleTransport("fixture", POCKET3_PROFILE)
    with pytest.raises(ValueError, match="SHA-256"):
        asyncio.run(
            transport.send_frame(
                different_raw, command=command, authorization=authorization
            )
        )
