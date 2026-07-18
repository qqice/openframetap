"""Minimal BlueZ/Bleak FFF4/FFF5 transport with fail-closed sending."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime, timezone
import time
from typing import Awaitable, Callable

from openframetap.devices.base import DeviceProfile
from openframetap.protocol.commands import (
    CommandDefinition,
    SendAuthorization,
    assert_send_allowed,
)
from openframetap.protocol.duml import decode_duml_frame
from openframetap.protocol.framing import split_for_att


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass(frozen=True, slots=True)
class NotificationRecord:
    wall_timestamp: str
    monotonic_ns: int
    characteristic_uuid: str
    characteristic_handle: int | None
    data: bytes

    def to_dict(self) -> dict:
        return {
            "wall_timestamp": self.wall_timestamp,
            "monotonic_ns": self.monotonic_ns,
            "characteristic_uuid": self.characteristic_uuid,
            "characteristic_handle": self.characteristic_handle,
            "length": len(self.data),
            "data_hex": self.data.hex(),
        }


NotificationHandler = Callable[[NotificationRecord], Awaitable[None] | None]
EventHandler = Callable[[dict], None]


def classify_ble_error(exc: BaseException) -> str:
    text = f"{type(exc).__name__}: {exc}".lower()
    if any(token in text for token in ("not permitted", "not authorized", "permission")):
        return "bluez_permission"
    if any(token in text for token in ("in progress", "busy", "already connected")):
        return "connection_occupied"
    if "not found" in text or "unknown object" in text:
        return "characteristic_or_device_not_found"
    if "disconnect" in text:
        return "device_disconnected"
    if "timeout" in text:
        return "connection_timeout"
    return "bleak_or_bluez_error"


class BluezBleTransport:
    def __init__(
        self,
        address: str,
        profile: DeviceProfile,
        *,
        timeout: float = 20.0,
        event_handler: EventHandler | None = None,
    ) -> None:
        self.address = address
        self.profile = profile
        self.timeout = timeout
        self.event_handler = event_handler or (lambda event: None)
        self._client = None
        self._notification_characteristic = None
        self._write_characteristic = None
        self._subscribed = False
        self._disconnect_count = 0
        self._active_disconnect_count = 0
        self._setup_disconnect_count = 0
        self._disconnect_requested = False

    @property
    def is_connected(self) -> bool:
        return bool(self._client and self._client.is_connected)

    @property
    def mtu(self) -> int | None:
        if not self._client:
            return None
        value = getattr(self._client, "mtu_size", None)
        return int(value) if value is not None else None

    @property
    def disconnect_count(self) -> int:
        return self._disconnect_count

    @property
    def active_disconnect_count(self) -> int:
        return self._active_disconnect_count

    @property
    def setup_disconnect_count(self) -> int:
        return self._setup_disconnect_count

    async def connect(self) -> None:
        try:
            from bleak import BleakClient
        except ImportError as exc:
            raise RuntimeError("bleak is not installed in the runtime virtualenv") from exc

        def disconnected(_client) -> None:
            intentional = self._disconnect_requested
            if not intentional:
                self._disconnect_count += 1
                if self._subscribed:
                    self._active_disconnect_count += 1
                else:
                    self._setup_disconnect_count += 1
            self.event_handler(
                {
                    "wall_timestamp": utc_now(),
                    "monotonic_ns": time.monotonic_ns(),
                    "event": "disconnected_callback",
                    "address": self.address,
                    "intentional": intentional,
                    "session_phase": "active_listen" if self._subscribed else "setup_or_teardown",
                }
            )

        self._client = BleakClient(
            self.address,
            timeout=self.timeout,
            pair=False,
            disconnected_callback=disconnected,
        )
        try:
            await self._client.connect()
            services = self._client.services
            self._notification_characteristic = services.get_characteristic(
                self.profile.notification_uuid
            )
            self._write_characteristic = services.get_characteristic(self.profile.write_uuid)
            if self._notification_characteristic is None:
                raise RuntimeError(f"FFF4 characteristic not found: {self.profile.notification_uuid}")
            properties = {str(item).lower() for item in self._notification_characteristic.properties}
            if "notify" not in properties and "indicate" not in properties:
                raise RuntimeError(f"FFF4 lacks notify/indicate property: {sorted(properties)}")
            self.event_handler(
                {
                    "wall_timestamp": utc_now(),
                    "monotonic_ns": time.monotonic_ns(),
                    "event": "connected",
                    "address": self.address,
                    "mtu_reported_by_backend": self.mtu,
                    "pair_requested": False,
                    "fff4_properties": sorted(properties),
                    "fff4_handle": getattr(self._notification_characteristic, "handle", None),
                    "fff5_handle": getattr(self._write_characteristic, "handle", None),
                }
            )
        except Exception as exc:
            self.event_handler(
                {
                    "wall_timestamp": utc_now(),
                    "monotonic_ns": time.monotonic_ns(),
                    "event": "connection_error",
                    "category": classify_ble_error(exc),
                    "error": f"{type(exc).__name__}: {exc}",
                }
            )
            raise

    async def subscribe(self, handler: NotificationHandler) -> None:
        if not self._client or not self._notification_characteristic:
            raise RuntimeError("transport is not connected")

        def callback(characteristic, data: bytearray) -> None:
            record = NotificationRecord(
                wall_timestamp=utc_now(),
                monotonic_ns=time.monotonic_ns(),
                characteristic_uuid=str(characteristic.uuid),
                characteristic_handle=getattr(characteristic, "handle", None),
                data=bytes(data),
            )
            result = handler(record)
            if result is not None:
                asyncio.get_running_loop().create_task(result)

        try:
            await self._client.start_notify(self._notification_characteristic, callback)
            self._subscribed = True
            self.event_handler(
                {
                    "wall_timestamp": utc_now(),
                    "monotonic_ns": time.monotonic_ns(),
                    "event": "fff4_subscribed",
                    "write_attempted": False,
                }
            )
        except Exception as exc:
            self.event_handler(
                {
                    "wall_timestamp": utc_now(),
                    "monotonic_ns": time.monotonic_ns(),
                    "event": "subscription_error",
                    "category": classify_ble_error(exc),
                    "error": f"{type(exc).__name__}: {exc}",
                }
            )
            raise

    async def send_frame(
        self,
        raw: bytes,
        *,
        command: CommandDefinition,
        authorization: SendAuthorization | None,
    ) -> None:
        """Validate policy and decoded command metadata before any FFF5 write."""

        assert_send_allowed(command, authorization)
        frame = decode_duml_frame(raw)
        if not (frame.crc8_valid and frame.crc16_valid):
            raise ValueError("refusing to send a frame with invalid CRC")
        actual = (frame.sender, frame.receiver, frame.cmd_set, frame.cmd_id)
        expected = (command.sender, command.receiver, command.cmd_set, command.cmd_id)
        if actual != expected:
            raise ValueError(f"frame metadata {actual!r} does not match command {expected!r}")
        if not self._client or not self._write_characteristic or not self.is_connected:
            raise RuntimeError("FFF5 transport is not connected")
        properties = {str(item).lower() for item in self._write_characteristic.properties}
        if not ({"write-without-response", "write"} & properties):
            raise RuntimeError(f"FFF5 is not writable: {sorted(properties)}")
        mtu = self.mtu or 23
        for chunk_index, chunk in enumerate(split_for_att(raw, mtu=mtu)):
            self.event_handler(
                {
                    "wall_timestamp": utc_now(),
                    "monotonic_ns": time.monotonic_ns(),
                    "event": "fff5_write",
                    "command": command.name,
                    "authorization_reference": authorization.approval_reference,
                    "chunk_index": chunk_index,
                    "data_hex": chunk.hex(),
                }
            )
            await self._client.write_gatt_char(
                self._write_characteristic, chunk, response=False
            )

    async def disconnect(self) -> None:
        if not self._client:
            return
        try:
            if self._subscribed and self._client.is_connected:
                await self._client.stop_notify(self._notification_characteristic)
                self._subscribed = False
            if self._client.is_connected:
                self._disconnect_requested = True
                await self._client.disconnect()
        finally:
            self.event_handler(
                {
                    "wall_timestamp": utc_now(),
                    "monotonic_ns": time.monotonic_ns(),
                    "event": "disconnect_complete",
                    "connected": self.is_connected,
                }
            )
