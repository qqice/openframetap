from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path


def unwrap_frame_ids(values: list[int], bits: int) -> list[int]:
    if not values:
        return []
    modulus = 1 << bits
    half = modulus // 2
    offset = 0
    previous = values[0]
    result = [previous]
    for value in values[1:]:
        delta = value - previous
        if delta < -half:
            offset += modulus
        elif delta > half:
            offset -= modulus
        result.append(value + offset)
        previous = value
    return result


@dataclass(slots=True)
class PatternTimingMap:
    bits: int
    by_unwrapped_id: dict[int, int]
    candidates_by_modulo: dict[int, list[int]]
    refresh_hz: float

    @classmethod
    def load(cls, path: Path, *, bits: int) -> "PatternTimingMap":
        if not path.is_file():
            raise FileNotFoundError(f"pattern timing log not found: {path}")
        records = []
        for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if not line.strip():
                continue
            try:
                item = json.loads(line)
                records.append(
                    (int(item["frame_id"]), int(item["monotonic_ns"]), float(item["refresh_hz"]))
                )
            except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
                raise ValueError(f"invalid pattern timing JSONL line {line_number}") from exc
        if len(records) < 2:
            raise ValueError("pattern timing log has fewer than two records")
        timestamps = [item[1] for item in records]
        if any(right <= left for left, right in zip(timestamps, timestamps[1:])):
            raise ValueError("pattern monotonic timestamps are not strictly increasing")
        unwrapped = unwrap_frame_ids([item[0] for item in records], bits)
        mapping = dict(zip(unwrapped, timestamps, strict=True))
        candidates: dict[int, list[int]] = {}
        for raw, absolute in zip((item[0] for item in records), unwrapped, strict=True):
            candidates.setdefault(raw, []).append(absolute)
        return cls(bits, mapping, candidates, records[0][2])

    def resolve_pair(
        self,
        source_frame_id: int,
        displayed_frame_id: int,
        *,
        previous_source_unwrapped: int | None,
        maximum_latency_ms: float,
    ) -> tuple[int, int, int, int]:
        modulus = 1 << self.bits
        delta = (source_frame_id - displayed_frame_id) % modulus
        maximum_frames = maximum_latency_ms * self.refresh_hz / 1000.0 + 2
        if delta > maximum_frames:
            raise ValueError("decoded source/display ID delta exceeds maximum latency")
        candidates = self.candidates_by_modulo.get(source_frame_id, [])
        if previous_source_unwrapped is not None:
            candidates = [value for value in candidates if value >= previous_source_unwrapped]
        for source_absolute in candidates:
            displayed_absolute = source_absolute - delta
            if displayed_absolute in self.by_unwrapped_id:
                return (
                    source_absolute,
                    displayed_absolute,
                    self.by_unwrapped_id[source_absolute],
                    self.by_unwrapped_id[displayed_absolute],
                )
        raise ValueError("decoded frame IDs do not map to the pattern timing log")
