"""Read-only Pocket notification sessions."""

from __future__ import annotations

import asyncio
import time
from pathlib import Path

from openframetap.devices.pocket3 import POCKET3_PROFILE
from openframetap.telemetry.recorder import TelemetryRecorder
from openframetap.transport.bluez_ble import BluezBleTransport


async def listen_pocket3(
    address: str,
    *,
    seconds: int,
    output_dir: Path,
    operation: str = "fff4-listen-no-writes",
) -> tuple[dict, bool]:
    if seconds <= 0:
        raise ValueError("seconds must be positive")
    recorder = TelemetryRecorder(output_dir, address=address, operation=operation)
    transport = BluezBleTransport(
        address,
        POCKET3_PROFILE,
        event_handler=recorder.record_event,
    )
    start = time.monotonic()
    error: str | None = None
    connected_at_end = False
    try:
        await transport.connect()
        await transport.subscribe(recorder.record_notification)
        await asyncio.sleep(seconds)
        connected_at_end = transport.is_connected
    except (KeyboardInterrupt, asyncio.CancelledError):
        error = "cancelled"
        raise
    except Exception as exc:
        error = f"{type(exc).__name__}: {exc}"
    finally:
        actual_seconds = time.monotonic() - start
        try:
            await transport.disconnect()
        except Exception as exc:
            disconnect_error = f"{type(exc).__name__}: {exc}"
            error = f"{error}; disconnect: {disconnect_error}" if error else disconnect_error
        summary = recorder.finalize(
            actual_seconds=actual_seconds,
            disconnect_count=transport.active_disconnect_count,
            setup_disconnect_count=transport.setup_disconnect_count,
            unintentional_disconnect_callbacks_total=transport.disconnect_count,
            connected=connected_at_end,
            error=error,
        )
    return summary, error is None
