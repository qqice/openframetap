from __future__ import annotations

import asyncio
import threading
import time
from typing import Callable

from openframetap.app.state import StateStore
from openframetap.devices.pocket3 import POCKET3_PROFILE
from openframetap.protocol.reassembly import DumlStreamReassembler
from openframetap.telemetry.decoder import decode_telemetry
from openframetap.transport.bluez_ble import BluezBleTransport


class ReadOnlyBleMonitor:
    """FFF4-only monitor. It has no method capable of sending an FFF5 frame."""

    def __init__(
        self,
        address: str,
        state: StateStore,
        *,
        event_handler: Callable[[dict], None] | None = None,
        transport_factory=BluezBleTransport,
    ) -> None:
        self.address = address
        self.state = state
        self.event_handler = event_handler or (lambda _event: None)
        self.transport_factory = transport_factory
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self.summary = {
            "notifications": 0,
            "duml_frames": 0,
            "fff5_write_count": 0,
            "cccd_write_count": 0,
            "error": None,
        }

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            raise RuntimeError("BLE status monitor already running")
        self._thread = threading.Thread(target=self._thread_main, name="readonly-ble", daemon=True)
        self._thread.start()

    def stop(self, timeout: float = 8.0) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout)
            if self._thread.is_alive():
                raise TimeoutError("BLE status monitor did not stop")

    def _thread_main(self) -> None:
        try:
            asyncio.run(self._run())
        except Exception as exc:  # thread boundary must preserve a status snapshot
            self.summary["error"] = f"{type(exc).__name__}: {exc}"
            self.state.update(ble_connected=False)

    async def _run(self) -> None:
        reassembler = DumlStreamReassembler()

        def transport_event(event: dict) -> None:
            self.event_handler({"kind": "ble_transport", **event})
            if event.get("event") == "connected":
                self.state.update(ble_connected=True)
            elif event.get("event") in {"disconnected_callback", "disconnect_complete"}:
                self.state.update(ble_connected=False)

        async def notification(record) -> None:
            self.summary["notifications"] += 1
            self.event_handler({"kind": "ble_notification", **record.to_dict()})
            for event in reassembler.feed(record.data):
                if event.kind != "frame" or event.frame is None:
                    continue
                self.summary["duml_frames"] += 1
                frame = event.frame
                decoded = decode_telemetry(frame)
                fields = decoded.fields
                key = (frame.cmd_set, frame.cmd_id)
                if decoded.message_type == "pairing_status":
                    self.state.update(pairing_state=str(fields["status"]))
                elif decoded.message_type == "battery_status_candidate" and fields.get("plausible"):
                    value = int(fields["battery_percent_candidate"])
                    self.state.update(
                        battery_percent=value,
                        battery_provenance={
                            "source_cmd_set": 0x0D,
                            "source_cmd_id": 0x02,
                            "source_offset": 20,
                            "encoding": "uint8",
                            "raw_value": frame.payload[20:21].hex(),
                            "decoded_value": value,
                            "confidence": "confirmed",
                            "last_updated": record.monotonic_ns,
                            "evidence_session": "live-readonly-ui",
                        },
                    )
                elif key == (0x04, 0x05):
                    raw = frame.payload.hex()
                    # Exact physical axis scale is still unconfirmed. Keep only
                    # source-labelled byte candidates for developer display.
                    self.state.update(
                        gimbal_yaw_raw_candidate=raw[0:8] or None,
                        gimbal_pitch_raw_candidate=raw[8:16] or None,
                        gimbal_roll_raw_candidate=raw[16:24] or None,
                    )

        transport = self.transport_factory(
            self.address,
            POCKET3_PROFILE,
            event_handler=transport_event,
        )
        try:
            await transport.connect()
            await transport.subscribe(notification)
            while not self._stop.is_set() and transport.is_connected:
                await asyncio.sleep(0.05)
        finally:
            await transport.disconnect()
            self.summary["fff5_write_count"] = transport.fff5_write_count
            self.summary["cccd_write_count"] = transport.cccd_write_count
            if transport.fff5_write_count != 0:
                self.summary["error"] = "read-only UI attempted an FFF5 write"
            self.state.update(ble_connected=False)

