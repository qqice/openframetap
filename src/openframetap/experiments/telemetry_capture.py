"""Append-only raw evidence recorder for synchronized experiments."""

from __future__ import annotations

from collections import Counter
from datetime import datetime, timezone
import json
from pathlib import Path
from typing import Any

from openframetap.protocol.reassembly import DumlStreamReassembler, ReassemblyEvent
from openframetap.telemetry.decoder import decode_telemetry
from openframetap.transport.bluez_ble import NotificationRecord


def _append_jsonl(path: Path, payload: dict[str, Any]) -> None:
    with path.open("a", encoding="utf-8", newline="\n") as stream:
        stream.write(json.dumps(payload, ensure_ascii=False, sort_keys=True) + "\n")
        stream.flush()


class ExperimentTelemetryRecorder:
    RAW_FILES = ("notifications.jsonl", "duml-frames.jsonl", "unknown-frames.jsonl")

    def __init__(self, output_dir: Path) -> None:
        self.output_dir = output_dir
        output_dir.mkdir(parents=True, exist_ok=True)
        for filename in self.RAW_FILES:
            (output_dir / filename).write_text("", encoding="utf-8")
        self.reassembler = DumlStreamReassembler()
        self.notification_count = 0
        self.command_counts: Counter[str] = Counter()
        self.unknown_counts: Counter[str] = Counter()

    def record_notification(self, notification: NotificationRecord) -> None:
        self.notification_count += 1
        reassembly_events = self.reassembler.feed(notification.data)
        complete_frames = [event.frame for event in reassembly_events if event.frame is not None]
        notification_payload: dict[str, Any] = {
            "wall_time_utc": notification.wall_timestamp,
            "monotonic_ns": notification.monotonic_ns,
            "characteristic_uuid": notification.characteristic_uuid,
            "characteristic_handle": notification.characteristic_handle,
            "raw_hex": notification.data.hex(),
            "notification_index": self.notification_count,
            "duml_cmd_set": complete_frames[0].cmd_set if len(complete_frames) == 1 else None,
            "duml_cmd_id": complete_frames[0].cmd_id if len(complete_frames) == 1 else None,
            "payload_hex": complete_frames[0].payload.hex() if len(complete_frames) == 1 else None,
            "complete_frame_count": len(complete_frames),
            "complete_frames": [
                {
                    "cmd_set": frame.cmd_set,
                    "cmd_id": frame.cmd_id,
                    "payload_hex": frame.payload.hex(),
                    "raw_hex": frame.raw.hex(),
                }
                for frame in complete_frames
            ],
        }
        _append_jsonl(self.output_dir / "notifications.jsonl", notification_payload)
        for event in reassembly_events:
            self._record_reassembly_event(event, notification)

    def _record_reassembly_event(
        self, event: ReassemblyEvent, notification: NotificationRecord
    ) -> None:
        base = {
            "wall_time_utc": notification.wall_timestamp,
            "monotonic_ns": notification.monotonic_ns,
            "source_notification_index": self.notification_count,
        }
        if event.kind != "frame" or event.frame is None:
            _append_jsonl(
                self.output_dir / "unknown-frames.jsonl",
                {**base, "reason": event.reason, "raw_hex": event.raw.hex()},
            )
            return
        frame_payload = {**base, **event.frame.to_dict()}
        _append_jsonl(self.output_dir / "duml-frames.jsonl", frame_payload)
        key = f"{event.frame.cmd_set:02X}/{event.frame.cmd_id:02X}"
        self.command_counts[key] += 1
        decoded = decode_telemetry(event.frame)
        if not decoded.known:
            self.unknown_counts[key] += 1
            _append_jsonl(
                self.output_dir / "unknown-frames.jsonl",
                {**frame_payload, "reason": "unknown_command", "decode": decoded.to_dict()},
            )

    def finalize_reassembly(self) -> None:
        for event in self.reassembler.finish():
            _append_jsonl(
                self.output_dir / "unknown-frames.jsonl",
                {"reason": event.reason, "raw_hex": event.raw.hex()},
            )

    def write_message_counts(self) -> dict[str, Any]:
        payload = {
            "generated_at_utc": datetime.now(timezone.utc).isoformat(),
            "notifications_received": self.notification_count,
            "duml_frames_received": self.reassembler.stats.frames_ok,
            "command_counts": dict(sorted(self.command_counts.items())),
            "unknown_command_counts": dict(sorted(self.unknown_counts.items())),
        }
        (self.output_dir / "message-counts.json").write_text(
            json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
        )
        return payload
