"""Orchestrate a TTY-marked FFF4-only Pocket 3 experiment."""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import platform
import time
from typing import Callable

from openframetap.devices.pocket3 import POCKET3_PROFILE
from openframetap.experiments.event_marker import EventWriter, ExperimentEvent, TTYKeyReader
from openframetap.experiments.protocol import EVENT_DEFINITIONS, KEY_HELP, PROCEDURE_GUIDE
from openframetap.experiments.telemetry_capture import ExperimentTelemetryRecorder
from openframetap.transport.bluez_ble import BluezBleTransport


def _record_definition(writer: EventWriter, key: str) -> ExperimentEvent:
    definition = EVENT_DEFINITIONS[key]
    event = ExperimentEvent.now(
        event_code=definition.event_code,
        event_name=definition.event_name,
        phase=definition.phase,
    )
    writer.record(event)
    print(f"MARKED {event.event_name} monotonic_ns={event.monotonic_ns}", flush=True)
    return event


async def run_passive_experiment(
    address: str,
    *,
    duration: float,
    output_dir: Path,
    transport_factory: Callable = BluezBleTransport,
    key_reader_factory: Callable = TTYKeyReader,
    software_git_head: str | None = None,
    remote_kernel: str | None = None,
) -> tuple[dict, bool]:
    if duration <= 0:
        raise ValueError("duration must be positive")
    output_dir.mkdir(parents=True, exist_ok=True)
    recorder = ExperimentTelemetryRecorder(output_dir)
    event_writer = EventWriter(output_dir / "events.jsonl")
    transport_events: list[dict] = []
    transport = transport_factory(
        address,
        POCKET3_PROFILE,
        event_handler=transport_events.append,
    )
    started_wall = datetime.now(timezone.utc)
    started_ns = time.monotonic_ns()
    error: str | None = None
    stop_reason = "duration_elapsed"
    att_mtu: int | None = None
    connected_at_end = False
    timed_procedure_started_ns: int | None = None
    event_writer.record(
        ExperimentEvent.now(
            event_code="session_start",
            event_name="experiment_started",
            phase="session",
        )
    )
    try:
        await transport.connect()
        acquire_mtu = getattr(transport, "acquire_mtu", None)
        att_mtu = await acquire_mtu() if callable(acquire_mtu) else getattr(transport, "mtu", None)
        await transport.subscribe(recorder.record_notification)
        print(KEY_HELP, flush=True)
        print(PROCEDURE_GUIDE, flush=True)
        reader = key_reader_factory()
        with reader:
            print(
                "Press r when the Pocket is positioned and you are ready to start the "
                f"{duration:g}-second procedure. Press q to stop without starting.",
                flush=True,
            )
            while timed_procedure_started_ns is None:
                key = await reader.read_key(timeout=300.0)
                if key is None:
                    stop_reason = "readiness_timeout"
                    raise TimeoutError("no readiness confirmation received within 300 seconds")
                key = key.lower()
                if key == "r":
                    timed_procedure_started_ns = time.monotonic_ns()
                    event_writer.record(
                        ExperimentEvent.now(
                            event_code="procedure_ready",
                            event_name="timed_procedure_started",
                            phase="session",
                        )
                    )
                    print("TIMED PROCEDURE STARTED", flush=True)
                elif key == "q":
                    stop_reason = "user_requested_before_start"
                    break
                elif key not in {"\r", "\n", " ", "\t"}:
                    print("Not started yet: press r when ready or q to stop.", flush=True)
            if timed_procedure_started_ns is None:
                connected_at_end = transport.is_connected
            deadline_ns = (
                timed_procedure_started_ns + int(duration * 1_000_000_000)
                if timed_procedure_started_ns is not None
                else time.monotonic_ns()
            )
            while True:
                remaining = (deadline_ns - time.monotonic_ns()) / 1_000_000_000
                if remaining <= 0:
                    break
                key = await reader.read_key(timeout=remaining)
                if key is None:
                    break
                key = key.lower()
                if key in EVENT_DEFINITIONS:
                    _record_definition(event_writer, key)
                elif key == "9":
                    value_text = await reader.read_line(
                        "Pocket displayed battery percent (0-100): ", max_length=3
                    )
                    try:
                        value = int(value_text)
                    except ValueError:
                        print("Battery observation rejected: enter an integer 0-100.", flush=True)
                        continue
                    if not 0 <= value <= 100:
                        print("Battery observation rejected: value is outside 0-100.", flush=True)
                        continue
                    event_writer.record(
                        ExperimentEvent.now(
                            event_code="battery_observation",
                            event_name="displayed_battery_percent",
                            phase="observation",
                            optional_note=f"Pocket screen displayed {value}%",
                            observed_value=value,
                        )
                    )
                    print(f"MARKED displayed_battery_percent={value}", flush=True)
                elif key == "m":
                    note = await reader.read_line("Custom event note: ", max_length=200)
                    event_writer.record(
                        ExperimentEvent.now(
                            event_code="custom",
                            event_name="custom_event",
                            phase="custom",
                            optional_note=note or None,
                        )
                    )
                    print("MARKED custom_event", flush=True)
                elif key == "q":
                    stop_reason = "user_requested"
                    event_writer.record(
                        ExperimentEvent.now(
                            event_code="stop_requested",
                            event_name="stop_and_save",
                            phase="session",
                        )
                    )
                    break
                elif key not in {"\r", "\n", " ", "\t"}:
                    print(f"Unknown key {key!r}; press a listed key.", flush=True)
        connected_at_end = transport.is_connected
    except (asyncio.CancelledError, KeyboardInterrupt):
        stop_reason = "ctrl_c"
        error = "cancelled_by_user"
        event_writer.record(
            ExperimentEvent.now(
                event_code="interrupt",
                event_name="ctrl_c_stop_and_save",
                phase="session",
            )
        )
    except Exception as exc:
        stop_reason = "error"
        error = f"{type(exc).__name__}: {exc}"
    finally:
        try:
            await transport.disconnect()
        except Exception as exc:
            disconnect_error = f"{type(exc).__name__}: {exc}"
            error = f"{error}; disconnect: {disconnect_error}" if error else disconnect_error
        recorder.finalize_reassembly()
        message_counts = recorder.write_message_counts()
        event_writer.record(
            ExperimentEvent.now(
                event_code="session_end",
                event_name="experiment_finished",
                phase="session",
                optional_note=stop_reason,
            )
        )
        finished_wall = datetime.now(timezone.utc)
        actual_seconds = (time.monotonic_ns() - started_ns) / 1_000_000_000
        fff5_write_count = int(getattr(transport, "fff5_write_count", 0))
        cccd_write_count = int(getattr(transport, "cccd_write_count", 0))
        if fff5_write_count != 0:
            safety_error = f"SAFETY FAILURE: fff5_write_count={fff5_write_count}"
            error = f"{error}; {safety_error}" if error else safety_error
            stop_reason = "fff5_write_detected"
        session = {
            "device_address": address,
            "start_time_utc": started_wall.isoformat(),
            "end_time_utc": finished_wall.isoformat(),
            "actual_duration_seconds": actual_seconds,
            "requested_duration_seconds": duration,
            "timed_procedure_started_monotonic_ns": timed_procedure_started_ns,
            "timed_procedure_actual_seconds": (
                max(0.0, (time.monotonic_ns() - timed_procedure_started_ns) / 1_000_000_000)
                if timed_procedure_started_ns is not None
                else 0.0
            ),
            "att_mtu": att_mtu,
            "notifications_received": recorder.notification_count,
            "duml_frames_received": recorder.reassembler.stats.frames_ok,
            "crc8_failures": recorder.reassembler.stats.crc8_failures,
            "crc16_failures": recorder.reassembler.stats.crc16_failures,
            "reassembly_errors": (
                recorder.reassembler.stats.invalid_lengths
                + recorder.reassembler.stats.truncated_fragments
            ),
            "disconnect_count": int(getattr(transport, "disconnect_count", 0)),
            "active_disconnect_count": int(
                getattr(transport, "active_disconnect_count", 0)
            ),
            "fff5_write_count": fff5_write_count,
            "cccd_write_count": cccd_write_count,
            "event_count": len(event_writer.events),
            "event_names": [event.event_name for event in event_writer.events],
            "software_git_head": software_git_head
            or os.environ.get("OPENFRAMETAP_GIT_HEAD", "unknown"),
            "remote_kernel": remote_kernel or platform.release(),
            "stop_reason": stop_reason,
            "connected_at_end_of_capture": connected_at_end,
            "error": error,
            "message_counts": message_counts["command_counts"],
            "transport_events": transport_events,
            "safety_assertion": "fff5_write_count == 0",
            "safety_assertion_passed": fff5_write_count == 0,
        }
        (output_dir / "session.json").write_text(
            json.dumps(session, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
        )
    return session, error is None and session["safety_assertion_passed"]
