from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class DeviceProfile:
    name: str
    service_uuid: str
    notification_uuid: str
    write_uuid: str
    default_address: str | None
