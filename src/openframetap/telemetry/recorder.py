"""Append-only JSONL evidence writer for notifications and DUML frames."""

from __future__ import annotations

from collections import Counter
from datetime import datetime, timezone
import json
from pathlib import Path
from typing import Any

from openframetap.protocol.reassembly import DumlStreamReassembler, ReassemblyEvent
from openframetap.telemetry.decoder import decode_telemetry
from openframetap.transport.bluez_ble import NotificationRecord


def _write_jsonl(path: Path, payload: dict[str, Any]) -> None:
    with path.open("a", encoding="utf-8", newline="\n") as stream:
        stream.write(json.dumps(payload, ensure_ascii=False, sort_keys=True) + "\n")


class TelemetryRecorder:
    FILES = (
        "notifications.jsonl",
        "duml-frames.jsonl",
        "decoded-telemetry.jsonl",
        "unknown-frames.jsonl",
        "events.jsonl",
    )

    def __init__(self, output_dir: Path, *, address: str, operation: str) -> None:
        self.output_dir = output_dir
        self.address = address
        self.operation = operation
        self.output_dir.mkdir(parents=True, exist_ok=True)
        for filename in self.FILES:
            (self.output_dir / filename).write_text("", encoding="utf-8")
        self.reassembler = DumlStreamReassembler()
        self.notification_count = 0
        self.command_counts: Counter[str] = Counter()
        self.parsed_counts: Counter[str] = Counter()
        self.unknown_counts: Counter[str] = Counter()
        self.started_at = datetime.now(timezone.utc)

    def record_event(self, event: dict[str, Any]) -> None:
        _write_jsonl(self.output_dir / "events.jsonl", event)

    async def record_notification(self, notification: NotificationRecord) -> None:
        self.notification_count += 1
        notification_payload = notification.to_dict()
        notification_payload["notification_index"] = self.notification_count
        _write_jsonl(self.output_dir / "notifications.jsonl", notification_payload)
        for event in self.reassembler.feed(notification.data):
            self._record_reassembly_event(event, notification)

    def _record_reassembly_event(
        self, event: ReassemblyEvent, notification: NotificationRecord
    ) -> None:
        base = {
            "wall_timestamp": notification.wall_timestamp,
            "monotonic_ns": notification.monotonic_ns,
            "source_notification_index": self.notification_count,
        }
        if event.kind != "frame" or event.frame is None:
            _write_jsonl(
                self.output_dir / "unknown-frames.jsonl",
                {**base, "reason": event.reason, "raw_hex": event.raw.hex()},
            )
            return
        frame_payload = {**base, **event.frame.to_dict()}
        _write_jsonl(self.output_dir / "duml-frames.jsonl", frame_payload)
        command_key = f"{event.frame.cmd_set:02X}/{event.frame.cmd_id:02X}"
        self.command_counts[command_key] += 1
        decoded = decode_telemetry(event.frame)
        decoded_payload = {**base, "command": command_key, **decoded.to_dict()}
        if decoded.known:
            self.parsed_counts[decoded.message_type] += 1
            _write_jsonl(self.output_dir / "decoded-telemetry.jsonl", decoded_payload)
        else:
            self.unknown_counts[command_key] += 1
            _write_jsonl(
                self.output_dir / "unknown-frames.jsonl",
                {**frame_payload, "decode": decoded.to_dict(), "reason": "unknown_command"},
            )

    def finalize(
        self,
        *,
        actual_seconds: float,
        disconnect_count: int,
        connected: bool,
        error: str | None = None,
    ) -> dict[str, Any]:
        for event in self.reassembler.finish():
            _write_jsonl(
                self.output_dir / "unknown-frames.jsonl",
                {"reason": event.reason, "raw_hex": event.raw.hex()},
            )
        summary = {
            "operation": self.operation,
            "address": self.address,
            "started_at": self.started_at.isoformat(),
            "finished_at": datetime.now(timezone.utc).isoformat(),
            "actual_listen_seconds": actual_seconds,
            "connected_at_end_of_listen": connected,
            "notification_count": self.notification_count,
            "duml_frame_count": self.reassembler.stats.frames_ok,
            "crc_success_count": self.reassembler.stats.frames_ok,
            "crc8_failure_count": self.reassembler.stats.crc8_failures,
            "crc16_failure_count": self.reassembler.stats.crc16_failures,
            "reassembly_failure_count": (
                self.reassembler.stats.invalid_lengths
                + self.reassembler.stats.truncated_fragments
            ),
            "reassembly": self.reassembler.stats.to_dict(),
            "command_counts": dict(sorted(self.command_counts.items())),
            "parsed_message_types": dict(sorted(self.parsed_counts.items())),
            "unknown_message_types": dict(sorted(self.unknown_counts.items())),
            "connection_interruptions": disconnect_count,
            "writes_attempted": 0,
            "pairing_requested": False,
            "error": error,
        }
        (self.output_dir / "summary.json").write_text(
            json.dumps(summary, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
        )
        return summary
