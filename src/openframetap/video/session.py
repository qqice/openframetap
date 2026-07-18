"""Serializable RTMP session state independent of BLE transport."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any


@dataclass(slots=True)
class VideoSession:
    kind: str
    started_at_utc: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    ended_at_utc: str | None = None
    listener_ipv4: str | None = None
    listener_port: int = 1935
    publisher_connected: bool = False
    media_detected: bool = False
    sample_saved: bool = False
    decode_ok: bool = False
    decode_errors: int = 0
    details: dict[str, Any] = field(default_factory=dict)

    def finish(self) -> None:
        self.ended_at_utc = datetime.now(timezone.utc).isoformat()

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

