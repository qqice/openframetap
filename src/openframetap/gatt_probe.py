from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from openframetap.models import (
    GattCharacteristic,
    GattDescriptor,
    GattService,
    json_safe,
    utc_timestamp,
)


def _handle(value: Any) -> int | None:
    handle = getattr(value, "handle", None)
    return int(handle) if handle is not None else None


async def probe(address: str, *, timeout: float = 20.0) -> tuple[dict[str, Any], bool]:
    """Connect and enumerate metadata only; never pair, read values, or write."""
    try:
        from bleak import BleakClient
    except ImportError as exc:
        raise RuntimeError("bleak is not installed in the runtime virtualenv") from exc

    payload: dict[str, Any] = {
        "captured_at": utc_timestamp(),
        "address": address,
        "operation": "metadata-only-gatt-enumeration",
        "pair_requested": False,
        "value_reads_attempted": False,
        "writes_attempted": False,
        "connected": False,
        "disconnected": False,
        "negotiated_mtu": None,
        "pairing_required": None,
        "services": [],
        "error": None,
        "disconnect_reason": None,
    }
    client = BleakClient(address, timeout=timeout, pair=False)
    ok = False
    try:
        await client.connect()
        payload["connected"] = bool(client.is_connected)
        mtu = getattr(client, "mtu_size", None)
        payload["negotiated_mtu"] = int(mtu) if mtu is not None else None
        services: list[GattService] = []
        service_collection = client.services
        for service in service_collection:
            characteristics: list[GattCharacteristic] = []
            for characteristic in service.characteristics:
                descriptors = [
                    GattDescriptor(uuid=str(item.uuid), handle=_handle(item))
                    for item in characteristic.descriptors
                ]
                characteristics.append(
                    GattCharacteristic(
                        uuid=str(characteristic.uuid),
                        handle=_handle(characteristic),
                        properties=sorted(str(item) for item in characteristic.properties),
                        descriptors=descriptors,
                    )
                )
            services.append(
                GattService(
                    uuid=str(service.uuid),
                    handle=_handle(service),
                    characteristics=characteristics,
                )
            )
        payload["services"] = json_safe(services)
        ok = True
    except Exception as exc:  # Bleak exposes backend-specific exception types.
        error = f"{type(exc).__name__}: {exc}"
        payload["error"] = error
        lowered = error.lower()
        payload["pairing_required"] = any(
            token in lowered
            for token in ("authentication", "not authorized", "not permitted", "pair")
        )
    finally:
        try:
            if client.is_connected:
                await client.disconnect()
            payload["disconnected"] = not bool(client.is_connected)
        except Exception as exc:
            payload["disconnect_reason"] = f"{type(exc).__name__}: {exc}"
    return payload, ok


def write_probe(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
