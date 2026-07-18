from __future__ import annotations

import math
import statistics


def percentile(values: list[float], percent: float) -> float | None:
    if not values:
        return None
    if not 0 <= percent <= 100:
        raise ValueError("percentile must be 0..100")
    ordered = sorted(values)
    position = (len(ordered) - 1) * percent / 100.0
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return ordered[lower]
    fraction = position - lower
    return ordered[lower] * (1 - fraction) + ordered[upper] * fraction


def latency_statistics(values: list[float]) -> dict:
    if not values:
        return {
            key: None
            for key in (
                "mean_latency_ms",
                "median_latency_ms",
                "minimum_latency_ms",
                "maximum_latency_ms",
                "p05_latency_ms",
                "p10_latency_ms",
                "p90_latency_ms",
                "p95_latency_ms",
                "p99_latency_ms",
                "standard_deviation_ms",
                "median_absolute_deviation_ms",
                "jitter_peak_to_peak_ms",
                "long_tail_count",
            )
        }
    median = statistics.median(values)
    deviations = [abs(value - median) for value in values]
    mad = statistics.median(deviations)
    tail_threshold = median + 3 * mad
    return {
        "mean_latency_ms": statistics.fmean(values),
        "median_latency_ms": median,
        "minimum_latency_ms": min(values),
        "maximum_latency_ms": max(values),
        "p05_latency_ms": percentile(values, 5),
        "p10_latency_ms": percentile(values, 10),
        "p90_latency_ms": percentile(values, 90),
        "p95_latency_ms": percentile(values, 95),
        "p99_latency_ms": percentile(values, 99),
        "standard_deviation_ms": statistics.pstdev(values),
        "median_absolute_deviation_ms": mad,
        "jitter_peak_to_peak_ms": max(values) - min(values),
        "long_tail_count": sum(value > tail_threshold for value in values),
    }


def run_length_encode(values: list[int]) -> list[dict]:
    if not values:
        return []
    runs = []
    start = 0
    current = values[0]
    for index, value in enumerate(values[1:], 1):
        if value != current:
            runs.append({"frame_id": current, "start_index": start, "length": index - start})
            current = value
            start = index
    runs.append({"frame_id": current, "start_index": start, "length": len(values) - start})
    return runs
