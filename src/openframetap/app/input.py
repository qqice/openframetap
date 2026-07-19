from __future__ import annotations

from dataclasses import asdict, dataclass
import json
import math
from pathlib import Path
import time
from typing import Protocol


@dataclass(frozen=True, slots=True)
class ControlInput:
    yaw: float = 0.0
    pitch: float = 0.0
    recenter_pressed: bool = False
    record_pressed: bool = False
    photo_pressed: bool = False
    emergency_stop: bool = False
    exit_requested: bool = False
    source: str = "none"
    monotonic_ns: int = 0
    active: bool = False

    def __post_init__(self) -> None:
        if not -1.0 <= self.yaw <= 1.0 or not -1.0 <= self.pitch <= 1.0:
            raise ValueError("control axes must be normalized to -1..1")

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class JoystickConfig:
    logical_width: int = 1280
    logical_height: int = 720
    center_x: float = 150.0
    center_y: float = 570.0
    radius: float = 110.0
    deadzone: float = 0.12
    maximum_output: float = 0.25
    cubic_blend: float = 0.65
    live_offset_min: int = 16
    live_offset_default: int = 96
    live_offset_max: int = 188

    def __post_init__(self) -> None:
        if self.logical_width <= 0 or self.logical_height <= 0 or self.radius <= 0:
            raise ValueError("joystick geometry must be positive")
        if not 0 <= self.deadzone < 1:
            raise ValueError("joystick deadzone must be 0..1")
        if not 0 < self.maximum_output <= 1:
            raise ValueError("maximum output must be 0..1")
        if not 0 <= self.cubic_blend <= 1:
            raise ValueError("cubic blend must be 0..1")
        if not 1 <= self.live_offset_min <= self.live_offset_default <= self.live_offset_max:
            raise ValueError("live offset bounds must satisfy 1 <= min <= default <= max")
        if self.live_offset_max > 188:
            raise ValueError("live offset exceeds the symmetric Mimo-captured envelope")

    @classmethod
    def load(cls, path: Path) -> "JoystickConfig":
        payload = json.loads(path.read_text(encoding="utf-8"))
        return cls(**payload["joystick"])

    def to_dict(self) -> dict:
        return asdict(self)


class InputSource(Protocol):
    def current(self) -> ControlInput: ...


def _curve(value: float, blend: float) -> float:
    return (1.0 - blend) * value + blend * value**3


def map_touch_axes(x: float, y: float, config: JoystickConfig) -> tuple[float, float]:
    dx = (x - config.center_x) / config.radius
    dy = (config.center_y - y) / config.radius
    magnitude = math.hypot(dx, dy)
    if magnitude <= config.deadzone:
        return 0.0, 0.0
    if magnitude > 1.0:
        dx /= magnitude
        dy /= magnitude
        magnitude = 1.0
    direction_x, direction_y = dx / magnitude, dy / magnitude
    normalized = (magnitude - config.deadzone) / (1.0 - config.deadzone)
    output = _curve(normalized, config.cubic_blend) * config.maximum_output
    return direction_x * output, direction_y * output


class KeyboardInput:
    YAW_NEGATIVE = {"left", "a"}
    YAW_POSITIVE = {"right", "d"}
    PITCH_POSITIVE = {"up", "w"}
    PITCH_NEGATIVE = {"down", "s"}

    def __init__(self, *, maximum_output: float = 0.25, clock=time.monotonic_ns) -> None:
        if not 0 < maximum_output <= 1:
            raise ValueError("keyboard maximum output must be 0..1")
        self.maximum_output = maximum_output
        self.clock = clock
        self._pressed: set[str] = set()
        self._emergency = False
        self._exit = False

    def key_down(self, key: str) -> ControlInput:
        key = key.lower()
        if key in {"space", " "}:
            self._emergency = True
            self._pressed.clear()
        elif key in {"escape", "esc", "q"}:
            self._exit = True
            self._pressed.clear()
        else:
            self._pressed.add(key)
        return self.current()

    def key_up(self, key: str) -> ControlInput:
        self._pressed.discard(key.lower())
        return self.current()

    def reset(self) -> ControlInput:
        self._pressed.clear()
        return self.current()

    def current(self) -> ControlInput:
        negative_yaw = bool(self._pressed & self.YAW_NEGATIVE)
        positive_yaw = bool(self._pressed & self.YAW_POSITIVE)
        positive_pitch = bool(self._pressed & self.PITCH_POSITIVE)
        negative_pitch = bool(self._pressed & self.PITCH_NEGATIVE)
        yaw = (int(positive_yaw) - int(negative_yaw)) * self.maximum_output
        pitch = (int(positive_pitch) - int(negative_pitch)) * self.maximum_output
        active = bool(yaw or pitch)
        return ControlInput(
            yaw=yaw,
            pitch=pitch,
            emergency_stop=self._emergency,
            exit_requested=self._exit,
            source="keyboard",
            monotonic_ns=self.clock(),
            active=active,
        )


class TouchJoystickInput:
    def __init__(self, config: JoystickConfig, *, clock=time.monotonic_ns) -> None:
        self.config = config
        self.clock = clock
        self._touch_id: object | None = None
        self._value = ControlInput(source="touch", monotonic_ns=self.clock())

    def touch_down(self, touch_id: object, x: float, y: float) -> ControlInput:
        if self._touch_id is not None and touch_id != self._touch_id:
            return self._value
        self._touch_id = touch_id
        return self._set(x, y)

    def touch_move(self, touch_id: object, x: float, y: float) -> ControlInput:
        if touch_id != self._touch_id:
            return self._value
        return self._set(x, y)

    def touch_up(self, touch_id: object) -> ControlInput:
        if touch_id != self._touch_id:
            return self._value
        self._touch_id = None
        self._value = ControlInput(source="touch", monotonic_ns=self.clock(), active=False)
        return self._value

    def touch_cancel(self, touch_id: object | None = None) -> ControlInput:
        if touch_id is None or touch_id == self._touch_id:
            self._touch_id = None
            self._value = ControlInput(source="touch", monotonic_ns=self.clock(), active=False)
        return self._value

    def _set(self, x: float, y: float) -> ControlInput:
        yaw, pitch = map_touch_axes(x, y, self.config)
        self._value = ControlInput(
            yaw=yaw,
            pitch=pitch,
            source="touch",
            monotonic_ns=self.clock(),
            active=bool(yaw or pitch),
        )
        return self._value

    def current(self) -> ControlInput:
        return self._value


class MockInput:
    def __init__(self, *, clock=time.monotonic_ns) -> None:
        self.clock = clock
        self._value = ControlInput(source="mock", monotonic_ns=self.clock())

    def set_axes(self, yaw: float, pitch: float) -> ControlInput:
        self._value = ControlInput(
            yaw=max(-1.0, min(1.0, float(yaw))),
            pitch=max(-1.0, min(1.0, float(pitch))),
            source="mock",
            monotonic_ns=self.clock(),
            active=bool(yaw or pitch),
        )
        return self._value

    def release(self) -> ControlInput:
        return self.set_axes(0.0, 0.0)

    def current(self) -> ControlInput:
        return self._value


class GamepadInput:
    """Interface placeholder; no device backend is enabled in this phase."""

    def current(self) -> ControlInput:
        return ControlInput(source="gamepad-unavailable", monotonic_ns=time.monotonic_ns())
