"""Bounded, one-axis Pocket 3 04/0C hardware-validation session."""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import time
from typing import Callable

from openframetap.app.artifacts import JsonlWriter
from openframetap.app.input import ControlInput
from openframetap.control.policy import validate_gimbal_control_frame
from openframetap.control.safety import (
    ControlConfig,
    ControlPrerequisites,
    FailClosedController,
)
from openframetap.devices.pocket3 import POCKET3_PROFILE
from openframetap.network.secrets import require_private_directory
from openframetap.protocol.commands import (
    DANGEROUS_COMMANDS,
    SendAuthorization,
)
from openframetap.protocol.duml import decode_duml_frame
from openframetap.protocol.reassembly import DumlStreamReassembler
from openframetap.transport.bluez_ble import BluezBleTransport


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


class OneShotBleSink:
    def __init__(self, transport, writer: JsonlWriter) -> None:
        self.transport = transport
        self.writer = writer
        self.command = DANGEROUS_COMMANDS["gimbal_speed_control"]
        self.authorization = SendAuthorization.bounded_gimbal_test(
            approval_reference="owner-invoked bounded gimbal test command"
        )
        self.records: list[dict] = []

    async def send(self, frame: bytes, *, is_zero: bool, reason: str) -> None:
        parsed = validate_gimbal_control_frame(
            frame, require_live_validated=False
        )
        record = {
            "wall_time_utc": utc_now(),
            "monotonic_ns": time.monotonic_ns(),
            "frame_hex": frame.hex(),
            "frame_sha256": hashlib.sha256(frame).hexdigest(),
            "is_zero": is_zero,
            "reason": reason,
            **parsed,
        }
        self.writer.write({**record, "phase": "attempt"})
        await self.transport.send_frame(
            frame, command=self.command, authorization=self.authorization
        )
        record["completed_monotonic_ns"] = time.monotonic_ns()
        self.writer.write({**record, "phase": "complete"})
        self.records.append(record)


def _write_checksums(output: Path) -> None:
    names = (
        "config.json",
        "sent-commands.jsonl",
        "notifications.jsonl",
        "telemetry.jsonl",
        "state-transitions.jsonl",
        "transport-events.jsonl",
        "summary.json",
    )
    with (output / "checksums.sha256").open("w", encoding="ascii", newline="\n") as stream:
        for name in names:
            path = output / name
            if path.is_file():
                stream.write(f"{hashlib.sha256(path.read_bytes()).hexdigest()}  {name}\n")


async def run_one_shot_gimbal_test(
    *,
    address: str,
    axis: str,
    direction: str,
    output_value: float,
    duration_ms: int,
    output_dir: Path,
    software_git_head: str,
    transport_factory: Callable = BluezBleTransport,
) -> dict:
    if axis not in {"yaw", "pitch"}:
        raise ValueError("axis must be yaw or pitch")
    if direction not in {"positive", "negative"}:
        raise ValueError("direction must be positive or negative")
    if not 0 < output_value <= 0.05:
        raise ValueError("first one-shot output must be in (0, 0.05]")
    if not 1 <= duration_ms <= 200:
        raise ValueError("first one-shot duration must be 1..200 ms")

    output_dir = require_private_directory(output_dir)
    sent = JsonlWriter(output_dir / "sent-commands.jsonl")
    notifications = JsonlWriter(output_dir / "notifications.jsonl")
    telemetry = JsonlWriter(output_dir / "telemetry.jsonl")
    transitions = JsonlWriter(output_dir / "state-transitions.jsonl")
    transport_events = JsonlWriter(output_dir / "transport-events.jsonl")
    reassembler = DumlStreamReassembler()
    notification_count = 0
    duml_count = 0
    gimbal_count = 0
    crc_failures = 0
    error = None
    started_ns = time.monotonic_ns()

    config = {
        "generated_at_utc": utc_now(),
        "device_address": address,
        "axis": axis,
        "direction": direction,
        "normalized_output": output_value,
        "duration_ms": duration_ms,
        "unconditional_zero_deadline_ms": 500,
        "profile": "pocket3_speed_04_0c_v0",
        "profile_hardware_validated_before_test": False,
        "application_pairing_basis": "confirmed_previous_hardware_evidence",
        "software_git_head": software_git_head,
    }
    (output_dir / "config.json").write_text(
        json.dumps(config, indent=2) + "\n", encoding="utf-8"
    )

    def event_handler(event: dict) -> None:
        transport_events.write(event)

    transport = transport_factory(
        address, POCKET3_PROFILE, event_handler=event_handler
    )

    async def on_notification(record) -> None:
        nonlocal notification_count, duml_count, gimbal_count, crc_failures
        notification_count += 1
        notifications.write(record.to_dict())
        for event in reassembler.feed(record.data):
            if event.kind != "frame" or event.frame is None:
                crc_failures += 1
                continue
            duml_count += 1
            frame = event.frame
            if not frame.crc8_valid or not frame.crc16_valid:
                crc_failures += 1
            if frame.cmd_set == 0x04:
                gimbal_count += 1
                telemetry.write(
                    {
                        "wall_time_utc": record.wall_timestamp,
                        "monotonic_ns": record.monotonic_ns,
                        **frame.to_dict(),
                    }
                )

    sink = OneShotBleSink(transport, sent)
    controller = FailClosedController(
        sink,
        config=ControlConfig(
            frequency_hz=10,
            watchdog_ms=300,
            maximum_movement_ms=500,
            max_axis=output_value,
            zero_retry_limit=2,
        ),
        on_transition=transitions.write,
        live=False,
    )
    try:
        await transport.connect()
        await transport.acquire_mtu()
        await transport.subscribe(on_notification)
        await asyncio.sleep(0.5)
        if notification_count == 0:
            raise RuntimeError("FFF4 subscription produced no notification evidence")
        controller.arm(ControlPrerequisites(True, True, True, True, True, True, True))
        signed = output_value if direction == "positive" else -output_value
        controller.submit(
            ControlInput(
                yaw=signed if axis == "yaw" else 0.0,
                pitch=signed if axis == "pitch" else 0.0,
                source="one-shot",
                active=True,
            )
        )
        await controller.tick()  # exactly one non-zero frame
        await asyncio.sleep(duration_ms / 1000)
        controller.submit(ControlInput(source="one-shot-release"))
        await controller.tick()  # first zero at the requested duration
        remaining = max(0.0, (500 - duration_ms) / 1000)
        await asyncio.sleep(remaining)
        await controller.stop("unconditional_500ms_zero")  # unconditional second zero
    except Exception as exc:
        error = f"{type(exc).__name__}: {exc}"
        if controller.state.value not in {"disabled", "fault", "disconnected"}:
            try:
                await controller.emergency_stop("one_shot_exception")
            except Exception as stop_exc:
                error += f"; zero failure: {type(stop_exc).__name__}: {stop_exc}"
    finally:
        await transport.disconnect()
        for event in reassembler.finish():
            if event.kind != "frame":
                crc_failures += 1
        for writer in (sent, notifications, telemetry, transitions, transport_events):
            writer.close()

    nonzero = [item for item in sink.records if not item["is_zero"]]
    zero = [item for item in sink.records if item["is_zero"]]
    summary = {
        "generated_at_utc": utc_now(),
        "actual_duration_seconds": (time.monotonic_ns() - started_ns) / 1e9,
        "error": error,
        "axis": axis,
        "direction": direction,
        "normalized_output": output_value,
        "nonzero_command_count": len(nonzero),
        "zero_command_count": len(zero),
        "fff5_write_count": transport.fff5_write_count,
        "cccd_write_count": transport.cccd_write_count,
        "notifications_received": notification_count,
        "duml_frames_received": duml_count,
        "gimbal_frames_received": gimbal_count,
        "crc_or_reassembly_failures": crc_failures,
        "disconnect_count": transport.disconnect_count,
        "att_mtu": transport.mtu,
        "final_control_state": controller.state.value,
        "final_output_zero": bool(sink.records and sink.records[-1]["is_zero"]),
        "one_shot_invariant": len(nonzero) == 1,
        "zero_redundancy_invariant": len(zero) >= 2,
        "software_git_head": software_git_head,
    }
    if not summary["one_shot_invariant"] or not summary["zero_redundancy_invariant"]:
        summary["error"] = summary["error"] or "one-shot/zero invariant failed"
    if not summary["final_output_zero"]:
        summary["error"] = summary["error"] or "final output was not zero"
    (output_dir / "summary.json").write_text(
        json.dumps(summary, indent=2) + "\n", encoding="utf-8"
    )
    _write_checksums(output_dir)
    return summary

