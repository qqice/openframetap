from __future__ import annotations

import asyncio

from openframetap.devices.pocket3 import POCKET3_PROFILE
from openframetap.pairing.manual_session import run_manual_pairing_session
from openframetap.protocol.duml import decode_duml_frame, encode_duml_frame
from openframetap.transport.bluez_ble import NotificationRecord


def test_session_bound_pairing_requires_confirmation_for_all_three_frames(tmp_path) -> None:
    sent = []
    proposed = []
    ready = encode_duml_frame(
        sender=1,
        receiver=2,
        sequence=1,
        flags=0,
        cmd_set=2,
        cmd_id=0x80,
        payload=b"ready",
    )
    status = encode_duml_frame(
        sender=7,
        receiver=2,
        sequence=0x72AA,
        flags=0xC0,
        cmd_set=7,
        cmd_id=0x45,
        payload=b"\x00\x02",
    )
    approval = encode_duml_frame(
        sender=7,
        receiver=2,
        sequence=0x0A00,
        flags=0x40,
        cmd_set=7,
        cmd_id=0x46,
        payload=b"\x01",
    )
    stage2_response = encode_duml_frame(
        sender=0x88,
        receiver=2,
        sequence=0x74AA,
        flags=0xC0,
        cmd_set=0,
        cmd_id=0x32,
        payload=b"\x00",
    )

    class FakeTransport:
        def __init__(self, _address, _profile, *, event_handler, **_kwargs) -> None:
            self.handler = None
            self.is_connected = False
            self.active_disconnect_count = 0
            self.setup_disconnect_count = 0
            self.disconnect_count = 0

        async def connect(self) -> None:
            self.is_connected = True

        async def subscribe(self, handler) -> None:
            self.handler = handler
            await self._notify(ready, 1)

        async def _notify(self, raw: bytes, index: int) -> None:
            await self.handler(
                NotificationRecord(
                    wall_timestamp=f"2026-07-18T00:00:0{index}+00:00",
                    monotonic_ns=index,
                    characteristic_uuid=POCKET3_PROFILE.notification_uuid,
                    characteristic_handle=44,
                    data=raw,
                )
            )

        async def send_frame(self, raw, *, command, authorization) -> None:
            sent.append((command.name, bytes(raw)))
            if command.name == "set_pairing_pin":
                await self._notify(status, 2)
                await self._notify(approval, 3)
            elif command.name == "pairing_stage2":
                await self._notify(stage2_response, 4)

        async def disconnect(self) -> None:
            self.is_connected = False

    async def confirm(candidate: dict) -> bool:
        proposed.append(candidate)
        return True

    summary, ok = asyncio.run(
        run_manual_pairing_session(
            "fixture",
            output_dir=tmp_path,
            confirmation_callback=confirm,
            telemetry_seconds=0,
            response_timeout=0.5,
            approval_timeout=0.5,
            transport_factory=FakeTransport,
        )
    )
    assert ok
    assert [name for name, _raw in sent] == [
        "set_pairing_pin",
        "pairing_stage1_ack",
        "pairing_stage2",
    ]
    assert [candidate["command"] for candidate in proposed] == [
        "set_pairing_pin",
        "pairing_stage1_ack",
        "pairing_stage2",
    ]
    stage1 = decode_duml_frame(sent[1][1])
    assert stage1.sequence == 0x0A00
    assert summary["writes_attempted"] == 3
    assert summary["automatic_follow_up_frames"] == 0
    assert summary["pairing_result"] == "paired_with_explicit_stage2_response"
    assert summary["pairing_state"] == "paired"


def test_declined_first_candidate_causes_zero_writes(tmp_path) -> None:
    ready = encode_duml_frame(
        sender=1,
        receiver=2,
        sequence=1,
        flags=0,
        cmd_set=2,
        cmd_id=0x80,
        payload=b"ready",
    )

    class FakeTransport:
        def __init__(self, _address, _profile, *, event_handler, **_kwargs) -> None:
            self.handler = None
            self.is_connected = False
            self.active_disconnect_count = 0
            self.setup_disconnect_count = 0
            self.disconnect_count = 0

        async def connect(self) -> None:
            self.is_connected = True

        async def subscribe(self, handler) -> None:
            self.handler = handler
            await handler(
                NotificationRecord(
                    wall_timestamp="2026-07-18T00:00:00+00:00",
                    monotonic_ns=1,
                    characteristic_uuid=POCKET3_PROFILE.notification_uuid,
                    characteristic_handle=44,
                    data=ready,
                )
            )

        async def send_frame(self, *_args, **_kwargs) -> None:
            raise AssertionError("declined candidate must never be written")

        async def disconnect(self) -> None:
            self.is_connected = False

    summary, ok = asyncio.run(
        run_manual_pairing_session(
            "fixture",
            output_dir=tmp_path,
            confirmation_callback=lambda _candidate: False,
            telemetry_seconds=0,
            transport_factory=FakeTransport,
        )
    )
    assert not ok
    assert summary["writes_attempted"] == 0
    assert summary["pairing_result"] == "cancelled_before_first_write"
