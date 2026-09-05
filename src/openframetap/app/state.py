from __future__ import annotations

from dataclasses import asdict, dataclass, field, replace
import threading
import time
from typing import Any


@dataclass(frozen=True, slots=True)
class AppStateSnapshot:
    session_mode: str = "livestream"
    connection_stage: str = "starting"
    normal_video_online: bool = False
    action_status: str = ''
    recording_capability_raw: str | None = None
    device_name: str = "Pocket 3"
    ble_connected: bool = False
    pairing_state: str = "confirmed_previous_evidence"
    rtmp_publisher_online: bool = False
    media_state: str = "stopped"
    media_error: str | None = None
    video_width: int | None = None
    video_height: int | None = None
    actual_fps: float | None = None
    receive_bitrate_bps: float | None = None
    dropped_frames: int = 0
    rendered_frames: int = 0
    battery_percent: int | None = None
    battery_provenance: dict[str, Any] | None = None
    rock_cpu_percent: float | None = None
    rock_rss_bytes: int | None = None
    rock_temperature_c: float | None = None
    gimbal_yaw_raw_candidate: str | None = None
    gimbal_pitch_raw_candidate: str | None = None
    gimbal_roll_raw_candidate: str | None = None
    control_state: str = "DISABLED"
    input_source: str = "none"
    yaw: float = 0.0
    pitch: float = 0.0
    last_command_age_ms: float | None = None
    watchdog_state: str = "inactive"
    last_zero_command_monotonic_ns: int | None = None
    last_update_monotonic_ns: int = field(default_factory=time.monotonic_ns)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class StateStore:
    """Thread-safe coalescing store; GUI consumers pull immutable snapshots."""

    def __init__(self, initial: AppStateSnapshot | None = None) -> None:
        self._value = initial or AppStateSnapshot()
        self._revision = 0
        self._lock = threading.Lock()

    def update(self, **changes: Any) -> int:
        unknown = set(changes) - set(AppStateSnapshot.__dataclass_fields__)
        if unknown:
            raise ValueError(f"unknown app state fields: {sorted(unknown)}")
        changes.setdefault("last_update_monotonic_ns", time.monotonic_ns())
        with self._lock:
            updated = replace(self._value, **changes)
            if updated == self._value:
                return self._revision
            self._value = updated
            self._revision += 1
            return self._revision

    def snapshot(self) -> tuple[int, AppStateSnapshot]:
        with self._lock:
            return self._revision, self._value

    def changed_since(self, revision: int) -> tuple[int, AppStateSnapshot] | None:
        current, value = self.snapshot()
        return None if current == revision else (current, value)
