from __future__ import annotations

import asyncio
from dataclasses import replace
import socket
from pathlib import Path
import subprocess
from types import SimpleNamespace

import pytest

from openframetap.control.gimbal_controller import (
    GimbalUdpController,
    WifiControlPrerequisites,
    WifiGimbalState,
)
from openframetap.control.gimbal_profile import (
    CENTER_STICK_COMMAND,
    Pocket3StickCommand,
    validate_stick_duml,
)
from openframetap.protocol.dji_wifi import DjiWifiOperatorSequencer
from openframetap.protocol.duml import encode_duml_frame
from openframetap.transport.dji_wifi_udp import (
    DjiWifiHandshakeProfile,
    DjiWifiUdpTransport,
    parse_rtmp_publisher_ips,
    validate_pocket_target_ip,
)
from openframetap.workflows import pocket3_wifi_gimbal as workflow


ALL_READY = WifiControlPrerequisites(*([True] * 9))


class FakeSink:
    def __init__(self, fail_at: int | None = None) -> None:
        self.commands: list[Pocket3StickCommand] = []
        self.reasons: list[str] = []
        self.fail_at = fail_at

    async def send_stick(self, command: Pocket3StickCommand, *, reason: str) -> dict:
        if self.fail_at is not None and len(self.commands) + 1 == self.fail_at:
            raise OSError("simulated UDP failure")
        self.commands.append(command)
        self.reasons.append(reason)
        return {"pitch": command.pitch, "yaw": command.yaw}


async def no_sleep(_seconds: float) -> None:
    return None


def test_center_payload_and_structured_duml_are_exact() -> None:
    assert CENTER_STICK_COMMAND.encode_payload().hex() == "00040000000400804200"
    frame = CENTER_STICK_COMMAND.encode_duml(sequence=0x1234)
    assert validate_stick_duml(frame) == CENTER_STICK_COMMAND


def test_fixed_fields_roll_ranges_and_axes_fail_closed() -> None:
    with pytest.raises(ValueError, match="roll"):
        Pocket3StickCommand(roll=1)
    with pytest.raises(ValueError, match="fixed fields"):
        Pocket3StickCommand(fixed_0042=0x43)
    with pytest.raises(ValueError, match="captured range"):
        Pocket3StickCommand(yaw=2000)
    with pytest.raises(ValueError, match="-1.0"):
        Pocket3StickCommand.from_axes(yaw_axis=1.1, pitch_axis=0)
    with pytest.raises(ValueError, match="max_offset"):
        Pocket3StickCommand.from_axes(yaw_axis=1, pitch_axis=0, max_offset=96)


def test_04_50_and_unknown_commands_are_rejected() -> None:
    forbidden = encode_duml_frame(
        sender=2, receiver=4, sequence=1, flags=0, cmd_set=4, cmd_id=0x50,
        payload=b"\x01\x04\x05",
    )
    with pytest.raises(ValueError, match="04/50"):
        validate_stick_duml(forbidden)
    unknown = encode_duml_frame(
        sender=2, receiver=4, sequence=1, flags=0, cmd_set=4, cmd_id=2, payload=b"",
    )
    with pytest.raises(ValueError, match="non-allowlisted"):
        validate_stick_duml(unknown)


def test_handshake_is_structured_and_matches_mimo_capture() -> None:
    assert DjiWifiHandshakeProfile().encode().hex() == (
        "3080557000000095a88264006400c0051400006400000190"
        "01c005140000640014006400c00514000064000101040102"
    )


def test_target_and_publisher_parsing_reject_tailscale() -> None:
    text = "0 0 192.168.2.224:1935 192.168.2.1:45678\n"
    assert parse_rtmp_publisher_ips(text) == ("192.168.2.1",)
    with pytest.raises(ValueError, match="Tailscale"):
        validate_pocket_target_ip("100.125.223.67")
    with pytest.raises(ValueError):
        validate_pocket_target_ip("8.8.8.8")


def test_target_port_and_local_port_ownership() -> None:
    with pytest.raises(ValueError, match="fixed at 9004"):
        DjiWifiUdpTransport("192.168.2.1", target_port=9005)
    occupied = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    occupied.bind(("0.0.0.0", 0))
    port = occupied.getsockname()[1]
    try:
        transport = DjiWifiUdpTransport("192.168.2.1", local_port=port)
        with pytest.raises(OSError):
            asyncio.run(transport.open(handshake_timeout=0.01))
    finally:
        occupied.close()


def test_transport_accepts_only_structured_command_and_single_writer() -> None:
    async def scenario() -> None:
        transport = DjiWifiUdpTransport("192.168.2.1")
        transport.socket = object()  # _sendto is replaced; no real network I/O.
        transport.sequencer = DjiWifiOperatorSequencer(0x7055, 8, 0)
        gate = asyncio.Event()

        async def blocked_send(_data: bytes) -> None:
            await gate.wait()

        transport._sendto = blocked_send  # type: ignore[method-assign]
        first = asyncio.create_task(
            transport.send_stick(CENTER_STICK_COMMAND, reason="first")
        )
        await asyncio.sleep(0)
        with pytest.raises(RuntimeError, match="single-writer"):
            await transport.send_stick(CENTER_STICK_COMMAND, reason="second")
        with pytest.raises(TypeError, match="Pocket3StickCommand"):
            await transport.send_stick(b"raw hex", reason="forbidden")  # type: ignore[arg-type]
        gate.set()
        await first

    asyncio.run(scenario())


def test_center_test_and_pulse_always_end_at_center() -> None:
    async def scenario() -> None:
        sink = FakeSink()
        controller = GimbalUdpController(sink, sleep=no_sleep)
        await controller.center_burst(3, reason="center-test")
        assert controller.state == WifiGimbalState.CENTERED
        assert all(command.is_center for command in sink.commands)
        await controller.arm(ALL_READY)
        await controller.pulse(axis="yaw", direction="positive")
        assert controller.state == WifiGimbalState.CENTERED
        assert controller.non_center_packets == 2
        assert controller.center_packets == 3 + 3 + 5 + 1
        assert sink.commands[-1].is_center
        assert [item.yaw for item in sink.commands if not item.is_center] == [1040, 1040]

    asyncio.run(scenario())


def test_missing_prerequisite_and_unbounded_pulse_are_refused() -> None:
    async def scenario() -> None:
        controller = GimbalUdpController(FakeSink(), sleep=no_sleep)
        with pytest.raises(RuntimeError, match="missing"):
            await controller.arm(replace(ALL_READY, ble_telemetry_online=False))
        await controller.arm(ALL_READY)
        with pytest.raises(ValueError, match="fixed"):
            await controller.pulse(axis="yaw", direction="positive", offset=17)

    asyncio.run(scenario())


def test_watchdog_and_stop_emit_redundant_center_and_disable() -> None:
    async def scenario() -> None:
        sink = FakeSink()
        controller = GimbalUdpController(sink, sleep=no_sleep)
        await controller.arm(ALL_READY)
        await controller.watchdog_stop()
        assert controller.watchdog_triggered
        assert sink.commands[-1].is_center
        await controller.emergency_stop("STOP")
        assert controller.emergency_stop_triggered
        assert controller.state == WifiGimbalState.DISABLED
        assert sink.commands[-1].is_center

    asyncio.run(scenario())


def test_250ms_watchdog_cancels_stalled_writer_then_centers() -> None:
    class StallingSink(FakeSink):
        async def send_stick(self, command, *, reason):
            if not command.is_center:
                await asyncio.Event().wait()
            return await super().send_stick(command, reason=reason)

    async def scenario() -> None:
        sink = StallingSink()
        controller = GimbalUdpController(sink, sleep=no_sleep)
        await controller.start_watchdog()
        await controller.arm(ALL_READY)
        with pytest.raises(RuntimeError, match="watchdog timeout"):
            await controller.pulse(axis="yaw", direction="positive")
        assert controller.watchdog_triggered
        assert controller.state == WifiGimbalState.FAULT
        assert sink.commands[-1].is_center
        assert len([command for command in sink.commands if command.is_center]) >= 9
        await controller.stop_watchdog()

    asyncio.run(scenario())


def test_udp_failure_enters_fault() -> None:
    async def scenario() -> None:
        controller = GimbalUdpController(FakeSink(fail_at=1), sleep=no_sleep)
        with pytest.raises(OSError):
            await controller.center_burst(3, reason="startup")
        assert controller.state == WifiGimbalState.FAULT

    asyncio.run(scenario())


def test_center_workflow_serializes_summary_and_never_writes_fff5(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    class FakeBle:
        def __init__(self, *_args, **_kwargs) -> None:
            self.disconnect_count = 0
            self.fff5_write_count = 0

        async def connect(self) -> None: pass
        async def acquire_mtu(self) -> int: return 517

        async def subscribe(self, callback) -> None:
            raw = encode_duml_frame(
                sender=4, receiver=2, sequence=1, flags=0x40,
                cmd_set=4, cmd_id=5, payload=b"\0" * 24,
            )
            record = SimpleNamespace(
                data=raw,
                wall_timestamp="2026-01-01T00:00:00+00:00",
                monotonic_ns=1,
                to_dict=lambda: {"data_hex": raw.hex(), "monotonic_ns": 1},
            )
            result = callback(record)
            if result is not None:
                await result

        async def disconnect(self) -> None: pass

    class FakeUdp:
        def __init__(self, target_ip, *, local_port, event_handler) -> None:
            self.target_ip = target_ip
            self.local_port = local_port
            self.event_handler = event_handler
            self.is_open = False
            self.sequence = 0
            self.receive_count = 0

        async def open(self) -> None: self.is_open = True

        async def send_stick(self, command, *, reason):
            duml = command.encode_duml(sequence=self.sequence)
            self.sequence += 1
            event = {
                "wall_time_utc": "2026-01-01T00:00:00+00:00",
                "monotonic_ns": self.sequence,
                "kind": "stick_center" if command.is_center else "stick_non_center",
                "reason": reason,
                "duml_hex": duml.hex(),
            }
            self.event_handler(event)
            return event

        async def close(self) -> None: self.is_open = False

        async def receive_datagram(self):
            if self.receive_count == 0:
                self.receive_count += 1
                raw = encode_duml_frame(
                    sender=4, receiver=2, sequence=2, flags=0,
                    cmd_set=4, cmd_id=5, payload=b"\0" * 24,
                )
                return SimpleNamespace(
                    data=raw,
                    wall_time_utc="2026-01-01T00:00:01+00:00",
                    monotonic_ns=2,
                    to_dict=lambda: {"data_hex": raw.hex(), "monotonic_ns": 2},
                )
            await asyncio.Event().wait()

    async def instant_sleep(_seconds: float) -> None: pass

    monkeypatch.setattr(workflow, "discover_rtmp_publisher_ip", lambda: "192.168.2.1")
    monkeypatch.setattr(
        workflow.subprocess,
        "run",
        lambda *args, **kwargs: subprocess.CompletedProcess(args[0], 0, "publisher\n", ""),
    )
    output = tmp_path / "artifacts" / "private" / "center"
    summary = asyncio.run(
        workflow.run_wifi_gimbal_test(
            mode="center-test",
            address="00:11:22:33:44:55",
            output_dir=output,
            software_git_head="abc",
            mimo_closed=True,
            transport_factory=FakeUdp,
            ble_transport_factory=FakeBle,
            sleep=instant_sleep,
        )
    )
    assert summary["error"] is None
    assert summary["center_packets"] == 8
    assert summary["non_center_packets"] == 0
    assert summary["fff5_write_count"] == 0
    assert summary["04_50_send_count"] == 0
    assert summary["udp_gimbal_telemetry_online_before_control"] is True
    assert (output / "summary.json").is_file()
    assert (output / "sent-datagrams.jsonl").read_text().count("stick_center") == 8
