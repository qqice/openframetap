from __future__ import annotations

import asyncio
import json
from pathlib import Path
import sys

from openframetap.cli import main as cli_main
from openframetap.devices.pocket3 import POCKET3_PROFILE
from openframetap.experiments.event_marker import EventWriter, ExperimentEvent
from openframetap.experiments.session import run_passive_experiment
from openframetap.protocol.duml import encode_duml_frame
from openframetap.transport.bluez_ble import NotificationRecord


class ScriptedReader:
    def __init__(self, keys: list[str] | None = None, lines: list[str] | None = None) -> None:
        self.keys = list(keys or [])
        self.lines = list(lines or [])

    def __enter__(self):
        return self

    def __exit__(self, *_args) -> None:
        pass

    async def read_key(self, timeout=None):
        if self.keys:
            return self.keys.pop(0)
        await asyncio.sleep(min(float(timeout or 0.01), 0.01))
        return None

    async def read_line(self, _prompt: str, *, max_length: int = 200) -> str:
        return self.lines.pop(0)[:max_length]


class BlockingReader(ScriptedReader):
    async def read_key(self, timeout=None):
        await asyncio.Event().wait()


def battery_frame(value: int = 88) -> bytes:
    payload = bytearray(34)
    payload[20] = value
    return encode_duml_frame(
        sender=5,
        receiver=2,
        sequence=1,
        flags=0,
        cmd_set=0x0D,
        cmd_id=0x02,
        payload=payload,
    )


class FakeTransport:
    instances: list["FakeTransport"] = []
    fff5_count = 0
    disconnected_during_capture = False

    def __init__(self, _address, _profile, *, event_handler, **_kwargs) -> None:
        self.event_handler = event_handler
        self.is_connected = False
        self.disconnect_count = 1 if self.disconnected_during_capture else 0
        self.active_disconnect_count = self.disconnect_count
        self.setup_disconnect_count = 0
        self.cccd_write_count = 0
        self.fff5_write_count = self.fff5_count
        self.disconnect_called = False
        type(self).instances.append(self)

    async def connect(self) -> None:
        self.is_connected = True

    async def acquire_mtu(self) -> int:
        return 517

    async def subscribe(self, handler) -> None:
        self.cccd_write_count += 1
        handler(
            NotificationRecord(
                wall_timestamp="2026-07-18T00:00:00+00:00",
                monotonic_ns=10_000,
                characteristic_uuid=POCKET3_PROFILE.notification_uuid,
                characteristic_handle=44,
                data=battery_frame(),
            )
        )
        if self.disconnected_during_capture:
            self.is_connected = False

    async def disconnect(self) -> None:
        if self.is_connected:
            self.cccd_write_count += 1
        self.is_connected = False
        self.disconnect_called = True

    async def send_frame(self, *_args, **_kwargs) -> None:
        raise AssertionError("passive experiment must not expose an FFF5 call path")


def test_event_jsonl_serialization(tmp_path: Path) -> None:
    writer = EventWriter(tmp_path / "events.jsonl")
    event = ExperimentEvent(
        wall_time_utc="2026-07-18T00:00:00+00:00",
        monotonic_ns=123,
        event_code="yaw_left",
        event_name="yaw_left_start",
        phase="yaw",
        optional_note="manual action",
    )
    writer.record(event)
    stored = json.loads((tmp_path / "events.jsonl").read_text(encoding="utf-8"))
    assert stored == event.to_dict()


def test_passive_experiment_records_same_clock_events_and_frames(tmp_path: Path) -> None:
    FakeTransport.instances.clear()
    reader = ScriptedReader(["r", "0", "9", "m", "q"], ["88", "safe manual note"])
    session, ok = asyncio.run(
        run_passive_experiment(
            "fixture",
            duration=1,
            output_dir=tmp_path,
            transport_factory=FakeTransport,
            key_reader_factory=lambda: reader,
            software_git_head="abc123",
            remote_kernel="fixture-kernel",
        )
    )
    assert ok
    assert session["fff5_write_count"] == 0
    assert session["cccd_write_count"] == 2
    assert session["att_mtu"] == 517
    assert session["software_git_head"] == "abc123"
    assert session["notifications_received"] == 1
    assert session["duml_frames_received"] == 1
    assert FakeTransport.instances[-1].disconnect_called

    events = [json.loads(line) for line in (tmp_path / "events.jsonl").read_text().splitlines()]
    battery = next(item for item in events if item["event_name"] == "displayed_battery_percent")
    assert battery["observed_value"] == 88
    assert all(item["monotonic_ns"] > 0 for item in events)
    notification = json.loads((tmp_path / "notifications.jsonl").read_text())
    assert notification["wall_time_utc"] == "2026-07-18T00:00:00+00:00"
    assert notification["monotonic_ns"] == 10_000
    assert notification["characteristic_uuid"] == POCKET3_PROFILE.notification_uuid
    assert notification["duml_cmd_set"] == 0x0D
    assert notification["duml_cmd_id"] == 0x02
    assert notification["payload_hex"] is not None


def test_ctrl_c_cancellation_saves_and_disconnects(tmp_path: Path) -> None:
    FakeTransport.instances.clear()

    async def scenario():
        task = asyncio.create_task(
            run_passive_experiment(
                "fixture",
                duration=60,
                output_dir=tmp_path,
                transport_factory=FakeTransport,
                key_reader_factory=BlockingReader,
            )
        )
        await asyncio.sleep(0.01)
        task.cancel()
        return await task

    session, ok = asyncio.run(scenario())
    assert not ok
    assert session["stop_reason"] == "ctrl_c"
    assert session["fff5_write_count"] == 0
    assert (tmp_path / "session.json").exists()
    assert (tmp_path / "message-counts.json").exists()
    assert FakeTransport.instances[-1].disconnect_called


def test_nonzero_fff5_counter_fails_closed(tmp_path: Path) -> None:
    class UnsafeCounterTransport(FakeTransport):
        fff5_count = 1

    session, ok = asyncio.run(
        run_passive_experiment(
            "fixture",
            duration=1,
            output_dir=tmp_path,
            transport_factory=UnsafeCounterTransport,
            key_reader_factory=lambda: ScriptedReader(["r", "q"]),
        )
    )
    assert not ok
    assert session["safety_assertion_passed"] is False
    assert "SAFETY FAILURE" in session["error"]


def test_device_disconnect_is_preserved_in_session(tmp_path: Path) -> None:
    class DisconnectedTransport(FakeTransport):
        disconnected_during_capture = True

    session, _ok = asyncio.run(
        run_passive_experiment(
            "fixture",
            duration=1,
            output_dir=tmp_path,
            transport_factory=DisconnectedTransport,
            key_reader_factory=lambda: ScriptedReader(["r", "q"]),
        )
    )
    assert session["disconnect_count"] == 1
    assert session["connected_at_end_of_capture"] is False
    assert session["fff5_write_count"] == 0


def test_experiment_cli_refuses_without_tty(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(sys.stdin, "isatty", lambda: False)
    status = cli_main(
        [
            "pocket3",
            "experiment",
            "--address",
            "fixture",
            "--duration",
            "1",
            "--output",
            str(tmp_path),
        ]
    )
    assert status == 4
    assert not any(tmp_path.iterdir())
