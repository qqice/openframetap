"""Dependency-free correlation and controlled-event statistics."""

from __future__ import annotations

from collections import defaultdict
from bisect import bisect_left, bisect_right
import math
import statistics
from typing import Any, Iterable, Sequence


ACTION_EVENTS = {
    "yaw_left_start": "yaw",
    "yaw_right_start": "yaw",
    "pitch_up_start": "pitch",
    "pitch_down_start": "pitch",
    "roll_clockwise_start": "roll",
    "roll_counter_clockwise_start": "roll",
}


def population_variance(values: Sequence[float]) -> float:
    """Fast floating-point population variance for high-volume candidate scans."""

    if not values:
        raise ValueError("population variance requires at least one value")
    mean = math.fsum(values) / len(values)
    return math.fsum((value - mean) ** 2 for value in values) / len(values)


def population_standard_deviation(values: Sequence[float]) -> float:
    return math.sqrt(population_variance(values))


def pearson_correlation(left: Sequence[float], right: Sequence[float]) -> float | None:
    if len(left) != len(right):
        raise ValueError("correlation inputs must have equal length")
    if len(left) < 2:
        return None
    left_mean = statistics.fmean(left)
    right_mean = statistics.fmean(right)
    left_centered = [value - left_mean for value in left]
    right_centered = [value - right_mean for value in right]
    denominator = math.sqrt(
        sum(value * value for value in left_centered)
        * sum(value * value for value in right_centered)
    )
    if denominator == 0:
        return None
    return sum(a * b for a, b in zip(left_centered, right_centered)) / denominator


def _ranks(values: Sequence[float]) -> list[float]:
    order = sorted(range(len(values)), key=lambda index: values[index])
    ranks = [0.0] * len(values)
    position = 0
    while position < len(order):
        end = position + 1
        while end < len(order) and values[order[end]] == values[order[position]]:
            end += 1
        average_rank = (position + 1 + end) / 2
        for index in order[position:end]:
            ranks[index] = average_rank
        position = end
    return ranks


def spearman_correlation(left: Sequence[float], right: Sequence[float]) -> float | None:
    if len(left) != len(right):
        raise ValueError("correlation inputs must have equal length")
    if len(left) < 2:
        return None
    return pearson_correlation(_ranks(left), _ranks(right))


def first_difference(values: Sequence[float], times_ns: Sequence[int]) -> list[float]:
    if len(values) != len(times_ns):
        raise ValueError("value and time inputs must have equal length")
    result = []
    for index in range(1, len(values)):
        seconds = (times_ns[index] - times_ns[index - 1]) / 1_000_000_000
        result.append((values[index] - values[index - 1]) / seconds if seconds > 0 else 0.0)
    return result


def monotonic_segment_count(values: Sequence[float]) -> int:
    signs = []
    for left, right in zip(values, values[1:]):
        delta = right - left
        sign = 1 if delta > 0 else -1 if delta < 0 else 0
        if sign:
            signs.append(sign)
    if not signs:
        return 0
    return 1 + sum(left != right for left, right in zip(signs, signs[1:]))


def zero_crossing_count(values: Sequence[float]) -> int:
    crossings = 0
    previous = 0
    for value in values:
        sign = 1 if value > 0 else -1 if value < 0 else 0
        if sign and previous and sign != previous:
            crossings += 1
        if sign:
            previous = sign
    return crossings


def wraparound_candidate(values: Sequence[float], *, width: int, signed: bool) -> bool:
    if signed or len(values) < 3 or width not in {1, 2, 3, 4}:
        return False
    modulus = float(1 << (width * 8))
    if max(values) - min(values) < modulus * 0.75:
        return False
    return any(abs(right - left) > modulus * 0.5 for left, right in zip(values, values[1:]))


def build_action_intervals(events: Sequence[dict[str, Any]], end_ns: int) -> list[dict[str, Any]]:
    ordered = sorted(events, key=lambda event: int(event["monotonic_ns"]))
    intervals: list[dict[str, Any]] = []
    active: dict[str, Any] | None = None
    for event in ordered:
        name = event.get("event_name")
        timestamp = int(event["monotonic_ns"])
        if name == "baseline_static_start" or name in ACTION_EVENTS:
            if active is not None and timestamp > active["start_ns"]:
                intervals.append({**active, "end_ns": timestamp})
            active = {
                "event_name": name,
                "phase": event.get("phase"),
                "start_ns": timestamp,
            }
        elif name in {"return_to_neutral", "end_current_action"}:
            if active is not None and timestamp > active["start_ns"]:
                intervals.append({**active, "end_ns": timestamp})
                active = None
        elif name == "stable_interval":
            next_end = next(
                (
                    int(candidate["monotonic_ns"])
                    for candidate in ordered
                    if int(candidate["monotonic_ns"]) > timestamp
                ),
                end_ns,
            )
            if next_end > timestamp:
                intervals.append(
                    {
                        "event_name": "stable_interval",
                        "phase": "stable",
                        "start_ns": timestamp,
                        "end_ns": next_end,
                    }
                )
    if active is not None and end_ns > active["start_ns"]:
        intervals.append({**active, "end_ns": end_ns})
    return intervals


def event_field_metrics(
    times_ns: Sequence[int],
    values: Sequence[float],
    events: Sequence[dict[str, Any]],
    *,
    prepared_windows: dict[str, Any] | None = None,
) -> dict[str, Any]:
    if not times_ns or not events:
        return {
            "action_deltas": {},
            "axis_scores": {},
            "static_variance": None,
            "action_interval_count": 0,
        }
    prepared = prepared_windows or prepare_event_windows(times_ns, events)
    effects: defaultdict[str, list[float]] = defaultdict(list)
    static_variances = [
        population_variance([values[index] for index in indexes])
        for indexes in prepared["static_intervals"]
        if len(indexes) >= 2
    ]
    for interval in prepared["intervals"]:
        indexes = interval["indexes"]
        if not indexes:
            continue
        name = interval["event_name"]
        selected = [values[index] for index in indexes]
        if name in ACTION_EVENTS:
            before = [values[index] for index in interval["before_indexes"]]
            baseline = statistics.median(before) if before else selected[0]
            effects[name].append(statistics.median(selected) - baseline)

    action_deltas = {
        name: {
            "count": len(deltas),
            "median_delta": statistics.median(deltas),
            "mean_delta": statistics.fmean(deltas),
        }
        for name, deltas in sorted(effects.items())
    }
    static_variance = math.fsum(static_variances) / len(static_variances) if static_variances else None
    static_std = math.sqrt(static_variance) if static_variance is not None else 0.0
    pairs = {
        "yaw": ("yaw_left_start", "yaw_right_start"),
        "pitch": ("pitch_up_start", "pitch_down_start"),
        "roll": ("roll_clockwise_start", "roll_counter_clockwise_start"),
    }
    axis_scores: dict[str, Any] = {}
    for axis, (positive_name, negative_name) in pairs.items():
        if positive_name not in effects or negative_name not in effects:
            continue
        positive = statistics.median(effects[positive_name])
        negative = statistics.median(effects[negative_name])
        opposite = positive * negative < 0
        noise = max(static_std, 1e-9)
        snr = min(abs(positive), abs(negative)) / noise
        score = (1.0 if opposite else 0.0) * min(1.0, snr / 3.0)
        axis_scores[axis] = {
            "score": score,
            "opposite_direction": opposite,
            "first_action_delta": positive,
            "opposite_action_delta": negative,
            "minimum_signal_to_static_noise": snr,
        }
    return {
        "action_deltas": action_deltas,
        "axis_scores": axis_scores,
        "static_variance": static_variance,
        "action_interval_count": len(prepared["intervals"]),
    }


def prepare_event_windows(
    times_ns: Sequence[int], events: Sequence[dict[str, Any]]
) -> dict[str, Any]:
    """Precompute time-to-index windows once for every field in one payload group."""

    if not times_ns or not events:
        return {"intervals": [], "static_indexes": [], "static_intervals": []}
    intervals = build_action_intervals(events, max(times_ns))
    prepared = []
    static_indexes: set[int] = set()
    static_intervals: list[list[int]] = []
    for interval in intervals:
        start = bisect_left(times_ns, interval["start_ns"])
        end = bisect_right(times_ns, interval["end_ns"])
        indexes = list(range(start, end))
        before_start = bisect_left(times_ns, interval["start_ns"] - 1_000_000_000)
        before_end = bisect_left(times_ns, interval["start_ns"])
        before_indexes = list(range(before_start, before_end))
        if interval["event_name"] in {"baseline_static_start", "stable_interval"}:
            static_indexes.update(indexes)
            static_intervals.append(indexes)
        prepared.append(
            {
                **interval,
                "indexes": indexes,
                "before_indexes": before_indexes,
            }
        )
    return {
        "intervals": prepared,
        "static_indexes": sorted(static_indexes),
        "static_intervals": static_intervals,
    }


def median_ratio(left: Iterable[float], right: Iterable[float]) -> float | None:
    ratios = [a / b for a, b in zip(left, right) if b != 0]
    return statistics.median(ratios) if ratios else None
