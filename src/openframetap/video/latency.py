"""Timestamp extraction for startup and internal-pipeline latency evidence."""

from __future__ import annotations

from dataclasses import asdict, dataclass


@dataclass(slots=True)
class StartupTimeline:
    command_started_ns: int
    process_started_ns: int | None = None
    sink_caps_observed_ns: int | None = None
    process_ended_ns: int | None = None

    def to_dict(self) -> dict:
        payload = asdict(self)
        payload["process_spawn_seconds"] = (
            (self.process_started_ns - self.command_started_ns) / 1e9
            if self.process_started_ns is not None
            else None
        )
        payload["sink_caps_seconds"] = (
            (self.sink_caps_observed_ns - self.command_started_ns) / 1e9
            if self.sink_caps_observed_ns is not None
            else None
        )
        payload["actual_duration_seconds"] = (
            (self.process_ended_ns - self.process_started_ns) / 1e9
            if self.process_started_ns is not None and self.process_ended_ns is not None
            else None
        )
        return payload


def pipeline_latency_statement() -> dict:
    return {
        "glass_to_glass_measured": False,
        "internal_latency_available": False,
        "statement": (
            "Startup timestamps cover process spawn and sink-cap negotiation only; "
            "they are not glass-to-glass latency."
        ),
    }


def parse_latency_tracer(text: str) -> dict:
    import re

    values = [
        int(value)
        for value in re.findall(r"latency[^\n]*time=\(guint64\)(\d+)", text)
    ]
    return {
        "internal_latency_available": bool(values),
        "sample_count": len(values),
        "minimum_ms": min(values) / 1e6 if values else None,
        "average_ms": sum(values) / len(values) / 1e6 if values else None,
        "maximum_ms": max(values) / 1e6 if values else None,
        "glass_to_glass_measured": False,
        "statement": (
            "GStreamer tracer time covers source-to-sink pipeline processing only; "
            "it excludes camera exposure/encoding and is not glass-to-glass latency."
        ),
    }
