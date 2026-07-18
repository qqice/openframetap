"""Reproducible report generation without modifying raw experiment evidence."""

from __future__ import annotations

import binascii
from bisect import bisect_right
import csv
from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path
import statistics
import struct
from typing import Any, Iterable, Sequence
import zlib

from openframetap.analysis.alignment import nearest_neighbor_alignment
from openframetap.analysis.correlation import (
    first_difference,
    median_ratio,
    pearson_correlation,
    population_standard_deviation,
    spearman_correlation,
)
from openframetap.analysis.field_candidates import (
    generate_field_candidates,
    observations_for_candidate,
)
from openframetap.analysis.state_model import (
    Pocket3State,
    provenance_from_candidate,
)
from openframetap.telemetry.schemas import ObservationProvenance


RAW_EVIDENCE_FILES = (
    "capture.btsnoop",
    "btmon.txt",
    "notifications.jsonl",
    "duml-frames.jsonl",
    "events.jsonl",
)


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    records = []
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        try:
            records.append(json.loads(line))
        except json.JSONDecodeError as exc:
            raise ValueError(f"invalid JSONL at {path}:{line_number}: {exc}") from exc
    return records


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def verify_raw_checksums(experiment_dir: Path) -> dict[str, Any]:
    manifest = experiment_dir / "checksums.sha256"
    if not manifest.exists():
        raise FileNotFoundError(f"missing checksum manifest: {manifest}")
    entries = []
    for line in manifest.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        parts = line.split(None, 1)
        if len(parts) != 2 or len(parts[0]) != 64:
            raise ValueError(f"invalid checksum line: {line!r}")
        relative = parts[1].lstrip("* ").replace("\\", "/")
        if relative.startswith("/") or ".." in Path(relative).parts:
            raise ValueError(f"unsafe checksum path: {relative!r}")
        path = experiment_dir / relative
        actual = _sha256(path) if path.is_file() else None
        entries.append(
            {
                "file": relative,
                "expected_sha256": parts[0].lower(),
                "actual_sha256": actual,
                "matches": actual == parts[0].lower(),
            }
        )
    names = {entry["file"] for entry in entries}
    missing_manifest_entries = [name for name in RAW_EVIDENCE_FILES if name not in names]
    return {
        "manifest": str(manifest),
        "entries": entries,
        "all_match": all(entry["matches"] for entry in entries)
        and not missing_manifest_entries,
        "missing_manifest_entries": missing_manifest_entries,
    }


def _candidate_id(candidate: dict[str, Any]) -> str:
    return (
        f"{candidate['command']}:len{candidate['payload_length']}:"
        f"off{candidate['offset']}:{candidate['encoding']}"
    )


def _dynamic_rank(candidate: dict[str, Any]) -> tuple[float, float, int]:
    scores = candidate.get("event_correlations", {}).get("axis_scores", {})
    event_score = max((item.get("score", 0.0) for item in scores.values()), default=0.0)
    deviation = float(candidate.get("standard_deviation") or 0.0)
    return event_score, deviation, int(candidate.get("unique_count") or 0)


def _select_candidates(
    candidates: Sequence[dict[str, Any]], command: str, *, limit: int = 24
) -> list[dict[str, Any]]:
    selected = [
        candidate
        for candidate in candidates
        if candidate["command"] == command
        and candidate["encoding"] != "ascii"
        and int(candidate.get("unique_count") or 0) > 1
    ]
    selected.sort(key=_dynamic_rank, reverse=True)
    return selected[:limit]


def _series_pairs(
    frames: Sequence[dict[str, Any]],
    left_candidate: dict[str, Any],
    right_candidate: dict[str, Any],
    *,
    window_ms: float,
    lag_ms: float = 0,
    left_observations=None,
    right_observations=None,
) -> tuple[list[float], list[float], dict[str, Any]]:
    left = (
        left_observations
        if left_observations is not None
        else observations_for_candidate(frames, left_candidate)
    )
    right = (
        right_observations
        if right_observations is not None
        else observations_for_candidate(frames, right_candidate)
    )
    alignment = nearest_neighbor_alignment(
        left,
        right,
        max_window_ms=window_ms,
        source_time=lambda item: item.monotonic_ns + int(lag_ms * 1_000_000),
        target_time=lambda item: item.monotonic_ns,
    )
    left_values = [float(left[item["source_index"]].value) for item in alignment["matches"]]
    right_values = [float(right[item["target_index"]].value) for item in alignment["matches"]]
    return left_values, right_values, alignment


def _pair_analysis(
    frames: Sequence[dict[str, Any]],
    candidates: Sequence[dict[str, Any]],
    windows_ms: Sequence[float],
) -> dict[str, Any]:
    left_candidates = _select_candidates(candidates, "04/05")
    right_candidates = _select_candidates(candidates, "04/27")
    alignment_windows = {}
    left_frames = [record for record in frames if _frame_command(record) == "04/05"]
    right_frames = [record for record in frames if _frame_command(record) == "04/27"]
    for window in windows_ms:
        alignment = nearest_neighbor_alignment(
            left_frames, right_frames, max_window_ms=window
        )
        alignment_windows[str(window)] = {
            key: value for key, value in alignment.items() if key != "matches"
        }
    correlations = []
    primary_window = float(windows_ms[min(1, len(windows_ms) - 1)])
    observation_cache = {
        _candidate_id(candidate): observations_for_candidate(frames, candidate)
        for candidate in [*left_candidates, *right_candidates]
    }
    value_cache = {
        candidate_id: [float(observation.value) for observation in observations]
        for candidate_id, observations in observation_cache.items()
    }
    pair_alignment_cache: dict[tuple[int, int, float], dict[str, Any]] = {}

    def cached_alignment(
        left_candidate: dict[str, Any],
        right_candidate: dict[str, Any],
        lag_ms: float,
    ) -> dict[str, Any]:
        key = (
            int(left_candidate["payload_length"]),
            int(right_candidate["payload_length"]),
            float(lag_ms),
        )
        if key not in pair_alignment_cache:
            left_observations = observation_cache[_candidate_id(left_candidate)]
            right_observations = observation_cache[_candidate_id(right_candidate)]
            pair_alignment_cache[key] = nearest_neighbor_alignment(
                left_observations,
                right_observations,
                max_window_ms=primary_window,
                source_time=lambda item: item.monotonic_ns + int(lag_ms * 1_000_000),
                target_time=lambda item: item.monotonic_ns,
            )
        return pair_alignment_cache[key]

    def aligned_values(
        left_candidate: dict[str, Any],
        right_candidate: dict[str, Any],
        lag_ms: float,
    ) -> tuple[list[float], list[float], dict[str, Any]]:
        alignment = cached_alignment(left_candidate, right_candidate, lag_ms)
        left_values = value_cache[_candidate_id(left_candidate)]
        right_values = value_cache[_candidate_id(right_candidate)]
        return (
            [left_values[item["source_index"]] for item in alignment["matches"]],
            [right_values[item["target_index"]] for item in alignment["matches"]],
            alignment,
        )

    for left_candidate in left_candidates:
        for right_candidate in right_candidates:
            left_values, right_values, alignment = aligned_values(
                left_candidate,
                right_candidate,
                0.0,
            )
            if len(left_values) < 3:
                continue
            pearson = pearson_correlation(left_values, right_values)
            spearman = spearman_correlation(left_values, right_values)
            if pearson is None and spearman is None:
                continue
            left_diff = [b - a for a, b in zip(left_values, left_values[1:])]
            right_diff = [b - a for a, b in zip(right_values, right_values[1:])]
            diff_corr = pearson_correlation(left_diff, right_diff) if len(left_diff) >= 2 else None
            differences = [a - b for a, b in zip(left_values, right_values)]
            sums = [a + b for a, b in zip(left_values, right_values)]
            left_diff_std = (
                population_standard_deviation(left_diff) if len(left_diff) >= 2 else None
            )
            right_diff_std = (
                population_standard_deviation(right_diff) if len(right_diff) >= 2 else None
            )
            low_pass_candidate = None
            if left_diff_std is not None and right_diff_std is not None:
                if left_diff_std < right_diff_std * 0.75:
                    low_pass_candidate = "left_may_be_low_pass_of_right"
                elif right_diff_std < left_diff_std * 0.75:
                    low_pass_candidate = "right_may_be_low_pass_of_left"
            best_lag = {"lag_ms": 0.0, "pearson": pearson}
            for lag in range(-250, 251, 50):
                lag_left, lag_right, _ = aligned_values(
                    left_candidate,
                    right_candidate,
                    float(lag),
                )
                value = pearson_correlation(lag_left, lag_right) if len(lag_left) >= 3 else None
                if value is not None and (
                    best_lag["pearson"] is None or abs(value) > abs(best_lag["pearson"])
                ):
                    best_lag = {"lag_ms": float(lag), "pearson": value}
            ratio = median_ratio(left_values, right_values)
            relationship = "unresolved"
            if pearson is not None and pearson >= 0.95:
                relationship = "same_direction_or_fixed_scale_candidate"
            elif pearson is not None and pearson <= -0.95:
                relationship = "opposite_direction_or_negative_scale_candidate"
            correlations.append(
                {
                    "left_candidate": _candidate_id(left_candidate),
                    "right_candidate": _candidate_id(right_candidate),
                    "matched_count": alignment["matched_count"],
                    "window_ms": primary_window,
                    "pearson": pearson,
                    "spearman": spearman,
                    "first_difference_pearson": diff_corr,
                    "left_minus_right_mean": statistics.fmean(differences),
                    "left_minus_right_standard_deviation": population_standard_deviation(
                        differences
                    ),
                    "left_plus_right_mean": statistics.fmean(sums),
                    "left_plus_right_standard_deviation": population_standard_deviation(sums),
                    "median_left_over_right": ratio,
                    "low_pass_candidate": low_pass_candidate,
                    "best_lag": best_lag,
                    "relationship": relationship,
                }
            )
    correlations.sort(
        key=lambda item: max(
            abs(item["pearson"] or 0.0), abs(item["spearman"] or 0.0)
        ),
        reverse=True,
    )
    return {
        "alignment_windows": alignment_windows,
        "left_variable_candidate_count": len(left_candidates),
        "right_variable_candidate_count": len(right_candidates),
        "correlations": correlations[:100],
    }


def _frame_command(record: dict[str, Any]) -> str:
    try:
        cmd_set = int(record["cmd_set"], 0) if isinstance(record["cmd_set"], str) else int(record["cmd_set"])
        cmd_id = int(record["cmd_id"], 0) if isinstance(record["cmd_id"], str) else int(record["cmd_id"])
    except (KeyError, TypeError, ValueError):
        return "??/??"
    return f"{cmd_set:02X}/{cmd_id:02X}"


def _event_alignment(
    frames: Sequence[dict[str, Any]],
    events: Sequence[dict[str, Any]],
    windows_ms: Sequence[float],
) -> dict[str, Any]:
    result: dict[str, Any] = {"windows_ms": list(windows_ms), "commands": {}}
    relevant_events = [
        event
        for event in events
        if event.get("phase") not in {"session"} and "monotonic_ns" in event
    ]
    for command in ("04/05", "04/27", "04/1C", "04/38", "0D/02"):
        command_frames = [record for record in frames if _frame_command(record) == command]
        result["commands"][command] = {}
        for window in windows_ms:
            alignment = nearest_neighbor_alignment(
                relevant_events, command_frames, max_window_ms=window
            )
            result["commands"][command][str(window)] = {
                key: value for key, value in alignment.items() if key != "matches"
            }
    return result


def _battery_comparison(
    frames: Sequence[dict[str, Any]],
    events: Sequence[dict[str, Any]],
    candidates: Sequence[dict[str, Any]],
    *,
    window_ms: float,
) -> dict[str, Any]:
    candidate = next(
        (
            item
            for item in candidates
            if item["command"] == "0D/02"
            and item["offset"] == 20
            and item["encoding"] == "uint8"
        ),
        None,
    )
    observations = [
        event
        for event in events
        if event.get("event_name") == "displayed_battery_percent"
        and isinstance(event.get("observed_value"), int)
    ]
    if candidate is None:
        return {"status": "candidate_missing", "confidence": "unknown", "matches": []}
    values = observations_for_candidate(frames, candidate)
    if not observations:
        return {
            "status": "manual_observation_missing",
            "confidence": "low",
            "candidate": _candidate_id(candidate),
            "matches": [],
        }
    alignment = nearest_neighbor_alignment(
        observations,
        values,
        max_window_ms=window_ms,
        target_time=lambda item: item.monotonic_ns,
    )
    comparisons = []
    for match in alignment["matches"]:
        manual = int(observations[match["source_index"]]["observed_value"])
        captured = int(values[match["target_index"]].value)
        comparisons.append(
            {
                "manual_percent": manual,
                "captured_value": captured,
                "difference": captured - manual,
                "delta_ns": match["delta_ns"],
                "exact_match": captured == manual,
            }
        )
    if not comparisons:
        status, confidence = "no_observation_within_window", "low"
    elif not all(item["exact_match"] for item in comparisons):
        status, confidence = "screen_value_mismatch", "rejected"
    elif len({item["manual_percent"] for item in comparisons}) == 1:
        status, confidence = "screen_correlated_but_no_transition_observed", "medium"
    else:
        status, confidence = "screen_correlated_with_transition", "high"
    return {
        "status": status,
        "confidence": confidence,
        "candidate": _candidate_id(candidate),
        "window_ms": window_ms,
        "alignment": {key: value for key, value in alignment.items() if key != "matches"},
        "matches": comparisons,
    }


def _axis_candidates(candidates: Sequence[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    result = {}
    for axis in ("yaw", "pitch", "roll"):
        ranked = []
        for candidate in candidates:
            if candidate["command"] not in {"04/05", "04/27"}:
                continue
            if candidate["encoding"] not in {
                "int16_le",
                "int16_be",
                "int32_le",
                "int32_be",
                "float32_le",
                "float32_be",
            }:
                continue
            score = (
                candidate.get("event_correlations", {})
                .get("axis_scores", {})
                .get(axis, {})
                .get("score", 0.0)
            )
            if score > 0:
                axis_metrics = (
                    candidate.get("event_correlations", {})
                    .get("axis_scores", {})
                    .get(axis, {})
                )
                signal_to_noise = float(
                    axis_metrics.get("minimum_signal_to_static_noise", 0.0)
                )
                # Prefer the cleanest primitive field when the bounded score
                # saturates. This avoids choosing an overlapping int32 merely
                # because it appears first in the enumerator.
                ranked.append(
                    (
                        score,
                        signal_to_noise,
                        -int(candidate["width"]),
                        candidate["encoding"].endswith("_le"),
                        candidate,
                    )
                )
        if ranked:
            score, signal_to_noise, _, _, candidate = max(
                ranked, key=lambda item: item[:-1]
            )
            confidence = "medium" if score >= 0.8 else "low"
            result[axis] = {
                "candidate": candidate,
                "candidate_id": _candidate_id(candidate),
                "score": score,
                "minimum_signal_to_static_noise": signal_to_noise,
                "confidence": confidence,
                "scale_status": "fixed_scale_candidates_only_not_selected_as_semantics",
            }
    return result


def _payload_transitions(
    frames: Sequence[dict[str, Any]], events: Sequence[dict[str, Any]]
) -> list[dict[str, Any]]:
    """Describe payload changes against the preceding ROCK monotonic event."""

    ordered_events = sorted(
        (
            event
            for event in events
            if "monotonic_ns" in event and event.get("phase") != "session"
        ),
        key=lambda event: int(event["monotonic_ns"]),
    )
    event_times = [int(event["monotonic_ns"]) for event in ordered_events]
    transitions = []
    previous = None
    for frame in sorted(frames, key=lambda record: int(record["monotonic_ns"])):
        payload = str(frame.get("payload_hex", ""))
        if previous is None:
            previous = payload
            continue
        if payload == previous:
            continue
        timestamp = int(frame["monotonic_ns"])
        event_index = bisect_right(event_times, timestamp) - 1
        preceding = ordered_events[event_index] if event_index >= 0 else None
        transitions.append(
            {
                "monotonic_ns": timestamp,
                "wall_time_utc": frame.get("wall_time_utc"),
                "from_payload_hex": previous,
                "to_payload_hex": payload,
                "nearest_preceding_event": (
                    preceding.get("event_name") if preceding else None
                ),
                "delta_from_event_ms": (
                    (timestamp - int(preceding["monotonic_ns"])) / 1_000_000
                    if preceding
                    else None
                ),
            }
        )
        previous = payload
    return transitions


def _message_observations(
    frames: Sequence[dict[str, Any]], events: Sequence[dict[str, Any]]
) -> dict[str, Any]:
    result = {}
    for command in ("04/05", "04/27", "04/1C", "04/38", "0D/02", "00/81", "02/80"):
        selected = [frame for frame in frames if _frame_command(frame) == command]
        payloads = [frame.get("payload_hex", "") for frame in selected]
        times = [int(frame["monotonic_ns"]) for frame in selected]
        duration = (max(times) - min(times)) / 1_000_000_000 if len(times) > 1 else 0.0
        lengths: dict[str, int] = {}
        for payload in payloads:
            length = str(len(payload) // 2)
            lengths[length] = lengths.get(length, 0) + 1
        observation = {
            "count": len(selected),
            "payload_lengths": lengths,
            "unique_payload_count": len(set(payloads)),
            "update_rate_hz": ((len(selected) - 1) / duration if duration > 0 else 0.0),
            "first_payload_hex": payloads[0] if payloads else None,
            "last_payload_hex": payloads[-1] if payloads else None,
        }
        unique_payloads = set(payloads)
        if len(unique_payloads) <= 16:
            observation["payload_value_counts"] = {
                payload: payloads.count(payload) for payload in sorted(unique_payloads)
            }
        if command == "04/27":
            observation["payload_transitions"] = _payload_transitions(selected, events)
        result[command] = observation
    return result


def _build_state(
    experiment_dir: Path,
    frames: Sequence[dict[str, Any]],
    candidates: Sequence[dict[str, Any]],
    battery: dict[str, Any],
    axes: dict[str, dict[str, Any]],
) -> Pocket3State:
    state = Pocket3State()
    session_name = experiment_dir.name
    latest_ns = max((int(frame["monotonic_ns"]) for frame in frames), default=0)
    battery_candidate = next(
        (
            item
            for item in candidates
            if item["command"] == "0D/02"
            and item["offset"] == 20
            and item["encoding"] == "uint8"
        ),
        None,
    )
    if battery_candidate is not None:
        state.apply(
            "battery_percent",
            int(battery_candidate["last_value"]),
            provenance_from_candidate(
                battery_candidate,
                confidence=battery["confidence"],
                last_updated=latest_ns,
                evidence_session=session_name,
            ),
        )
    product = next(
        (
            item
            for item in candidates
            if item["command"] == "00/81"
            and item["encoding"] == "ascii"
            and item["offset"] == 0
        ),
        None,
    )
    if product is not None:
        cmd_set, cmd_id = (int(part, 16) for part in product["command"].split("/"))
        state.apply(
            "product_identifier",
            str(product["last_value"]),
            ObservationProvenance(
                source_cmd_set=cmd_set,
                source_cmd_id=cmd_id,
                source_offset=int(product["offset"]),
                raw_value=str(product["last_value"]).encode("ascii").hex(),
                decoded_value=product["last_value"],
                confidence="medium",
                last_updated=latest_ns,
                evidence_session=session_name,
                encoding="ascii",
            ),
        )
    for axis, proposal in axes.items():
        candidate = proposal["candidate"]
        state.apply(
            f"gimbal_{axis}_candidate",
            float(candidate["last_value"]),
            provenance_from_candidate(
                candidate,
                confidence=proposal["confidence"],
                last_updated=latest_ns,
                evidence_session=session_name,
            ),
        )
    return state


def _csv_value(value: Any) -> Any:
    if isinstance(value, (dict, list)):
        return json.dumps(value, ensure_ascii=False, sort_keys=True)
    return value


def _write_csv(path: Path, rows: Sequence[dict[str, Any]]) -> None:
    fields = sorted({key for row in rows for key in row})
    with path.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields)
        writer.writeheader()
        for row in rows:
            writer.writerow({key: _csv_value(row.get(key)) for key in fields})


def _png_chunk(kind: bytes, data: bytes) -> bytes:
    return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", binascii.crc32(kind + data) & 0xFFFFFFFF)


def _write_line_plot(
    path: Path,
    series: Sequence[tuple[str, Sequence[tuple[int, float]]]],
    event_times: Sequence[int],
    *,
    title: str,
) -> None:
    width, height = 1200, 480
    pixels = bytearray([255] * width * height * 3)

    def pixel(x: int, y: int, color: tuple[int, int, int]) -> None:
        if 0 <= x < width and 0 <= y < height:
            index = (y * width + x) * 3
            pixels[index : index + 3] = bytes(color)

    def line(x0: int, y0: int, x1: int, y1: int, color: tuple[int, int, int]) -> None:
        dx, sx = abs(x1 - x0), 1 if x0 < x1 else -1
        dy, sy = -abs(y1 - y0), 1 if y0 < y1 else -1
        error = dx + dy
        while True:
            pixel(x0, y0, color)
            if x0 == x1 and y0 == y1:
                break
            doubled = 2 * error
            if doubled >= dy:
                error += dy
                x0 += sx
            if doubled <= dx:
                error += dx
                y0 += sy

    all_times = [timestamp for _, points in series for timestamp, _ in points] + list(event_times)
    start, end = (min(all_times), max(all_times)) if all_times else (0, 1)
    if start == end:
        end += 1
    for x in range(50, width - 20):
        pixel(x, height - 40, (80, 80, 80))
    for y in range(20, height - 39):
        pixel(50, y, (80, 80, 80))
    for timestamp in event_times:
        x = 50 + int((timestamp - start) / (end - start) * (width - 80))
        for y in range(20, height - 40, 3):
            pixel(x, y, (235, 160, 50))
    colors = ((0, 90, 200), (200, 50, 50), (40, 160, 80), (130, 70, 180))
    for series_index, (_label, points) in enumerate(series):
        if not points:
            continue
        values = [value for _, value in points if math.isfinite(value)]
        if not values:
            continue
        low, high = min(values), max(values)
        if low == high:
            high = low + 1
        coordinates = []
        for timestamp, value in points:
            if not math.isfinite(value):
                continue
            x = 50 + int((timestamp - start) / (end - start) * (width - 80))
            y = 20 + int((high - value) / (high - low) * (height - 70))
            coordinates.append((x, y))
        for (x0, y0), (x1, y1) in zip(coordinates, coordinates[1:]):
            line(x0, y0, x1, y1, colors[series_index % len(colors)])
    raw = b"".join(b"\x00" + bytes(pixels[row * width * 3 : (row + 1) * width * 3]) for row in range(height))
    png = b"\x89PNG\r\n\x1a\n"
    png += _png_chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
    png += _png_chunk(b"tEXt", b"Title\x00" + title.encode("latin-1", errors="replace"))
    png += _png_chunk(b"IDAT", zlib.compress(raw, level=6))
    png += _png_chunk(b"IEND", b"")
    path.write_bytes(png)


def _candidate_series(
    frames: Sequence[dict[str, Any]], candidate: dict[str, Any]
) -> list[tuple[int, float]]:
    return [
        (item.monotonic_ns, float(item.value))
        for item in observations_for_candidate(frames, candidate)
    ]


def _write_plots(
    plots_dir: Path,
    frames: Sequence[dict[str, Any]],
    events: Sequence[dict[str, Any]],
    candidates: Sequence[dict[str, Any]],
    pair_analysis: dict[str, Any],
    axes: dict[str, dict[str, Any]],
) -> None:
    plots_dir.mkdir(parents=True, exist_ok=True)
    event_times = [int(event["monotonic_ns"]) for event in events if "monotonic_ns" in event]
    command_top = {
        command: _select_candidates(candidates, command, limit=3)
        for command in ("04/05", "04/27")
    }
    for command, filename in (
        ("04/05", "04_05_fields_vs_time.png"),
        ("04/27", "04_27_fields_vs_time.png"),
    ):
        series = [
            (_candidate_id(candidate), _candidate_series(frames, candidate))
            for candidate in command_top[command]
        ]
        _write_line_plot(plots_dir / filename, series, event_times, title=command)
    pair_series = []
    if pair_analysis["correlations"]:
        top = pair_analysis["correlations"][0]
        by_id = {_candidate_id(candidate): candidate for candidate in candidates}
        for side in ("left_candidate", "right_candidate"):
            candidate = by_id.get(top[side])
            if candidate:
                pair_series.append((top[side], _candidate_series(frames, candidate)))
    _write_line_plot(
        plots_dir / "04_05_04_27_candidate_pair.png",
        pair_series,
        event_times,
        title="04/05 and 04/27 candidate pair",
    )
    for axis in ("yaw", "pitch", "roll"):
        series = []
        if axis in axes:
            candidate = axes[axis]["candidate"]
            series = [(_candidate_id(candidate), _candidate_series(frames, candidate))]
        _write_line_plot(
            plots_dir / f"{axis}_event_aligned_candidates.png",
            series,
            event_times,
            title=f"{axis} event-aligned candidates",
        )
    battery_candidate = next(
        (
            candidate
            for candidate in candidates
            if candidate["command"] == "0D/02"
            and candidate["offset"] == 20
            and candidate["encoding"] == "uint8"
        ),
        None,
    )
    battery_series = []
    if battery_candidate:
        battery_series.append(("0D/02 offset20 uint8", _candidate_series(frames, battery_candidate)))
    manual_points = [
        (int(event["monotonic_ns"]), float(event["observed_value"]))
        for event in events
        if event.get("event_name") == "displayed_battery_percent"
        and isinstance(event.get("observed_value"), int)
    ]
    battery_series.append(("screen observation", manual_points))
    _write_line_plot(
        plots_dir / "battery_candidate_vs_observation.png",
        battery_series,
        event_times,
        title="battery candidate versus screen observation",
    )


def _write_markdown_report(
    path: Path,
    *,
    session: dict[str, Any],
    candidates: Sequence[dict[str, Any]],
    pair_analysis: dict[str, Any],
    battery: dict[str, Any],
    axes: dict[str, dict[str, Any]],
    checksum_before: dict[str, Any],
    checksum_after: dict[str, Any],
    message_observations: dict[str, Any],
) -> None:
    command_counts = session.get("message_counts", {})
    lines = [
        "# Pocket 3 passive telemetry report",
        "",
        "## Evidence boundary",
        "",
        f"【实机事实】The experiment received {session.get('notifications_received', 0)} BLE notifications and {session.get('duml_frames_received', 0)} CRC-valid DUML frames.",
        f"【实机事实】FFF5 write count was {session.get('fff5_write_count')}; CCCD operation count was {session.get('cccd_write_count')}.",
        f"【实机事实】Raw checksum validation passed before analysis: {checksum_before['all_match']}; after analysis: {checksum_after['all_match']}.",
        "",
        "## Message observations",
        "",
        f"【统计观察】Message counts: `{json.dumps(command_counts, sort_keys=True)}`.",
        f"【统计观察】Payload-level observations: `{json.dumps(message_observations, sort_keys=True)}`.",
        f"【统计观察】The bounded enumerator produced {len(candidates)} aligned numeric, bit, float-domain-filtered, and ASCII candidates grouped by payload length.",
        "",
        "## Controlled motion candidates",
        "",
    ]
    if axes:
        for axis, proposal in axes.items():
            lines.append(
                f"【捕获推断】{axis} candidate `{proposal['candidate_id']}` scored {proposal['score']:.3f} for opposite-direction controlled actions; confidence is {proposal['confidence']}. No physical unit or protocol semantic is confirmed."
            )
    else:
        lines.append(
            "【待验证假设】No candidate had both required opposite-direction action intervals; yaw/pitch/roll semantics remain unresolved."
        )
    lines.extend(
        [
            "",
            "## 04/05 and 04/27 relationship",
            "",
            f"【统计观察】The configured alignment windows were {list(pair_analysis['alignment_windows'])} ms labels, with unmatched ratios retained in `message-pair-analysis.json`.",
        ]
    )
    if pair_analysis["correlations"]:
        top = pair_analysis["correlations"][0]
        lines.append(
            f"【捕获推断】The strongest retained pair is `{top['left_candidate']}` versus `{top['right_candidate']}` with Pearson={top['pearson']} and Spearman={top['spearman']}. Correlation alone does not name either field."
        )
    else:
        if pair_analysis["right_variable_candidate_count"] == 0:
            lines.append(
                "【统计观察】04/05 and 04/27 aligned in time, but 04/27 had no changing bounded numeric field in this session, so a numeric correlation cannot be computed."
            )
        else:
            lines.append("【待验证假设】No sufficiently sampled 04/05 ↔ 04/27 numeric pair was available.")
    transitions_04_27 = message_observations.get("04/27", {}).get(
        "payload_transitions", []
    )
    if transitions_04_27:
        lines.append(
            "【统计观察】04/27 payload transitions against the nearest preceding "
            f"manual event: `{json.dumps(transitions_04_27, sort_keys=True)}`."
        )
        lines.append(
            "【捕获推断】04/27 behaves as a sparse state/threshold candidate in this "
            "session, not as a continuously varying three-axis value; its exact "
            "meaning remains unresolved."
        )
    lines.extend(
        [
            "",
            "## Battery and identity",
            "",
            f"【统计观察】0D/02 offset 20 comparison status: `{battery['status']}`; confidence `{battery['confidence']}`.",
            "【参考实现结论】Offset 20 is used as a battery candidate by some public implementations, while other implementations disagree; local screen correlation controls any confidence upgrade.",
            "【捕获推断】ASCII `hg212` is preserved as a product-identifier candidate only; this report does not promote it to a confirmed model code.",
            "",
            "## Rejected and unresolved interpretations",
            "",
            "【已否定假设】02/80 is not treated as `pairing_started`; prior paired-session evidence showed it continuing after an explicit already-paired response.",
            "【待验证假设】04/1C, 04/38, camera mode, target angle, angular velocity, and error terms remain unnamed unless controlled repetition and cross-message relationships support them.",
            "",
            "## Safety",
            "",
            "【实机事实】The experiment entrypoint contains no DJI query or FFF5 send call. Only FFF4 notification CCCD enable/disable is permitted.",
        ]
    )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def analyze_experiment(
    experiment_dir: Path,
    *,
    windows_ms: Sequence[float] = (50.0, 100.0, 250.0),
    battery_window_ms: float = 1000.0,
    analysis_git_head: str = "unknown",
    analysis_location: str = "unknown",
) -> dict[str, Any]:
    experiment_dir = experiment_dir.resolve()
    session_path = experiment_dir / "session.json"
    session = json.loads(session_path.read_text(encoding="utf-8"))
    if int(session.get("fff5_write_count", -1)) != 0:
        raise RuntimeError(
            f"SAFETY FAILURE: refusing analysis because fff5_write_count={session.get('fff5_write_count')}"
        )
    checksum_before = verify_raw_checksums(experiment_dir)
    if not checksum_before["all_match"]:
        raise RuntimeError("raw evidence checksum verification failed before analysis")
    frames = _read_jsonl(experiment_dir / "duml-frames.jsonl")
    events = _read_jsonl(experiment_dir / "events.jsonl")
    candidates = generate_field_candidates(frames, events)
    pair_analysis = _pair_analysis(frames, candidates, windows_ms)
    message_observations = _message_observations(frames, events)
    event_alignment = _event_alignment(frames, events, windows_ms)
    battery = _battery_comparison(
        frames, events, candidates, window_ms=battery_window_ms
    )
    axes = _axis_candidates(candidates)
    state = _build_state(experiment_dir, frames, candidates, battery, axes)

    analysis_dir = experiment_dir / "analysis"
    analysis_dir.mkdir(parents=True, exist_ok=True)
    (analysis_dir / "field-candidates.json").write_text(
        json.dumps(candidates, indent=2, ensure_ascii=False, allow_nan=False) + "\n",
        encoding="utf-8",
    )
    _write_csv(analysis_dir / "field-candidates.csv", candidates)
    (analysis_dir / "event-alignment.json").write_text(
        json.dumps(event_alignment, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    _write_csv(analysis_dir / "correlations.csv", pair_analysis["correlations"])
    (analysis_dir / "message-pair-analysis.json").write_text(
        json.dumps(pair_analysis, indent=2, ensure_ascii=False, allow_nan=False) + "\n",
        encoding="utf-8",
    )
    (analysis_dir / "message-observations.json").write_text(
        json.dumps(message_observations, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    (analysis_dir / "state-model.json").write_text(
        json.dumps(state.to_dict(), indent=2, ensure_ascii=False, allow_nan=False) + "\n",
        encoding="utf-8",
    )
    _write_plots(
        analysis_dir / "plots", frames, events, candidates, pair_analysis, axes
    )
    checksum_after = verify_raw_checksums(experiment_dir)
    if not checksum_after["all_match"]:
        raise RuntimeError("raw evidence checksum verification failed after analysis")
    _write_markdown_report(
        analysis_dir / "telemetry-report.md",
        session=session,
        candidates=candidates,
        pair_analysis=pair_analysis,
        battery=battery,
        axes=axes,
        checksum_before=checksum_before,
        checksum_after=checksum_after,
        message_observations=message_observations,
    )
    metadata = {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "analysis_git_head": analysis_git_head,
        "analysis_location": analysis_location,
        "experiment_directory": str(experiment_dir),
        "alignment_windows_ms": list(windows_ms),
        "battery_alignment_window_ms": battery_window_ms,
        "checksum_before": checksum_before,
        "checksum_after": checksum_after,
        "field_candidate_count": len(candidates),
        "axis_candidates": {
            axis: {key: value for key, value in proposal.items() if key != "candidate"}
            for axis, proposal in axes.items()
        },
        "battery_comparison": battery,
        "message_observations": message_observations,
    }
    (analysis_dir / "analysis-metadata.json").write_text(
        json.dumps(metadata, indent=2, ensure_ascii=False, allow_nan=False) + "\n",
        encoding="utf-8",
    )
    return metadata
