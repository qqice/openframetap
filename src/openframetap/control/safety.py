"""Deterministic fail-closed yaw/pitch control state machine."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from enum import Enum
import asyncio
import time
from typing import Awaitable, Callable, Protocol

from openframetap.app.input import ControlInput
from openframetap.control.policy import validate_gimbal_control_frame
from openframetap.protocol.gimbal_commands import (
    POCKET3_SPEED_PROFILE,
    Pocket3GimbalSpeedProfile,
    build_speed_frame,
    build_zero_frame,
)


class ControlState(str, Enum):
    DISABLED = "disabled"
    ARMED = "armed"
    ACTIVE = "active"
    STOPPING = "stopping"
    FAULT = "fault"
    DISCONNECTED = "disconnected"


@dataclass(frozen=True, slots=True)
class ControlPrerequisites:
    ble_connected: bool = False
    application_paired: bool = False
    notifications_active: bool = False
    profile_validated: bool = False
    emergency_stop_available: bool = False
    watchdog_running: bool = False
    ui_focused: bool = False

    def missing(self) -> tuple[str, ...]:
        return tuple(name for name, value in asdict(self).items() if not value)


@dataclass(frozen=True, slots=True)
class ControlConfig:
    frequency_hz: float = 10.0
    watchdog_ms: int = 300
    maximum_movement_ms: int = 500
    max_axis: float = 0.15
    zero_retry_limit: int = 2

    def __post_init__(self) -> None:
        if not 0 < self.frequency_hz <= 20:
            raise ValueError("frequency_hz must be in (0, 20]")
        if not 0 < self.watchdog_ms <= 300:
            raise ValueError("watchdog_ms must be in (0, 300]")
        if not 0 < self.maximum_movement_ms <= 2000:
            raise ValueError("maximum_movement_ms must be in (0, 2000]")
        if not 0 < self.max_axis <= 0.15:
            raise ValueError("max_axis must be in (0, 0.15]")
        if not 1 <= self.zero_retry_limit <= 3:
            raise ValueError("zero_retry_limit must be in [1, 3]")


class CommandSink(Protocol):
    async def send(self, frame: bytes, *, is_zero: bool, reason: str) -> None: ...


class MockCommandSink:
    """Records exact would-send frames while guaranteeing zero FFF5 writes."""

    def __init__(self, *, fail_calls: set[int] | None = None) -> None:
        self.fail_calls = set(fail_calls or ())
        self.calls = 0
        self.records: list[dict] = []
        self.fff5_write_count = 0

    async def send(self, frame: bytes, *, is_zero: bool, reason: str) -> None:
        self.calls += 1
        if self.calls in self.fail_calls:
            raise OSError(f"simulated send failure on call {self.calls}")
        parsed = validate_gimbal_control_frame(
            frame, profile=POCKET3_SPEED_PROFILE, require_live_validated=False
        )
        self.records.append(
            {
                "call": self.calls,
                "monotonic_ns": time.monotonic_ns(),
                "frame_hex": frame.hex(),
                "is_zero": is_zero,
                "reason": reason,
                "would_write_fff5": False,
                **parsed,
            }
        )


TransitionCallback = Callable[[dict], None]


class FailClosedController:
    """The sole producer of serialized control frames.

    ``tick`` is deterministic and testable. ``run`` is the one task that calls
    it at the configured rate in an application.
    """

    def __init__(
        self,
        sink: CommandSink,
        *,
        config: ControlConfig | None = None,
        profile: Pocket3GimbalSpeedProfile = POCKET3_SPEED_PROFILE,
        clock_ns: Callable[[], int] = time.monotonic_ns,
        on_transition: TransitionCallback | None = None,
        live: bool = False,
    ) -> None:
        self.sink = sink
        self.config = config or ControlConfig()
        self.profile = profile
        self.clock_ns = clock_ns
        self.on_transition = on_transition
        self.live = live
        self.state = ControlState.DISABLED
        self.sequence = 0
        self.latest_input = ControlInput()
        self.last_input_ns: int | None = None
        self.last_send_ns: int | None = None
        self.active_since_ns: int | None = None
        self.last_zero_ns: int | None = None
        self.watchdog_state = "inactive"
        self.emergency_latched = False
        self.neutral_required = False
        self.fault_reason: str | None = None
        self._writer_task: asyncio.Task | None = None
        self._stopped = asyncio.Event()

    def snapshot(self) -> dict:
        return {
            "state": self.state.value,
            "input": asdict(self.latest_input),
            "last_input_ns": self.last_input_ns,
            "last_send_ns": self.last_send_ns,
            "active_since_ns": self.active_since_ns,
            "last_zero_ns": self.last_zero_ns,
            "watchdog_state": self.watchdog_state,
            "emergency_latched": self.emergency_latched,
            "neutral_required": self.neutral_required,
            "fault_reason": self.fault_reason,
        }

    def _transition(self, state: ControlState, reason: str) -> None:
        previous = self.state
        self.state = state
        if self.on_transition:
            self.on_transition(
                {
                    "monotonic_ns": self.clock_ns(),
                    "from": previous.value,
                    "to": state.value,
                    "reason": reason,
                }
            )

    def arm(self, prerequisites: ControlPrerequisites) -> None:
        if self.state not in {ControlState.DISABLED, ControlState.DISCONNECTED}:
            raise RuntimeError(f"cannot arm from {self.state.value}")
        missing = prerequisites.missing()
        if missing:
            raise RuntimeError("cannot arm; missing: " + ", ".join(missing))
        self.emergency_latched = False
        self.neutral_required = False
        self.fault_reason = None
        self.watchdog_state = "healthy"
        self._transition(ControlState.ARMED, "prerequisites_confirmed")

    def submit(self, control_input: ControlInput) -> None:
        now = self.clock_ns()
        yaw = max(-self.config.max_axis, min(self.config.max_axis, control_input.yaw))
        pitch = max(-self.config.max_axis, min(self.config.max_axis, control_input.pitch))
        active = control_input.active and (yaw != 0.0 or pitch != 0.0)
        self.latest_input = ControlInput(
            yaw=yaw,
            pitch=pitch,
            recenter_pressed=False,
            record_pressed=False,
            photo_pressed=False,
            source=control_input.source,
            monotonic_ns=now,
            active=active,
        )
        self.last_input_ns = now
        if not active:
            self.neutral_required = False

    async def _send(self, *, yaw: float, pitch: float, is_zero: bool, reason: str) -> None:
        frame = (
            build_zero_frame(sequence=self.sequence, profile=self.profile)
            if is_zero
            else build_speed_frame(
                sequence=self.sequence,
                yaw=yaw,
                pitch=pitch,
                profile=self.profile,
                live=True,
            )
        )
        validate_gimbal_control_frame(
            frame,
            profile=self.profile,
            require_live_validated=self.live,
        )
        self.sequence = (self.sequence + 1) & 0xFFFF
        await self.sink.send(frame, is_zero=is_zero, reason=reason)
        self.last_send_ns = self.clock_ns()
        if is_zero:
            self.last_zero_ns = self.last_send_ns

    async def _zero(self, reason: str, *, destination: ControlState) -> bool:
        self._transition(ControlState.STOPPING, reason)
        for attempt in range(1, self.config.zero_retry_limit + 1):
            try:
                await self._send(yaw=0.0, pitch=0.0, is_zero=True, reason=reason)
                self.latest_input = ControlInput(source="safety", monotonic_ns=self.clock_ns())
                self.active_since_ns = None
                self._transition(destination, f"zero_sent:{reason}")
                return True
            except Exception as exc:
                self.fault_reason = f"zero attempt {attempt} failed: {exc}"
        self._transition(ControlState.FAULT, "zero_unconfirmed")
        return False

    async def emergency_stop(self, reason: str = "emergency_stop") -> bool:
        self.emergency_latched = True
        self.neutral_required = True
        return await self._zero(reason, destination=ControlState.DISABLED)

    async def disconnect(self) -> bool:
        if self.state in {ControlState.ACTIVE, ControlState.ARMED}:
            return await self._zero("ble_disconnected", destination=ControlState.DISCONNECTED)
        self._transition(ControlState.DISCONNECTED, "ble_disconnected")
        return True

    async def focus_lost(self) -> bool:
        return await self.emergency_stop("window_focus_lost")

    async def stop(self, reason: str = "controller_stop") -> bool:
        if self.state in {ControlState.FAULT, ControlState.DISCONNECTED}:
            return False
        return await self._zero(reason, destination=ControlState.DISABLED)

    async def tick(self) -> None:
        if self.state not in {ControlState.ARMED, ControlState.ACTIVE}:
            return
        now = self.clock_ns()
        if self.state == ControlState.ACTIVE and (
            self.last_input_ns is None
            or now - self.last_input_ns > self.config.watchdog_ms * 1_000_000
        ):
            self.watchdog_state = "expired"
            await self._zero("watchdog_timeout", destination=ControlState.ARMED)
            return
        self.watchdog_state = "healthy"
        current = self.latest_input
        if not current.active or (current.yaw == 0 and current.pitch == 0):
            if self.state == ControlState.ACTIVE:
                await self._zero("input_released", destination=ControlState.ARMED)
            return
        if self.neutral_required:
            return
        if self.active_since_ns is None:
            self.active_since_ns = now
        elif now - self.active_since_ns >= self.config.maximum_movement_ms * 1_000_000:
            self.neutral_required = True
            await self._zero("maximum_movement_duration", destination=ControlState.ARMED)
            return
        period_ns = round(1_000_000_000 / self.config.frequency_hz)
        if self.last_send_ns is not None and now - self.last_send_ns < period_ns:
            return
        try:
            await self._send(
                yaw=current.yaw,
                pitch=current.pitch,
                is_zero=False,
                reason="input_active",
            )
            if self.state != ControlState.ACTIVE:
                self._transition(ControlState.ACTIVE, "nonzero_sent")
        except Exception as exc:
            self.fault_reason = f"nonzero send failed: {exc}"
            if not await self._zero("send_failure", destination=ControlState.FAULT):
                return
            self._transition(ControlState.FAULT, "send_failure_after_zero")

    async def run(self) -> None:
        task = asyncio.current_task()
        if self._writer_task is not None and self._writer_task is not task:
            raise RuntimeError("single-writer violation")
        self._writer_task = task
        self._stopped.clear()
        period = 1.0 / self.config.frequency_hz
        try:
            while self.state not in {ControlState.DISABLED, ControlState.FAULT, ControlState.DISCONNECTED}:
                await self.tick()
                await asyncio.sleep(period)
        except asyncio.CancelledError:
            await asyncio.shield(self.emergency_stop("writer_cancelled"))
            raise
        finally:
            self._writer_task = None
            self._stopped.set()
