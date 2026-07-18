"""Read-only Pocket notification sessions."""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
import hashlib
import json
import time
from pathlib import Path
from typing import Callable

from openframetap.devices.pocket3 import POCKET3_PROFILE
from openframetap.protocol.commands import (
    COMMANDS_BY_NAME,
    SendAuthorization,
    assert_send_allowed,
    validate_command_frame,
)
from openframetap.protocol.duml import decode_duml_frame
from openframetap.protocol.reassembly import DumlStreamReassembler
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


async def manual_send_pocket3_frame(
    address: str,
    *,
    raw: bytes,
    command_name: str,
    confirmed_sha256: str,
    seconds: int,
    output_dir: Path,
    transport_factory: Callable = BluezBleTransport,
) -> tuple[dict, bool]:
    """Execute exactly one user-confirmed frame, then listen without follow-ups.

    This function performs no pairing-state transition and cannot generate or
    send a second frame.  The local wrapper must collect the confirmation from
    the human before invoking this runtime entrypoint.
    """

    if seconds <= 0:
        raise ValueError("seconds must be positive")
    raw = bytes(raw)
    digest = hashlib.sha256(raw).hexdigest()
    if confirmed_sha256.lower() != digest:
        raise PermissionError("manual confirmation SHA-256 does not match the frame")
    try:
        command = COMMANDS_BY_NAME[command_name]
    except KeyError as exc:
        raise ValueError(f"unknown command definition: {command_name}") from exc
    decoded = decode_duml_frame(raw)
    if not (decoded.crc8_valid and decoded.crc16_valid):
        raise ValueError("candidate frame CRC validation failed")
    authorization = SendAuthorization.pairing(
        approval_reference=f"manual-frame-sha256:{digest}"
    )
    assert_send_allowed(command, authorization)
    expected = (command.sender, command.receiver, command.cmd_set, command.cmd_id)
    actual = (decoded.sender, decoded.receiver, decoded.cmd_set, decoded.cmd_id)
    if actual != expected:
        raise ValueError(f"frame metadata {actual!r} does not match command {expected!r}")
    validate_command_frame(command, decoded)

    output_dir.mkdir(parents=True, exist_ok=True)
    transmission_path = output_dir / "transmission.json"
    transmission = {
        "status": "validated_not_yet_written",
        "human_confirmation": "full frame SHA-256 entered in local wrapper",
        "confirmed_sha256": confirmed_sha256.lower(),
        "command": command_name,
        "frame_sha256": digest,
        "frame_hex": raw.hex(),
        "decoded": decoded.to_dict(),
        "follow_up_frames_sent": 0,
    }
    transmission_path.write_text(
        json.dumps(transmission, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )

    recorder = TelemetryRecorder(
        output_dir,
        address=address,
        operation="single-user-confirmed-frame-then-listen",
    )
    ready = asyncio.Event()
    observer = DumlStreamReassembler()
    pairing_status: str | None = None
    pocket_confirmation_observed = False
    protocol_mismatch: str | None = None

    async def notification_handler(notification) -> None:
        nonlocal pairing_status, pocket_confirmation_observed, protocol_mismatch
        await recorder.record_notification(notification)
        for event in observer.feed(notification.data):
            frame = event.frame
            if event.kind != "frame" or frame is None:
                continue
            if (frame.cmd_set, frame.cmd_id) == (0x07, 0x45):
                expected_meta = (
                    frame.sender == 0x07
                    and frame.receiver == 0x02
                    and frame.sequence == decoded.sequence
                    and frame.flags == 0xC0
                )
                if not expected_meta or frame.payload not in {b"\x00\x01", b"\x00\x02"}:
                    protocol_mismatch = f"unexpected pairing status frame: {frame.raw.hex()}"
                    print(f"PAIRING_PROTOCOL_MISMATCH: {protocol_mismatch}", flush=True)
                elif pairing_status is None:
                    pairing_status = "already_paired" if frame.payload[1] == 1 else "confirmation_required"
                    print(f"PAIRING_STATUS: {pairing_status}", flush=True)
                    if pairing_status == "confirmation_required":
                        print(
                            "USER_ACTION_REQUIRED: inspect the Pocket screen and confirm pairing there. "
                            "No follow-up BLE frame will be sent automatically.",
                            flush=True,
                        )
            elif (frame.cmd_set, frame.cmd_id) == (0x07, 0x46):
                expected_approval = (
                    frame.sender == 0x07
                    and frame.receiver == 0x02
                    and frame.flags == 0x40
                    and frame.payload == b"\x01"
                )
                if not expected_approval:
                    protocol_mismatch = f"unexpected Pocket approval frame: {frame.raw.hex()}"
                    print(f"PAIRING_PROTOCOL_MISMATCH: {protocol_mismatch}", flush=True)
                elif not pocket_confirmation_observed:
                    pocket_confirmation_observed = True
                    print(
                        "POCKET_CONFIRMATION_OBSERVED: approval notification captured; "
                        "automatic follow-up BLE frames remain disabled.",
                        flush=True,
                    )
        ready.set()

    transport = transport_factory(
        address,
        POCKET3_PROFILE,
        event_handler=recorder.record_event,
    )
    start = time.monotonic()
    error: str | None = None
    connected_at_end = False
    frame_written = False
    try:
        await transport.connect()
        await transport.subscribe(notification_handler)
        await asyncio.wait_for(ready.wait(), timeout=5.0)
        transmission["write_started_at"] = datetime.now(timezone.utc).isoformat()
        transmission_path.write_text(
            json.dumps(transmission, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
        )
        await transport.send_frame(raw, command=command, authorization=authorization)
        frame_written = True
        transmission["status"] = "single_frame_written"
        transmission["write_completed_at"] = datetime.now(timezone.utc).isoformat()
        transmission_path.write_text(
            json.dumps(transmission, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
        )
        await asyncio.sleep(seconds)
        connected_at_end = transport.is_connected
    except (KeyboardInterrupt, asyncio.CancelledError):
        error = "cancelled"
        raise
    except Exception as exc:
        error = f"{type(exc).__name__}: {exc}"
        transmission["status"] = "failed"
        transmission["error"] = error
        transmission_path.write_text(
            json.dumps(transmission, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
        )
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
            writes_attempted=1 if frame_written else 0,
            pairing_requested=command_name == "set_pairing_pin" and frame_written,
            extra_summary={
                "manual_single_frame_mode": True,
                "frame_sha256": digest,
                "command_sent": command_name if frame_written else None,
                "automatic_follow_up_frames": 0,
                "bluez_pairing_requested": False,
                "pairing_status": pairing_status,
                "pocket_confirmation_observed": pocket_confirmation_observed,
                "protocol_mismatch": protocol_mismatch,
            },
        )
    return summary, error is None
