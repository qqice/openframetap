from __future__ import annotations

import asyncio
import json
from pathlib import Path
from types import SimpleNamespace
import sys

import pytest

from openframetap.devices.pocket3 import POCKET3_PROFILE, build_set_pairing_pin_frame
from openframetap.cli import main as cli_main
from openframetap.protocol.commands import PAIRING_COMMANDS, CommandRejected
from openframetap.protocol.duml import decode_duml_frame, encode_duml_frame
from openframetap.telemetry.decoder import decode_telemetry
from openframetap.telemetry.recorder import TelemetryRecorder
from openframetap.transport.bluez_ble import (
    BluezBleTransport,
    NotificationRecord,
    classify_ble_error,
)
from openframetap.session import manual_send_pocket3_frame

LIVE_FIXTURE = Path(__file__).parent / "fixtures" / "live_fff4_frames.json"
PAIRING_FIXTURE = Path(__file__).parent / "fixtures" / "manual_pairing_frames.json"
PAIRED_FIXTURE = Path(__file__).parent / "fixtures" / "paired_session_frames.json"


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
    assert transport.cccd_write_count == 2
    assert transport.fff5_write_count == 0
    callback = [event for event in events if event["event"] == "disconnected_callback"]
    assert len(callback) == 1
    assert callback[0]["intentional"] is True


def test_manual_frame_rejects_wrong_human_confirmation_before_transport(tmp_path) -> None:
    def forbidden_factory(*_args, **_kwargs):
        raise AssertionError("transport must not be constructed")

    with pytest.raises(PermissionError, match="SHA-256"):
        asyncio.run(
            manual_send_pocket3_frame(
                "fixture",
                raw=build_set_pairing_pin_frame(),
                command_name="set_pairing_pin",
                confirmed_sha256="0" * 64,
                seconds=1,
                output_dir=tmp_path,
                transport_factory=forbidden_factory,
            )
        )


def test_manual_write_cli_refuses_without_local_human_gate(monkeypatch, tmp_path) -> None:
    monkeypatch.delenv("OPENFRAMETAP_USER_INITIATED", raising=False)
    raw = build_set_pairing_pin_frame()
    import hashlib

    status = cli_main(
        [
            "ble",
            "manual-write",
            "fixture",
            "--hex",
            raw.hex(),
            "--command",
            "set_pairing_pin",
            "--confirmed-sha256",
            hashlib.sha256(raw).hexdigest(),
            "--output-dir",
            str(tmp_path),
        ]
    )
    assert status == 4
    assert not any(tmp_path.iterdir())


def test_manual_pair_session_cli_refuses_without_real_tty(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("OPENFRAMETAP_USER_INITIATED", "1")
    status = cli_main(
        [
            "pocket3",
            "pair",
            "manual-session",
            "fixture",
            "--output-dir",
            str(tmp_path),
            "--telemetry-seconds",
            "0",
        ]
    )
    assert status == 4
    assert not any(tmp_path.iterdir())


def test_manual_mode_sends_exactly_one_confirmed_frame_and_no_followup(tmp_path) -> None:
    sent = []
    ready_frame = encode_duml_frame(
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
            self.event_handler = event_handler
            self.is_connected = False
            self.active_disconnect_count = 0
            self.setup_disconnect_count = 0
            self.disconnect_count = 0

        async def connect(self) -> None:
            self.is_connected = True

        async def subscribe(self, handler) -> None:
            await handler(
                NotificationRecord(
                    wall_timestamp="2026-07-18T00:00:00+00:00",
                    monotonic_ns=1,
                    characteristic_uuid=POCKET3_PROFILE.notification_uuid,
                    characteristic_handle=44,
                    data=ready_frame,
                )
            )

        async def send_frame(self, raw, *, command, authorization) -> None:
            sent.append((bytes(raw), command.name, authorization.approval_reference))

        async def disconnect(self) -> None:
            self.is_connected = False

    raw = build_set_pairing_pin_frame()
    import hashlib

    digest = hashlib.sha256(raw).hexdigest()
    summary, ok = asyncio.run(
        manual_send_pocket3_frame(
            "fixture",
            raw=raw,
            command_name="set_pairing_pin",
            confirmed_sha256=digest,
            seconds=0.001,
            output_dir=tmp_path,
            transport_factory=FakeTransport,
        )
    )
    assert ok
    assert sent == [(raw, "set_pairing_pin", f"manual-frame-sha256:{digest}")]
    assert summary["writes_attempted"] == 1
    assert summary["automatic_follow_up_frames"] == 0
    transmission = json.loads((tmp_path / "transmission.json").read_text(encoding="utf-8"))
    assert transmission["status"] == "single_frame_written"
    assert transmission["follow_up_frames_sent"] == 0


def test_manual_mode_reports_confirmation_but_still_sends_no_followup(tmp_path) -> None:
    sent = []
    request = build_set_pairing_pin_frame()
    request_frame = decode_duml_frame(request)
    ready_frame = encode_duml_frame(
        sender=1,
        receiver=2,
        sequence=1,
        flags=0,
        cmd_set=2,
        cmd_id=0x80,
        payload=b"ready",
    )
    status_frame = encode_duml_frame(
        sender=7,
        receiver=2,
        sequence=request_frame.sequence,
        flags=0xC0,
        cmd_set=7,
        cmd_id=0x45,
        payload=b"\x00\x02",
    )
    approval_frame = encode_duml_frame(
        sender=7,
        receiver=2,
        sequence=0x0400,
        flags=0x40,
        cmd_set=7,
        cmd_id=0x46,
        payload=b"\x01",
    )

    class FakeTransport:
        def __init__(self, _address, _profile, *, event_handler, **_kwargs) -> None:
            self.event_handler = event_handler
            self.handler = None
            self.is_connected = False
            self.active_disconnect_count = 0
            self.setup_disconnect_count = 0
            self.disconnect_count = 0

        async def connect(self) -> None:
            self.is_connected = True

        async def subscribe(self, handler) -> None:
            self.handler = handler
            await self._notify(ready_frame, 1)

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
            sent.append((bytes(raw), command.name))
            await self._notify(status_frame, 2)
            await self._notify(approval_frame, 3)

        async def disconnect(self) -> None:
            self.is_connected = False

    import hashlib

    summary, ok = asyncio.run(
        manual_send_pocket3_frame(
            "fixture",
            raw=request,
            command_name="set_pairing_pin",
            confirmed_sha256=hashlib.sha256(request).hexdigest(),
            seconds=0.001,
            output_dir=tmp_path,
            transport_factory=FakeTransport,
        )
    )
    assert ok
    assert sent == [(request, "set_pairing_pin")]
    assert summary["pairing_status"] == "confirmation_required"
    assert summary["pocket_confirmation_observed"] is True
    assert summary["automatic_follow_up_frames"] == 0


def test_manual_prerequisite_timeout_causes_zero_writes(tmp_path) -> None:
    sent = []
    request = build_set_pairing_pin_frame()
    unrelated = encode_duml_frame(
        sender=1,
        receiver=2,
        sequence=1,
        flags=0,
        cmd_set=2,
        cmd_id=0x80,
        payload=b"ready",
    )
    required = bytes.fromhex("550e046607020100400746019767")

    class FakeTransport:
        def __init__(self, _address, _profile, *, event_handler, **_kwargs) -> None:
            self.is_connected = False
            self.active_disconnect_count = 0
            self.setup_disconnect_count = 0
            self.disconnect_count = 0

        async def connect(self) -> None:
            self.is_connected = True

        async def subscribe(self, handler) -> None:
            await handler(
                NotificationRecord(
                    wall_timestamp="2026-07-18T00:00:00+00:00",
                    monotonic_ns=1,
                    characteristic_uuid=POCKET3_PROFILE.notification_uuid,
                    characteristic_handle=44,
                    data=unrelated,
                )
            )

        async def send_frame(self, *_args, **_kwargs) -> None:
            sent.append(True)

        async def disconnect(self) -> None:
            self.is_connected = False

    import hashlib

    summary, ok = asyncio.run(
        manual_send_pocket3_frame(
            "fixture",
            raw=request,
            command_name="set_pairing_pin",
            confirmed_sha256=hashlib.sha256(request).hexdigest(),
            seconds=0.001,
            output_dir=tmp_path,
            required_incoming_raw=required,
            prerequisite_timeout=0.001,
            transport_factory=FakeTransport,
        )
    )
    assert not ok
    assert sent == []
    assert summary["writes_attempted"] == 0
    assert summary["required_incoming_frame_observed"] is False
    assert "TimeoutError" in summary["error"]


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


@pytest.mark.parametrize(
    "entry",
    json.loads(PAIRING_FIXTURE.read_text(encoding="utf-8"))["frames"],
    ids=lambda entry: entry["name"],
)
def test_manual_pairing_fixture_replay(entry: dict) -> None:
    frame = decode_duml_frame(bytes.fromhex(entry["hex"]))
    assert frame.crc8_valid and frame.crc16_valid
    decoded = decode_telemetry(frame)
    assert decoded.message_type == entry["expected_type"]
    field, expected = entry["expected_field"]
    assert decoded.fields[field] == expected


@pytest.mark.parametrize(
    "entry",
    json.loads(PAIRED_FIXTURE.read_text(encoding="utf-8"))["frames"],
    ids=lambda entry: entry["name"],
)
def test_paired_session_fixture_replay(entry: dict) -> None:
    frame = decode_duml_frame(bytes.fromhex(entry["hex"]))
    assert frame.crc8_valid and frame.crc16_valid
    decoded = decode_telemetry(frame)
    assert decoded.message_type == entry["expected_type"]
    field, expected = entry["expected_field"]
    assert decoded.fields[field] == expected


def test_02_80_is_not_claimed_as_pairing_started() -> None:
    frame = decode_duml_frame(
        bytes.fromhex(
            "554904930102411000028001048000010000000000000000000000000000000000000000000000000000000246000001000000000000000000000000000000000000000001000017fc"
        )
    )
    decoded = decode_telemetry(frame)
    assert decoded.message_type == "camera_status_02_80_candidate"
    assert decoded.confidence == "low"
    assert decoded.fields["pairing_started_interpretation_rejected"] is True


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
