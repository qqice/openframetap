from __future__ import annotations

import asyncio
import json
from pathlib import Path
from types import SimpleNamespace
import sys

import pytest

from openframetap.devices.pocket3 import POCKET3_PROFILE, build_set_pairing_pin_frame
from openframetap.protocol.commands import PAIRING_COMMANDS, CommandRejected
from openframetap.protocol.duml import decode_duml_frame, encode_duml_frame
from openframetap.telemetry.decoder import decode_telemetry
from openframetap.telemetry.recorder import TelemetryRecorder
from openframetap.transport.bluez_ble import (
    BluezBleTransport,
    NotificationRecord,
    classify_ble_error,
)

LIVE_FIXTURE = Path(__file__).parent / "fixtures" / "live_fff4_frames.json"


def test_ble_error_categories() -> None:
    assert classify_ble_error(PermissionError("Not authorized")) == "bluez_permission"
    assert classify_ble_error(TimeoutError("connection timeout")) == "connection_timeout"
    assert classify_ble_error(RuntimeError("device busy")) == "connection_occupied"


def test_transport_rejects_before_touching_client() -> None:
    transport = BluezBleTransport("fixture", POCKET3_PROFILE)
    with pytest.raises(CommandRejected):
        asyncio.run(
            transport.send_frame(
                build_set_pairing_pin_frame(),
                command=PAIRING_COMMANDS["set_pairing_pin"],
                authorization=None,
            )
        )


def test_requested_disconnect_is_not_an_interruption(monkeypatch) -> None:
    class Characteristic:
        def __init__(self, uuid: str, handle: int, properties: list[str]) -> None:
            self.uuid = uuid
            self.handle = handle
            self.properties = properties

    fff4 = Characteristic(POCKET3_PROFILE.notification_uuid, 44, ["notify"])
    fff5 = Characteristic(POCKET3_PROFILE.write_uuid, 47, ["write-without-response"])

    class Services:
        def get_characteristic(self, uuid: str):
            return fff4 if uuid == fff4.uuid else fff5 if uuid == fff5.uuid else None

    class Client:
        def __init__(self, *_args, disconnected_callback=None, **_kwargs) -> None:
            self.disconnected_callback = disconnected_callback
            self.is_connected = False
            self.services = Services()
            self.mtu_size = 23

        async def connect(self) -> None:
            self.is_connected = True

        async def start_notify(self, *_args) -> None:
            pass

        async def stop_notify(self, *_args) -> None:
            pass

        async def disconnect(self) -> None:
            self.is_connected = False
            self.disconnected_callback(self)

    monkeypatch.setitem(sys.modules, "bleak", SimpleNamespace(BleakClient=Client))
    events = []
    transport = BluezBleTransport("fixture", POCKET3_PROFILE, event_handler=events.append)

    async def scenario() -> None:
        await transport.connect()
        await transport.subscribe(lambda _notification: None)
        await transport.disconnect()

    asyncio.run(scenario())
    assert transport.disconnect_count == 0
    assert transport.active_disconnect_count == 0
    assert transport.setup_disconnect_count == 0
    callback = [event for event in events if event["event"] == "disconnected_callback"]
    assert len(callback) == 1
    assert callback[0]["intentional"] is True


def test_battery_decoder_keeps_raw_payload() -> None:
    payload = bytearray(34)
    payload[20] = 88
    raw = encode_duml_frame(
        sender=5,
        receiver=2,
        sequence=1,
        flags=0,
        cmd_set=0x0D,
        cmd_id=0x02,
        payload=payload,
    )
    decoded = decode_telemetry(decode_duml_frame(raw))
    assert decoded.message_type == "battery_status_candidate"
    assert decoded.fields["battery_percent_candidate"] == 88
    assert decoded.raw_payload_hex == bytes(payload).hex()


def test_unknown_telemetry_is_lossless() -> None:
    raw = encode_duml_frame(
        sender=1,
        receiver=2,
        sequence=2,
        flags=0,
        cmd_set=0xFE,
        cmd_id=0xDC,
        payload=b"\x00\xffunknown",
    )
    decoded = decode_telemetry(decode_duml_frame(raw))
    assert not decoded.known
    assert decoded.message_type == "unknown_fe_dc"
    assert decoded.raw_payload_hex == "00ff756e6b6e6f776e"


@pytest.mark.parametrize(
    "entry",
    json.loads(LIVE_FIXTURE.read_text(encoding="utf-8"))["frames"],
    ids=lambda entry: entry["name"],
)
def test_live_fff4_fixture_replay(entry: dict) -> None:
    frame = decode_duml_frame(bytes.fromhex(entry["hex"]))
    assert frame.crc8_valid and frame.crc16_valid
    decoded = decode_telemetry(frame)
    assert decoded.message_type == entry["expected_type"]
    if decoded.message_type == "battery_status_candidate":
        assert decoded.fields["battery_percent_candidate"] == 100
    if decoded.message_type == "device_info_candidate":
        assert decoded.fields["model_or_product_ascii"] == "hg212"


def test_recorder_serializes_notification_frame_and_summary(tmp_path) -> None:
    raw = encode_duml_frame(
        sender=1,
        receiver=2,
        sequence=3,
        flags=0,
        cmd_set=0xFE,
        cmd_id=0xDC,
        payload=b"raw",
    )
    recorder = TelemetryRecorder(tmp_path, address="fixture", operation="offline-test")
    notification = NotificationRecord(
        wall_timestamp="2026-07-18T00:00:00+00:00",
        monotonic_ns=123,
        characteristic_uuid=POCKET3_PROFILE.notification_uuid,
        characteristic_handle=44,
        data=raw,
    )
    asyncio.run(recorder.record_notification(notification))
    summary = recorder.finalize(
        actual_seconds=1.0,
        disconnect_count=0,
        connected=True,
    )
    assert summary["notification_count"] == 1
    assert summary["duml_frame_count"] == 1
    assert summary["unknown_message_types"] == {"FE/DC": 1}
    stored = json.loads((tmp_path / "summary.json").read_text(encoding="utf-8"))
    assert stored["writes_attempted"] == 0
    assert "00ff" not in (tmp_path / "decoded-telemetry.jsonl").read_text(encoding="utf-8")
    assert raw.hex() in (tmp_path / "unknown-frames.jsonl").read_text(encoding="utf-8")
