"""Monotonic-clock nearest-neighbor alignment."""

from __future__ import annotations

from bisect import bisect_left
from dataclasses import asdict, dataclass
from typing import Any, Callable, Sequence


@dataclass(frozen=True, slots=True)
class AlignmentMatch:
    source_index: int
    target_index: int
    source_monotonic_ns: int
    target_monotonic_ns: int
    delta_ns: int

    def to_dict(self) -> dict[str, int]:
        return asdict(self)


def nearest_neighbor_alignment(
    source: Sequence[Any],
    target: Sequence[Any],
    *,
    max_window_ms: float,
    source_time: Callable[[Any], int] = lambda item: int(item["monotonic_ns"]),
    target_time: Callable[[Any], int] = lambda item: int(item["monotonic_ns"]),
) -> dict[str, Any]:
    if max_window_ms < 0:
        raise ValueError("max_window_ms must be non-negative")
    target_order = sorted(enumerate(target), key=lambda pair: target_time(pair[1]))
    target_times = [target_time(item) for _, item in target_order]
    max_delta = int(max_window_ms * 1_000_000)
    matches: list[AlignmentMatch] = []
    used_targets: set[int] = set()
    for source_index, item in enumerate(source):
        timestamp = source_time(item)
        position = bisect_left(target_times, timestamp)
        candidates = []
        if position < len(target_times):
            candidates.append(position)
        if position > 0:
            candidates.append(position - 1)
        if not candidates:
            continue
        best = min(candidates, key=lambda index: abs(target_times[index] - timestamp))
        delta = target_times[best] - timestamp
        if abs(delta) <= max_delta:
            target_index = target_order[best][0]
            used_targets.add(target_index)
            matches.append(
                AlignmentMatch(
                    source_index=source_index,
                    target_index=target_index,
                    source_monotonic_ns=timestamp,
                    target_monotonic_ns=target_times[best],
                    delta_ns=delta,
                )
            )
    source_count = len(source)
    target_count = len(target)
    return {
        "max_window_ms": max_window_ms,
        "source_count": source_count,
        "target_count": target_count,
        "matched_count": len(matches),
        "source_unmatched_count": source_count - len(matches),
        "source_unmatched_ratio": (
            (source_count - len(matches)) / source_count if source_count else 0.0
        ),
        "target_unmatched_count": target_count - len(used_targets),
        "target_unmatched_ratio": (
            (target_count - len(used_targets)) / target_count if target_count else 0.0
        ),
        "matches": [match.to_dict() for match in matches],
    }
