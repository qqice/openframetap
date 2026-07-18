from __future__ import annotations

from dataclasses import asdict, dataclass, field, is_dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping


def utc_timestamp() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


def json_safe(value: Any) -> Any:
    """Convert runtime objects to stable, loss-minimizing JSON values."""
    if is_dataclass(value):
        return json_safe(asdict(value))
    if isinstance(value, bytes):
        return value.hex()
    if isinstance(value, bytearray):
        return bytes(value).hex()
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, Mapping):
        return {str(key): json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [json_safe(item) for item in value]
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    return repr(value)


@dataclass(slots=True)
class AdvertisementRecord:
    timestamp: str
    display_name: str | None
    address: str
    address_type: str | None
    rssi: int | None
    tx_power: int | None
    service_uuids: list[str] = field(default_factory=list)
    manufacturer_data: dict[str, str] = field(default_factory=dict)
    service_data: dict[str, str] = field(default_factory=dict)
    raw_advertisement_fields: dict[str, Any] = field(default_factory=dict)
    suspected_dji_osmo: bool = False
    profile_match_reasons: list[str] = field(default_factory=list)
    suspected_model_raw_hex: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return json_safe(self)


@dataclass(slots=True)
class GattDescriptor:
    uuid: str
    handle: int | None


@dataclass(slots=True)
class GattCharacteristic:
    uuid: str
    handle: int | None
    properties: list[str]
    descriptors: list[GattDescriptor] = field(default_factory=list)


@dataclass(slots=True)
class GattService:
    uuid: str
    handle: int | None
    characteristics: list[GattCharacteristic] = field(default_factory=list)
