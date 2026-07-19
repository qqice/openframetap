from __future__ import annotations

import asyncio

import pytest

from openframetap.app.input import ControlInput
from openframetap.control.policy import validate_gimbal_control_frame
from openframetap.control.safety import (
    ControlConfig,
    ControlPrerequisites,
    ControlState,
    FailClosedController,
    MockCommandSink,
)
from openframetap.protocol.commands import CommandRejected
from openframetap.protocol.duml import encode_duml_frame
from openframetap.protocol.gimbal_commands import build_speed_frame


class Clock:
    def __init__(self) -> None:
        self.now = 1_000_000_000

    def __call__(self) -> int:
        return self.now

    def advance_ms(self, value: int) -> None:
        self.now += value * 1_000_000


def ready() -> ControlPrerequisites:
    return ControlPrerequisites(True, True, True, True, True, True, True)


def run(coro):
    return asyncio.run(coro)


def test_arm_requires_every_prerequisite() -> None:
    controller = FailClosedController(MockCommandSink())
    with pytest.raises(RuntimeError, match="ble_connected"):
        controller.arm(ControlPrerequisites())
    controller.arm(ready())
    assert controller.state == ControlState.ARMED


def test_rate_limit_and_input_release_zero() -> None:
    clock, sink = Clock(), MockCommandSink()
    controller = FailClosedController(sink, clock_ns=clock)
    controller.arm(ready())
    controller.submit(ControlInput(yaw=0.1, active=True, source="keyboard"))
    run(controller.tick())
    clock.advance_ms(50)
    run(controller.tick())
    assert len(sink.records) == 1
    controller.submit(ControlInput(source="keyboard"))
    run(controller.tick())
    assert sink.records[-1]["is_zero"] is True
    assert controller.state == ControlState.ARMED


def test_watchdog_always_returns_to_zero() -> None:
    clock, sink = Clock(), MockCommandSink()
    controller = FailClosedController(sink, clock_ns=clock)
    controller.arm(ready())
    controller.submit(ControlInput(yaw=0.1, active=True, source="touch"))
    run(controller.tick())
    clock.advance_ms(301)
    run(controller.tick())
    assert sink.records[-1]["is_zero"] is True
    assert controller.watchdog_state == "expired"


def test_maximum_movement_requires_neutral_before_retrigger() -> None:
    clock, sink = Clock(), MockCommandSink()
    controller = FailClosedController(sink, clock_ns=clock)
    controller.arm(ready())
    controller.submit(ControlInput(yaw=0.1, active=True, source="touch"))
    run(controller.tick())
    clock.advance_ms(250)
    controller.submit(ControlInput(yaw=0.1, active=True, source="touch"))
    run(controller.tick())
    clock.advance_ms(250)
    controller.submit(ControlInput(yaw=0.1, active=True, source="touch"))
    run(controller.tick())
    count = len(sink.records)
    assert sink.records[-1]["is_zero"] is True
    assert controller.neutral_required
    controller.submit(ControlInput(yaw=0.1, active=True, source="touch"))
    clock.advance_ms(100)
    run(controller.tick())
    assert len(sink.records) == count
    controller.submit(ControlInput(source="touch"))
    assert controller.neutral_required is False


@pytest.mark.parametrize("operation", ["emergency", "focus", "disconnect", "stop"])
def test_all_safety_paths_zero(operation: str) -> None:
    clock, sink = Clock(), MockCommandSink()
    controller = FailClosedController(sink, clock_ns=clock)
    controller.arm(ready())
    controller.submit(ControlInput(pitch=0.1, active=True, source="keyboard"))
    run(controller.tick())
    if operation == "emergency":
        run(controller.emergency_stop())
    elif operation == "focus":
        run(controller.focus_lost())
    elif operation == "disconnect":
        run(controller.disconnect())
    else:
        run(controller.stop())
    assert sink.records[-1]["is_zero"] is True
    assert controller.state in {ControlState.DISABLED, ControlState.DISCONNECTED}


def test_send_failure_attempts_zero_then_faults_closed() -> None:
    clock, sink = Clock(), MockCommandSink(fail_calls={1})
    controller = FailClosedController(sink, clock_ns=clock)
    controller.arm(ready())
    controller.submit(ControlInput(yaw=0.1, active=True, source="mock"))
    run(controller.tick())
    assert controller.state == ControlState.FAULT
    assert sink.records[-1]["is_zero"] is True


def test_zero_retry_limit_faults_without_looping() -> None:
    sink = MockCommandSink(fail_calls={1, 2, 3, 4})
    controller = FailClosedController(sink, config=ControlConfig(zero_retry_limit=2))
    controller.arm(ready())
    assert run(controller.stop()) is False
    assert controller.state == ControlState.FAULT
    assert sink.calls == 2


def test_mock_never_writes_fff5() -> None:
    sink = MockCommandSink()
    controller = FailClosedController(sink)
    controller.arm(ready())
    controller.submit(ControlInput(yaw=0.1, active=True, source="mock"))
    run(controller.tick())
    run(controller.stop())
    assert sink.fff5_write_count == 0
    assert all(record["would_write_fff5"] is False for record in sink.records)


def test_policy_rejects_raw_pwm_and_unknown_commands() -> None:
    def frame(cmd_id: int) -> bytes:
        return encode_duml_frame(
            sender=2,
            receiver=4,
            sequence=1,
            flags=0x40,
            cmd_set=4,
            cmd_id=cmd_id,
            payload=b"\x00" * (6 if cmd_id == 1 else 7),
        )

    with pytest.raises(CommandRejected, match="raw PWM"):
        validate_gimbal_control_frame(frame(1), require_live_validated=False)
    with pytest.raises(CommandRejected, match="unknown"):
        validate_gimbal_control_frame(frame(0x55), require_live_validated=False)


def test_live_requires_validated_profile() -> None:
    raw = build_speed_frame(sequence=1, yaw=0.05, pitch=0.0, live=True)
    with pytest.raises(CommandRejected, match="validated profile"):
        validate_gimbal_control_frame(raw, require_live_validated=True)


def test_single_writer_guard() -> None:
    async def scenario() -> None:
        controller = FailClosedController(MockCommandSink())
        controller.arm(ready())
        controller.submit(ControlInput(yaw=0.1, active=True, source="mock"))
        first = asyncio.create_task(controller.run())
        await asyncio.sleep(0)
        with pytest.raises(RuntimeError, match="single-writer"):
            await controller.run()
        first.cancel()
        with pytest.raises(asyncio.CancelledError):
            await first

    run(scenario())
