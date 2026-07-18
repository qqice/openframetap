"""Bounded field enumeration for selected DUML payload families."""

from __future__ import annotations

from dataclasses import dataclass
import math
import statistics
import struct
from typing import Any, Iterable, Sequence

from openframetap.analysis.correlation import (
    event_field_metrics,
    monotonic_segment_count,
    wraparound_candidate,
    zero_crossing_count,
)
from openframetap.telemetry.observations import NumericObservation


TARGET_COMMANDS = {"04/05", "04/27", "04/1C", "04/38", "0D/02", "00/81", "02/80"}
ANGLE_SCALES = (1.0, 0.1, 0.01, 1 / 16, 1 / 100, 1 / 1000, 180 / 32768, 360 / 65536)


@dataclass(frozen=True, slots=True)
class FrameSample:
    monotonic_ns: int
    cmd_set: int
    cmd_id: int
    payload: bytes

    @property
    def command(self) -> str:
        return f"{self.cmd_set:02X}/{self.cmd_id:02X}"


def _parse_hex_int(value: int | str) -> int:
    if isinstance(value, int):
        return value
    return int(value, 0)


def frame_samples(records: Iterable[dict[str, Any]]) -> list[FrameSample]:
    samples = []
    for record in records:
        try:
            cmd_set = _parse_hex_int(record["cmd_set"])
            cmd_id = _parse_hex_int(record["cmd_id"])
            payload = bytes.fromhex(record["payload_hex"])
            monotonic_ns = int(record["monotonic_ns"])
        except (KeyError, TypeError, ValueError):
            continue
        samples.append(FrameSample(monotonic_ns, cmd_set, cmd_id, payload))
    return sorted(samples, key=lambda sample: sample.monotonic_ns)


def decode_field(payload: bytes, offset: int, width: int, encoding: str) -> int | float:
    if offset < 0 or width <= 0 or offset + width > len(payload):
        raise ValueError("field extends outside payload")
    raw = payload[offset : offset + width]
    if encoding == "uint8" and width == 1:
        return raw[0]
    if encoding == "int8" and width == 1:
        return int.from_bytes(raw, "little", signed=True)
    if encoding.startswith("uint") or encoding.startswith("int"):
        signed = encoding.startswith("int")
        endian = "little" if encoding.endswith("_le") else "big"
        expected_width = int(encoding[3 if signed else 4 :].split("_")[0]) // 8
        if expected_width != width:
            raise ValueError("encoding width mismatch")
        return int.from_bytes(raw, endian, signed=signed)
    if encoding == "float32_le" and width == 4:
        return struct.unpack("<f", raw)[0]
    if encoding == "float32_be" and width == 4:
        return struct.unpack(">f", raw)[0]
    if encoding.startswith("bit") and width == 1:
        bit = int(encoding[3:])
        if not 0 <= bit <= 7:
            raise ValueError("bit index outside 0..7")
        return (raw[0] >> bit) & 1
    raise ValueError(f"unsupported encoding {encoding!r} for width {width}")


def _candidate_layouts(payload_length: int) -> Iterable[tuple[int, int, str]]:
    for offset in range(payload_length):
        yield offset, 1, "uint8"
        yield offset, 1, "int8"
    for offset in range(0, payload_length - 1, 2):
        for encoding in ("uint16_le", "int16_le", "uint16_be", "int16_be"):
            yield offset, 2, encoding
    for offset in range(0, payload_length - 2, 3):
        for encoding in ("uint24_le", "int24_le", "uint24_be", "int24_be"):
            yield offset, 3, encoding
    for offset in range(0, payload_length - 3, 4):
        for encoding in (
            "uint32_le",
            "int32_le",
            "uint32_be",
            "int32_be",
            "float32_le",
            "float32_be",
        ):
            yield offset, 4, encoding


def _numeric_statistics(
    values: Sequence[float], times_ns: Sequence[int], *, width: int, encoding: str
) -> dict[str, Any]:
    duration_seconds = (times_ns[-1] - times_ns[0]) / 1_000_000_000 if len(times_ns) > 1 else 0
    differences = [right - left for left, right in zip(values, values[1:])]
    return {
        "minimum": min(values),
        "maximum": max(values),
        "mean": statistics.fmean(values),
        "standard_deviation": statistics.pstdev(values) if len(values) >= 2 else 0.0,
        "unique_count": len(set(values)),
        "update_rate": ((len(values) - 1) / duration_seconds if duration_seconds > 0 else 0.0),
        "zero_crossings": zero_crossing_count(values),
        "monotonic_segments": monotonic_segment_count(values),
        "largest_step": max((abs(value) for value in differences), default=0.0),
        "wraparound_candidate": wraparound_candidate(
            values, width=width, signed=encoding.startswith("int")
        ),
    }


def _scale_candidates(values: Sequence[float]) -> list[dict[str, float]]:
    result = []
    for scale in ANGLE_SCALES:
        scaled_min = min(values) * scale
        scaled_max = max(values) * scale
        if max(abs(scaled_min), abs(scaled_max)) <= 720 and scaled_max - scaled_min >= 0.01:
            result.append({"scale": scale, "minimum": scaled_min, "maximum": scaled_max})
    return result


def _ascii_candidates(group: Sequence[FrameSample]) -> list[dict[str, Any]]:
    length = len(group[0].payload)
    result = []
    offset = 0
    while offset < length:
        if all(32 <= sample.payload[offset] <= 126 for sample in group):
            end = offset + 1
            while end < length and all(32 <= sample.payload[end] <= 126 for sample in group):
                end += 1
            if end - offset >= 3:
                values = [sample.payload[offset:end].decode("ascii") for sample in group]
                result.append(
                    {
                        "offset": offset,
                        "width": end - offset,
                        "encoding": "ascii",
                        "minimum": None,
                        "maximum": None,
                        "mean": None,
                        "standard_deviation": None,
                        "static_variance": None,
                        "unique_count": len(set(values)),
                        "update_rate": None,
                        "zero_crossings": None,
                        "monotonic_segments": None,
                        "largest_step": None,
                        "wraparound_candidate": False,
                        "event_correlations": {},
                        "first_value": values[0],
                        "last_value": values[-1],
                        "raw_values_sample": values[:5],
                        "scale_candidates": [],
                    }
                )
            offset = end
        else:
            offset += 1
    return result


def generate_field_candidates(
    records: Iterable[dict[str, Any]],
    events: Sequence[dict[str, Any]] = (),
    *,
    target_commands: set[str] | None = None,
) -> list[dict[str, Any]]:
    targets = target_commands or TARGET_COMMANDS
    samples = [sample for sample in frame_samples(records) if sample.command in targets]
    groups: dict[tuple[str, int], list[FrameSample]] = {}
    for sample in samples:
        groups.setdefault((sample.command, len(sample.payload)), []).append(sample)
    candidates: list[dict[str, Any]] = []
    for (command, payload_length), group in sorted(groups.items()):
        group.sort(key=lambda sample: sample.monotonic_ns)
        times_ns = [sample.monotonic_ns for sample in group]
        for offset, width, encoding in _candidate_layouts(payload_length):
            values = [decode_field(sample.payload, offset, width, encoding) for sample in group]
            if encoding.startswith("float") and any(
                not math.isfinite(value) or abs(value) > 1_000_000 for value in values
            ):
                continue
            numeric = [float(value) for value in values]
            stats = _numeric_statistics(numeric, times_ns, width=width, encoding=encoding)
            metrics = event_field_metrics(times_ns, numeric, events)
            candidates.append(
                {
                    "command": command,
                    "payload_length": payload_length,
                    "sample_count": len(group),
                    "offset": offset,
                    "width": width,
                    "encoding": encoding,
                    **stats,
                    "static_variance": metrics["static_variance"],
                    "event_correlations": metrics,
                    "first_value": values[0],
                    "last_value": values[-1],
                    "last_raw_hex": group[-1].payload[offset : offset + width].hex(),
                    "scale_candidates": _scale_candidates(numeric)
                    if encoding.startswith(("int", "uint", "float"))
                    else [],
                }
            )
        for offset in range(payload_length):
            byte_values = [sample.payload[offset] for sample in group]
            for bit in range(8):
                values = [float((value >> bit) & 1) for value in byte_values]
                if len(set(values)) < 2:
                    continue
                encoding = f"bit{bit}"
                stats = _numeric_statistics(values, times_ns, width=1, encoding=encoding)
                metrics = event_field_metrics(times_ns, values, events)
                candidates.append(
                    {
                        "command": command,
                        "payload_length": payload_length,
                        "sample_count": len(group),
                        "offset": offset,
                        "width": 1,
                        "encoding": encoding,
                        **stats,
                        "static_variance": metrics["static_variance"],
                        "event_correlations": metrics,
                        "first_value": int(values[0]),
                        "last_value": int(values[-1]),
                        "last_raw_hex": group[-1].payload[offset : offset + 1].hex(),
                        "scale_candidates": [],
                    }
                )
        for candidate in _ascii_candidates(group):
            candidates.append(
                {
                    "command": command,
                    "payload_length": payload_length,
                    "sample_count": len(group),
                    **candidate,
                }
            )
    return candidates


def observations_for_candidate(
    records: Iterable[dict[str, Any]], candidate: dict[str, Any]
) -> list[NumericObservation]:
    if candidate["encoding"] == "ascii":
        return []
    command = candidate["command"]
    result = []
    for sample in frame_samples(records):
        if sample.command != command or len(sample.payload) != candidate["payload_length"]:
            continue
        try:
            value = decode_field(
                sample.payload,
                int(candidate["offset"]),
                int(candidate["width"]),
                candidate["encoding"],
            )
        except ValueError:
            continue
        offset = int(candidate["offset"])
        width = int(candidate["width"])
        result.append(
            NumericObservation(
                monotonic_ns=sample.monotonic_ns,
                cmd_set=sample.cmd_set,
                cmd_id=sample.cmd_id,
                payload_length=len(sample.payload),
                offset=offset,
                width=width,
                encoding=candidate["encoding"],
                raw_hex=sample.payload[offset : offset + width].hex(),
                value=value,
            )
        )
    return result
