"""Fail-closed single-writer controller for Pocket 3 UDP stick commands."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from enum import Enum
import time
from typing import Awaitable, Callable, Protocol

from openframetap.control.gimbal_profile import (
    CENTER_STICK_COMMAND,
    INITIAL_TEST_MAX_OFFSET,
    Pocket3StickCommand,
)


class WifiGimbalState(str, Enum):
    DISABLED = "disabled"
    CENTERED = "centered"
    ARMED = "armed"
    ACTIVE = "active"
    STOPPING = "stopping"
    FAULT = "fault"


@dataclass(frozen=True, slots=True)
class WifiControlPrerequisites:
    pocket_ip_confirmed: bool = False
    rtmp_publisher_online: bool = False
    ble_telemetry_online: bool = False
    udp_socket_ready: bool = False
    envelope_validated: bool = False
    center_payload_validated: bool = False
    watchdog_running: bool = False
    single_writer_acquired: bool = False
    mimo_absent: bool = False

    def missing(self) -> tuple[str, ...]:
        return tuple(name for name in self.__slots__ if not getattr(self, name))


@dataclass(frozen=True, slots=True)
class WifiGimbalConfig:
    control_rate_hz: float = 10.0
    input_watchdog_ms: int = 250
    maximum_non_center_ms: int = 250
    absolute_hard_limit_ms: int = 500
    initial_max_offset: int = INITIAL_TEST_MAX_OFFSET

    def __post_init__(self) -> None:
        if self.control_rate_hz != 10.0:
            raise ValueError("initial Wi-Fi control rate is fixed at 10 Hz")
        if self.input_watchdog_ms != 250:
            raise ValueError("initial input watchdog is fixed at 250 ms")
        if not 0 < self.maximum_non_center_ms <= 250:
            raise ValueError("maximum non-center duration exceeds 250 ms")
        if self.absolute_hard_limit_ms != 500:
            raise ValueError("absolute hard limit is fixed at 500 ms")
        if self.initial_max_offset != INITIAL_TEST_MAX_OFFSET:
            raise ValueError("initial one-shot max offset is fixed at 16")


class StickSink(Protocol):
    async def send_stick(self, command: Pocket3StickCommand, *, reason: str) -> dict: ...


TransitionHandler = Callable[[dict], None]
Sleep = Callable[[float], Awaitable[None]]


class GimbalUdpController:
    """The only task allowed to convert control intent into UDP sends."""

    def __init__(
        self,
        sink: StickSink,
        *,
        config: WifiGimbalConfig | None = None,
        sleep: Sleep = asyncio.sleep,
        clock_ns: Callable[[], int] = time.monotonic_ns,
        on_transition: TransitionHandler | None = None,
    ) -> None:
        self.sink = sink
        self.config = config or WifiGimbalConfig()
        self.sleep = sleep
        self.clock_ns = clock_ns
        self.on_transition = on_transition or (lambda event: None)
        self.state = WifiGimbalState.DISABLED
        self.center_packets = 0
        self.non_center_packets = 0
        self.watchdog_triggered = False
        self.emergency_stop_triggered = False
        self.last_center_ns: int | None = None
        self.last_send_ns: int | None = None
        self.non_center_started_ns: int | None = None
        self.last_valid_input_ns: int | None = None
        self.fault_reason: str | None = None
        self._operation_lock = asyncio.Lock()
        self._watchdog_task: asyncio.Task | None = None
        self._watchdog_expired = asyncio.Event()

    @property
    def watchdog_running(self) -> bool:
        return bool(self._watchdog_task and not self._watchdog_task.done())

    @property
    def watchdog_expired(self) -> bool:
        return self._watchdog_expired.is_set()

    def _transition(self, target: WifiGimbalState, reason: str) -> None:
        previous = self.state
        self.state = target
        self.on_transition(
            {
                "monotonic_ns": self.clock_ns(),
                "from": previous.value,
                "to": target.value,
                "reason": reason,
            }
        )

    async def _send(self, command: Pocket3StickCommand, reason: str) -> dict:
        try:
            record = await self.sink.send_stick(command, reason=reason)
        except BaseException as exc:
            self.fault_reason = f"{type(exc).__name__}: {exc}"
            self._transition(WifiGimbalState.FAULT, "udp_send_failure")
            raise
        if command.is_center:
            self.center_packets += 1
            self.last_center_ns = self.clock_ns()
        else:
            self.non_center_packets += 1
            self.last_valid_input_ns = self.clock_ns()
            if self.non_center_started_ns is None:
                self.non_center_started_ns = self.clock_ns()
        self.last_send_ns = self.clock_ns()
        return record

    async def center_burst(
        self,
        count: int,
        *,
        interval_seconds: float = 0.05,
        reason: str,
    ) -> None:
        if count < 1:
            raise ValueError("center burst must contain at least one packet")
        self._transition(WifiGimbalState.STOPPING, reason)
        for index in range(count):
            await self._send(CENTER_STICK_COMMAND, reason)
            if index + 1 < count:
                await self.sleep(interval_seconds)
        self.non_center_started_ns = None
        self.last_valid_input_ns = None
        self._transition(WifiGimbalState.CENTERED, f"center_complete:{reason}")

    async def start_watchdog(self) -> None:
        if self.watchdog_running:
            raise RuntimeError("watchdog is already running")

        async def monitor() -> None:
            while True:
                await asyncio.sleep(0.025)
                if self.state != WifiGimbalState.ACTIVE:
                    continue
                now = self.clock_ns()
                if (
                    self.non_center_started_ns is not None
                    and now - self.non_center_started_ns
                    >= self.config.absolute_hard_limit_ms * 1_000_000
                ):
                    self.watchdog_triggered = True
                    self.fault_reason = "absolute hard limit reached"
                    self._watchdog_expired.set()
                    continue
                if (
                    self.last_valid_input_ns is not None
                    and now - self.last_valid_input_ns
                    > self.config.input_watchdog_ms * 1_000_000
                ):
                    self.watchdog_triggered = True
                    self._watchdog_expired.set()

        self._watchdog_task = asyncio.create_task(monitor(), name="gimbal-udp-watchdog")
        await asyncio.sleep(0)

    async def stop_watchdog(self) -> None:
        task = self._watchdog_task
        self._watchdog_task = None
        if task is not None:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

    async def arm(self, prerequisites: WifiControlPrerequisites) -> None:
        async with self._operation_lock:
            missing = prerequisites.missing()
            if missing:
                raise RuntimeError("cannot arm Wi-Fi gimbal control; missing: " + ", ".join(missing))
            if self.state not in {WifiGimbalState.DISABLED, WifiGimbalState.CENTERED}:
                raise RuntimeError(f"cannot arm from {self.state.value}")
            await self.center_burst(3, reason="session_start")
            await self.sleep(0.5)
            self._transition(WifiGimbalState.ARMED, "prerequisites_and_center_confirmed")

    async def pulse(
        self,
        *,
        axis: str,
        direction: str,
        offset: int = INITIAL_TEST_MAX_OFFSET,
        frames: int = 2,
        rate_hz: float = 10.0,
    ) -> None:
        async with self._operation_lock:
            if self.state != WifiGimbalState.ARMED:
                raise RuntimeError("one-shot pulse requires armed state")
            if axis not in {"yaw", "pitch"} or direction not in {"positive", "negative"}:
                raise ValueError("pulse axis/direction is not allowlisted")
            if offset != INITIAL_TEST_MAX_OFFSET or frames != 2 or rate_hz != 10.0:
                raise ValueError("initial pulse is fixed to offset 16, two frames, 10 Hz")
            sign = 1.0 if direction == "positive" else -1.0
            command = Pocket3StickCommand.from_axes(
                yaw_axis=sign if axis == "yaw" else 0.0,
                pitch_axis=sign if axis == "pitch" else 0.0,
                max_offset=offset,
            )
            self._transition(WifiGimbalState.ACTIVE, f"pulse:{axis}:{direction}")
            started = self.clock_ns()
            self._watchdog_expired.clear()

            async def emit_non_center() -> None:
                for index in range(frames):
                    if self._watchdog_expired.is_set():
                        raise TimeoutError("input watchdog expired")
                    if self.clock_ns() - started >= self.config.absolute_hard_limit_ms * 1_000_000:
                        raise TimeoutError("absolute hard limit reached")
                    await self._send(command, "one_shot_pulse")
                    if index + 1 < frames:
                        await self.sleep(1.0 / rate_hz)

            try:
                await asyncio.wait_for(
                    emit_non_center(), self.config.maximum_non_center_ms / 1000
                )
            except TimeoutError as exc:
                self.watchdog_triggered = True
                self.fault_reason = str(exc) or "input watchdog timeout"
                await self.center_burst(5, reason="input_watchdog_250ms")
                await self.sleep(0.3)
                await self.center_burst(1, reason="watchdog_final_center")
                self._transition(WifiGimbalState.FAULT, "bounded_pulse_timeout")
                raise RuntimeError(self.fault_reason) from exc
            await self.center_burst(5, reason="pulse_complete")
            await self.sleep(0.3)
            await self.center_burst(1, reason="final_center")
            await self.sleep(2.0)

    async def watchdog_stop(self) -> None:
        async with self._operation_lock:
            self.watchdog_triggered = True
            await self.center_burst(5, reason="input_watchdog_250ms")
            await self.sleep(0.3)
            await self.center_burst(1, reason="watchdog_final_center")

    async def send_live_axes(
        self,
        *,
        yaw: float,
        pitch: float,
        maximum_input: float = 0.20,
        max_offset: int = 32,
    ) -> None:
        if self.state not in {WifiGimbalState.ARMED, WifiGimbalState.ACTIVE}:
            raise RuntimeError(f"live axes require armed state, found {self.state.value}")
        if not -maximum_input <= yaw <= maximum_input or not -maximum_input <= pitch <= maximum_input:
            raise ValueError("live input exceeds configured normalized maximum")
        command = Pocket3StickCommand.from_axes(
            yaw_axis=yaw / maximum_input,
            pitch_axis=pitch / maximum_input,
            max_offset=max_offset,
        )
        if command.is_center:
            raise ValueError("live non-center send received centered input")
        if self.state != WifiGimbalState.ACTIVE:
            self._transition(WifiGimbalState.ACTIVE, "live_input_active")
        await self._send(command, "live_input")

    async def release_live(self, reason: str) -> None:
        await self.center_burst(5, reason=reason)
        await self.sleep(0.3)
        await self.center_burst(1, reason=f"{reason}_final_center")
        self._transition(WifiGimbalState.ARMED, f"rearmed_after:{reason}")

    async def emergency_stop(self, reason: str = "emergency_stop") -> None:
        async with self._operation_lock:
            self.emergency_stop_triggered = True
            await self.center_burst(5, reason=reason)
            await self.sleep(0.3)
            await self.center_burst(1, reason=f"{reason}_final_center")
            self._transition(WifiGimbalState.DISABLED, reason)

    async def close(self, reason: str = "controller_close") -> None:
        if self.state == WifiGimbalState.FAULT:
            return
        await self.emergency_stop(reason)
