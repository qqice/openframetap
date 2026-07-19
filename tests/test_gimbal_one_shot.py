from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from openframetap.control.one_shot import run_one_shot_gimbal_test
from openframetap.protocol.duml import encode_duml_frame
from openframetap.transport.bluez_ble import NotificationRecord


class FakeTransport:
    instances = []

    def __init__(self, _address, _profile, *, event_handler) -> None:
        self.event_handler = event_handler
        self.fff5_write_count = 0
        self.cccd_write_count = 0
        self.disconnect_count = 0
        self.mtu = 517
        self.sent = []
        self.__class__.instances.append(self)

    async def connect(self):
        pass

    async def acquire_mtu(self):
        return self.mtu

    async def subscribe(self, handler):
        self.cccd_write_count += 1
        frame = encode_duml_frame(
            sender=4, receiver=2, sequence=1, flags=0, cmd_set=4, cmd_id=5, payload=b"\0" * 49
        )
        await handler(NotificationRecord("fixture", 1, "fff4", 44, frame))

    async def send_frame(self, raw, *, command, authorization):
        self.fff5_write_count += 1
        self.sent.append(raw)

    async def disconnect(self):
        self.cccd_write_count += 1


def test_one_shot_sends_one_nonzero_and_two_zero_frames(tmp_path: Path) -> None:
    FakeTransport.instances.clear()
    output = tmp_path / "artifacts" / "private" / "gimbal-test"
    summary = asyncio.run(
        run_one_shot_gimbal_test(
            address="fixture",
            axis="yaw",
            direction="positive",
            output_value=0.05,
            duration_ms=1,
            output_dir=output,
            software_git_head="fixture-head",
            transport_factory=FakeTransport,
        )
    )
    assert summary["error"] is None
    assert summary["nonzero_command_count"] == 1
    assert summary["zero_command_count"] == 2
    assert summary["final_output_zero"] is True
    assert summary["fff5_write_count"] == 3
    assert len(FakeTransport.instances[0].sent) == 3
    assert (output / "checksums.sha256").is_file()


@pytest.mark.parametrize(
    "kwargs",
    (
        {"axis": "roll"},
        {"direction": "sideways"},
        {"output_value": 0.051},
        {"duration_ms": 201},
    ),
)
def test_one_shot_rejects_out_of_scope_parameters(tmp_path: Path, kwargs: dict) -> None:
    values = dict(
        address="fixture",
        axis="yaw",
        direction="positive",
        output_value=0.05,
        duration_ms=200,
        output_dir=tmp_path / "artifacts" / "private" / "bad",
        software_git_head="fixture",
        transport_factory=FakeTransport,
    )
    values.update(kwargs)
    with pytest.raises(ValueError):
        asyncio.run(run_one_shot_gimbal_test(**values))

