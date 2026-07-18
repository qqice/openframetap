from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

from openframetap.models import AdvertisementRecord, json_safe, utc_timestamp
from openframetap.profiles import classify_dji_osmo


def _address_type(device: Any) -> str | None:
    details = getattr(device, "details", None)
    if isinstance(details, dict):
        props = details.get("props") or details.get("Properties") or details
        if isinstance(props, dict):
            value = props.get("AddressType") or props.get("address_type")
            if value is not None:
                return str(value)
    return None


def _hex_mapping(values: Any, *, company_keys: bool = False) -> dict[str, str]:
    result: dict[str, str] = {}
    for key, value in dict(values or {}).items():
        if company_keys:
            normalized_key = f"0x{int(key):04x}"
        else:
            normalized_key = str(key).lower()
        if isinstance(value, (bytes, bytearray)):
            result[normalized_key] = bytes(value).hex()
        else:
            result[normalized_key] = str(value)
    return result


def advertisement_to_record(
    device: Any, advertisement: Any, *, observed_at: str | None = None
) -> AdvertisementRecord:
    manufacturer_data = _hex_mapping(
        getattr(advertisement, "manufacturer_data", {}), company_keys=True
    )
    service_data = _hex_mapping(getattr(advertisement, "service_data", {}))
    service_uuids = sorted(
        {str(value).lower() for value in getattr(advertisement, "service_uuids", []) or []}
    )
    display_name = getattr(advertisement, "local_name", None) or getattr(
        device, "name", None
    )
    rssi = getattr(advertisement, "rssi", None)
    if rssi is None:
        rssi = getattr(device, "rssi", None)

    raw_fields = {
        "local_name": getattr(advertisement, "local_name", None),
        "service_uuids": service_uuids,
        "manufacturer_data": manufacturer_data,
        "service_data": service_data,
        "tx_power": getattr(advertisement, "tx_power", None),
        "rssi": rssi,
        "platform_data": json_safe(getattr(advertisement, "platform_data", ())),
    }
    provisional = {
        "display_name": display_name,
        "manufacturer_data": manufacturer_data,
        "service_uuids": service_uuids,
    }
    profile = classify_dji_osmo(provisional)
    return AdvertisementRecord(
        timestamp=observed_at or utc_timestamp(),
        display_name=display_name,
        address=str(getattr(device, "address", "")),
        address_type=_address_type(device),
        rssi=int(rssi) if rssi is not None else None,
        tx_power=(
            int(getattr(advertisement, "tx_power"))
            if getattr(advertisement, "tx_power", None) is not None
            else None
        ),
        service_uuids=service_uuids,
        manufacturer_data=manufacturer_data,
        service_data=service_data,
        raw_advertisement_fields=raw_fields,
        suspected_dji_osmo=profile.suspected,
        profile_match_reasons=profile.reasons,
        suspected_model_raw_hex=profile.model_raw_hex,
    )


async def scan(seconds: int) -> list[AdvertisementRecord]:
    try:
        from bleak import BleakScanner
    except ImportError as exc:
        raise RuntimeError("bleak is not installed in the runtime virtualenv") from exc

    observations: dict[str, AdvertisementRecord] = {}

    def on_advertisement(device: Any, advertisement: Any) -> None:
        record = advertisement_to_record(device, advertisement)
        current = observations.get(record.address)
        if current is None or (record.rssi or -999) >= (current.rssi or -999):
            observations[record.address] = record

    async with BleakScanner(detection_callback=on_advertisement):
        await asyncio.sleep(seconds)
    return sorted(observations.values(), key=lambda item: item.rssi or -999, reverse=True)


def load_replay(path: Path) -> list[AdvertisementRecord]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    records = payload.get("devices", payload) if isinstance(payload, dict) else payload
    result: list[AdvertisementRecord] = []
    for item in records:
        profile = classify_dji_osmo(item)
        result.append(
            AdvertisementRecord(
                timestamp=str(item.get("timestamp") or utc_timestamp()),
                display_name=item.get("display_name"),
                address=str(item.get("address", "")),
                address_type=item.get("address_type"),
                rssi=item.get("rssi"),
                tx_power=item.get("tx_power"),
                service_uuids=list(item.get("service_uuids") or []),
                manufacturer_data=dict(item.get("manufacturer_data") or {}),
                service_data=dict(item.get("service_data") or {}),
                raw_advertisement_fields=dict(item.get("raw_advertisement_fields") or {}),
                suspected_dji_osmo=profile.suspected,
                profile_match_reasons=profile.reasons,
                suspected_model_raw_hex=profile.model_raw_hex,
            )
        )
    return result


def scan_payload(records: list[AdvertisementRecord], *, seconds: int | None) -> dict[str, Any]:
    return {
        "captured_at": utc_timestamp(),
        "scan_seconds": seconds,
        "device_count": len(records),
        "suspected_dji_count": sum(item.suspected_dji_osmo for item in records),
        "devices": [item.to_dict() for item in records],
    }


def render_scan(payload: dict[str, Any]) -> str:
    lines = [
        f"captured_at: {payload['captured_at']}",
        f"scan_seconds: {payload['scan_seconds']}",
        f"device_count: {payload['device_count']}",
        f"suspected_dji_count: {payload['suspected_dji_count']}",
    ]
    for item in payload["devices"]:
        lines.append(
            " | ".join(
                [
                    item.get("address") or "<no-address>",
                    item.get("address_type") or "unknown-address-type",
                    item.get("display_name") or "<unnamed>",
                    f"RSSI={item.get('rssi')}",
                    f"DJI={item.get('suspected_dji_osmo')}",
                    f"manufacturer={item.get('manufacturer_data')}",
                    f"services={item.get('service_uuids')}",
                ]
            )
        )
    return "\n".join(lines)
