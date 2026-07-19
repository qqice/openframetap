"""Reproducible offline failure-matrix validation for the control writer."""

from __future__ import annotations

import asyncio
from dataclasses import asdict
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path

from openframetap.app.input import ControlInput
from openframetap.control.safety import (
    ControlPrerequisites,
    ControlState,
    FailClosedController,
    MockCommandSink,
)
from openframetap.network.secrets import require_private_directory


class VirtualClock:
    def __init__(self) -> None:
        self.now_ns = 1_000_000_000

    def __call__(self) -> int:
        return self.now_ns

    def advance_ms(self, value: int) -> None:
        self.now_ns += value * 1_000_000


READY = ControlPrerequisites(True, True, True, True, True, True, True)


async def _scenario(name: str) -> dict:
    clock = VirtualClock()
    sink = MockCommandSink(fail_calls={1} if name == "send_failure" else None)
    transitions: list[dict] = []
    controller = FailClosedController(
        sink, clock_ns=clock, on_transition=transitions.append
    )
    controller.arm(READY)
    controller.submit(ControlInput(yaw=0.1, pitch=0.05, source=name, active=True))
    await controller.tick()

    if name in {"keyboard_key_up", "touch_up", "touch_cancel"}:
        controller.submit(ControlInput(source=name))
        await controller.tick()
    elif name == "window_focus_loss":
        await controller.focus_lost()
    elif name in {"ui_exit", "ctrl_c"}:
        await controller.emergency_stop(name)
    elif name == "ble_disconnect":
        await controller.disconnect()
    elif name == "send_failure":
        pass  # tick already attempted a bounded zero and entered fault
    elif name == "watchdog_timeout":
        clock.advance_ms(301)
        await controller.tick()
    elif name == "maximum_movement":
        for _ in range(2):
            clock.advance_ms(250)
            controller.submit(ControlInput(yaw=0.1, source=name, active=True))
            await controller.tick()
    elif name == "emergency_stop":
        await controller.emergency_stop()
    else:
        raise ValueError(name)

    final_zero = bool(sink.records and sink.records[-1]["is_zero"])
    return {
        "scenario": name,
        "state": controller.state.value,
        "fault_reason": controller.fault_reason,
        "command_count": len(sink.records),
        "zero_count": sum(bool(item["is_zero"]) for item in sink.records),
        "final_output_zero": final_zero,
        "fff5_write_count": sink.fff5_write_count,
        "records": sink.records,
        "transitions": transitions,
    }


async def _run_all() -> list[dict]:
    names = (
        "keyboard_key_up",
        "touch_up",
        "touch_cancel",
        "window_focus_loss",
        "ui_exit",
        "ctrl_c",
        "ble_disconnect",
        "send_failure",
        "watchdog_timeout",
        "maximum_movement",
        "emergency_stop",
    )
    return [await _scenario(name) for name in names]


def run_mock_validation(output: Path, *, software_git_head: str) -> dict:
    output = require_private_directory(output)
    results = asyncio.run(_run_all())
    jsonl = output / "mock-validation.jsonl"
    with jsonl.open("w", encoding="utf-8", newline="\n") as stream:
        for result in results:
            stream.write(json.dumps(result, sort_keys=True) + "\n")
    summary = {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "software_git_head": software_git_head,
        "scenario_count": len(results),
        "scenarios": [item["scenario"] for item in results],
        "all_final_output_zero": all(item["final_output_zero"] for item in results),
        "fff5_write_count": sum(item["fff5_write_count"] for item in results),
        "fail_closed_scenarios": [
            item["scenario"] for item in results if item["state"] == ControlState.FAULT.value
        ],
    }
    (output / "summary.json").write_text(
        json.dumps(summary, indent=2) + "\n", encoding="utf-8"
    )
    files = (jsonl, output / "summary.json")
    with (output / "checksums.sha256").open("w", encoding="ascii", newline="\n") as stream:
        for path in files:
            stream.write(f"{hashlib.sha256(path.read_bytes()).hexdigest()}  {path.name}\n")
    return summary

