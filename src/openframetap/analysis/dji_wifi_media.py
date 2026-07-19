"""Offline reconstruction of Pocket 3 H.264 from immutable Wi-Fi PCAP evidence."""

from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
from typing import Iterable

from openframetap.analysis.mimo_wifi import UdpDatagram, _read_udp
from openframetap.network.secrets import require_private_directory
from openframetap.protocol.dji_wifi import DjiWifiBasicHeader, DjiWifiEnvelopeError
from openframetap.protocol.dji_wifi_media import (
    DJI_WIFI_MEDIA_ACCESS_UNIT_HEADER_LENGTH,
    DJI_WIFI_MEDIA_TYPE,
    DjiWifiMediaAccessUnitHeader,
    DjiWifiMediaError,
    DjiWifiMediaFragment,
)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def annex_b_nal_types(data: bytes) -> list[int]:
    """Return NAL unit types from a byte-stream without decoding the payload."""

    starts: list[int] = []
    cursor = 0
    while cursor + 3 < len(data):
        if data[cursor : cursor + 4] == b"\x00\x00\x00\x01":
            starts.append(cursor + 4)
            cursor += 4
        elif data[cursor : cursor + 3] == b"\x00\x00\x01":
            starts.append(cursor + 3)
            cursor += 3
        else:
            cursor += 1
    return [data[start] & 0x1F for start in starts if start < len(data)]


@dataclass(frozen=True, slots=True)
class MediaFragmentObservation:
    packet_index: int
    capture_timestamp: float
    fragment: DjiWifiMediaFragment


@dataclass(slots=True)
class _Assembly:
    start: MediaFragmentObservation
    header: DjiWifiMediaAccessUnitHeader
    fragments: dict[int, MediaFragmentObservation] = field(default_factory=dict)
    duplicate_count: int = 0
    conflicting_duplicate_count: int = 0
    retransmission_generations: Counter[int] = field(default_factory=Counter)

    def add(self, observation: MediaFragmentObservation) -> None:
        fragment = observation.fragment
        previous = self.fragments.get(fragment.fragment_index)
        self.retransmission_generations[fragment.retransmission_generation] += 1
        if previous is None:
            self.fragments[fragment.fragment_index] = observation
            return
        self.duplicate_count += 1
        if previous.fragment.payload != fragment.payload:
            self.conflicting_duplicate_count += 1
            # Prefer the earliest capture-derived transmission generation.
            if (
                fragment.retransmission_generation,
                observation.capture_timestamp,
            ) < (
                previous.fragment.retransmission_generation,
                previous.capture_timestamp,
            ):
                self.fragments[fragment.fragment_index] = observation


@dataclass(frozen=True, slots=True)
class ReconstructedMediaFrame:
    packet_index: int
    capture_timestamp: float
    frame_id: int
    fragment_count: int
    frame_flags: tuple[int, ...]
    declared_length: int
    metadata_raw: bytes
    timestamp_ms_candidate: int
    bitstream: bytes
    missing_fragments: tuple[int, ...]
    duplicate_count: int
    conflicting_duplicate_count: int
    retransmission_generations: dict[int, int]

    @property
    def complete(self) -> bool:
        return not self.missing_fragments

    @property
    def length_valid(self) -> bool:
        return self.complete and len(self.bitstream) == self.declared_length

    @property
    def nal_types(self) -> list[int]:
        return annex_b_nal_types(self.bitstream) if self.length_valid else []

    def to_record(self) -> dict:
        return {
            "packet_index": self.packet_index,
            "capture_timestamp_unix": self.capture_timestamp,
            "frame_id": self.frame_id,
            "fragment_count": self.fragment_count,
            "frame_flags": list(self.frame_flags),
            "declared_length": self.declared_length,
            "reconstructed_length": len(self.bitstream),
            "metadata_raw_hex": self.metadata_raw.hex(),
            "timestamp_ms_candidate": self.timestamp_ms_candidate,
            "timestamp_semantic": "capture-inferred milliseconds",
            "complete": self.complete,
            "length_valid": self.length_valid,
            "missing_fragments": list(self.missing_fragments),
            "duplicate_count": self.duplicate_count,
            "conflicting_duplicate_count": self.conflicting_duplicate_count,
            "retransmission_generations": {
                str(key): value
                for key, value in sorted(self.retransmission_generations.items())
            },
            "nal_types": self.nal_types,
            "bitstream_sha256": hashlib.sha256(self.bitstream).hexdigest(),
        }


class DjiWifiMediaCollector:
    """Collect fragments across reordering and delayed byte-identical retries."""

    def __init__(self, *, orphan_window_seconds: float = 2.0) -> None:
        if orphan_window_seconds <= 0:
            raise ValueError("orphan window must be positive")
        self.orphan_window_seconds = orphan_window_seconds
        self._active: dict[int, _Assembly] = {}
        self._pending: list[MediaFragmentObservation] = []
        self._completed: list[ReconstructedMediaFrame] = []
        self.orphan_fragment_count = 0
        self.bogus_start_fragment_count = 0

    def feed(self, observation: MediaFragmentObservation) -> None:
        fragment = observation.fragment
        self._expire_pending(observation.capture_timestamp)
        if fragment.is_access_unit_start:
            header = DjiWifiMediaAccessUnitHeader.parse(fragment.payload)
            current = self._active.get(fragment.frame_id)
            if current is not None:
                if (
                    current.start.fragment.fragment_count == fragment.fragment_count
                    and current.start.fragment.payload == fragment.payload
                    and abs(
                        observation.capture_timestamp
                        - current.start.capture_timestamp
                    )
                    <= self.orphan_window_seconds
                ):
                    current.add(observation)
                    return
                self._completed.append(self._finalize(current))
            assembly = _Assembly(observation, header)
            assembly.add(observation)
            self._active[fragment.frame_id] = assembly
            retained = []
            for pending in self._pending:
                candidate = pending.fragment
                if (
                    candidate.frame_id == fragment.frame_id
                    and candidate.fragment_count == fragment.fragment_count
                    and candidate.fragment_index != 0
                    and abs(pending.capture_timestamp - observation.capture_timestamp)
                    <= self.orphan_window_seconds
                ):
                    assembly.add(pending)
                else:
                    retained.append(pending)
            self._pending = retained
            return

        assembly = self._active.get(fragment.frame_id)
        if (
            assembly is not None
            and assembly.start.fragment.fragment_count == fragment.fragment_count
            and abs(observation.capture_timestamp - assembly.start.capture_timestamp)
            <= self.orphan_window_seconds
        ):
            assembly.add(observation)
            return
        if fragment.fragment_index == 0:
            self.bogus_start_fragment_count += 1
        self._pending.append(observation)

    def finish(self) -> list[ReconstructedMediaFrame]:
        for assembly in self._active.values():
            self._completed.append(self._finalize(assembly))
        self._active.clear()
        self.orphan_fragment_count += len(self._pending)
        self._pending.clear()
        return sorted(self._completed, key=lambda item: item.packet_index)

    def _expire_pending(self, now: float) -> None:
        retained = []
        for pending in self._pending:
            if now - pending.capture_timestamp > self.orphan_window_seconds:
                self.orphan_fragment_count += 1
            else:
                retained.append(pending)
        self._pending = retained

    @staticmethod
    def _finalize(assembly: _Assembly) -> ReconstructedMediaFrame:
        start_fragment = assembly.start.fragment
        missing = tuple(
            index
            for index in range(start_fragment.fragment_count)
            if index not in assembly.fragments
        )
        joined = b"".join(
            assembly.fragments[index].fragment.payload
            for index in range(start_fragment.fragment_count)
            if index in assembly.fragments
        )
        bitstream = joined[DJI_WIFI_MEDIA_ACCESS_UNIT_HEADER_LENGTH:]
        return ReconstructedMediaFrame(
            packet_index=assembly.start.packet_index,
            capture_timestamp=assembly.start.capture_timestamp,
            frame_id=start_fragment.frame_id,
            fragment_count=start_fragment.fragment_count,
            frame_flags=tuple(
                sorted({item.fragment.frame_flags for item in assembly.fragments.values()})
            ),
            declared_length=assembly.header.declared_length,
            metadata_raw=assembly.header.metadata_raw,
            timestamp_ms_candidate=assembly.header.timestamp_ms_candidate,
            bitstream=bitstream,
            missing_fragments=missing,
            duplicate_count=assembly.duplicate_count,
            conflicting_duplicate_count=assembly.conflicting_duplicate_count,
            retransmission_generations=dict(assembly.retransmission_generations),
        )


def _select_media_flow(datagrams: Iterable[UdpDatagram]) -> tuple[str, int, str, int]:
    counts: Counter[tuple[str, int, str, int]] = Counter()
    for datagram in datagrams:
        if len(datagram.payload) < 8:
            continue
        try:
            basic = DjiWifiBasicHeader.parse(datagram.payload)
        except DjiWifiEnvelopeError:
            continue
        if basic.wh_type == DJI_WIFI_MEDIA_TYPE:
            counts[datagram.flow] += 1
    if not counts:
        raise ValueError("PCAP contains no DJI Wi-Fi WhType 02 media flow")
    return counts.most_common(1)[0][0]


def reconstruct_media_from_pcap(path: Path) -> tuple[list[ReconstructedMediaFrame], dict]:
    datagrams, pcap_metadata = _read_udp(path)
    selected_flow = _select_media_flow(datagrams)
    collector = DjiWifiMediaCollector()
    parse_errors: Counter[str] = Counter()
    media_datagrams = 0
    for datagram in datagrams:
        if datagram.flow != selected_flow:
            continue
        try:
            basic = DjiWifiBasicHeader.parse(datagram.payload)
        except DjiWifiEnvelopeError:
            continue
        if basic.wh_type != DJI_WIFI_MEDIA_TYPE:
            continue
        media_datagrams += 1
        try:
            fragment = DjiWifiMediaFragment.parse(datagram.payload)
        except DjiWifiMediaError as exc:
            parse_errors[str(exc)] += 1
            continue
        collector.feed(
            MediaFragmentObservation(datagram.index, datagram.timestamp, fragment)
        )
    frames = collector.finish()
    return frames, {
        **pcap_metadata,
        "udp_datagram_count": len(datagrams),
        "selected_media_flow": {
            "source": selected_flow[0],
            "source_port": selected_flow[1],
            "destination": selected_flow[2],
            "destination_port": selected_flow[3],
        },
        "media_datagram_count": media_datagrams,
        "media_parse_errors": dict(parse_errors),
        "orphan_fragment_count": collector.orphan_fragment_count,
        "bogus_start_fragment_count": collector.bogus_start_fragment_count,
    }


def extract_dji_wifi_media(path: Path, output_dir: Path) -> dict:
    """Extract exact complete Annex-B access units without changing the PCAP."""

    source = path.resolve()
    if not source.is_file():
        raise FileNotFoundError(source)
    output = require_private_directory(output_dir)
    source_hash_before = _sha256(source)
    frames, metadata = reconstruct_media_from_pcap(source)
    exact_frames = [frame for frame in frames if frame.length_valid]
    bitstream_path = output / "video.h264"
    with bitstream_path.open("wb") as stream:
        for frame in exact_frames:
            stream.write(frame.bitstream)
    records_path = output / "frames.jsonl"
    with records_path.open("w", encoding="utf-8", newline="\n") as stream:
        for frame in frames:
            stream.write(json.dumps(frame.to_record(), sort_keys=True) + "\n")
    source_hash_after = _sha256(source)
    if source_hash_before != source_hash_after:
        raise RuntimeError("source PCAP changed during immutable analysis")
    nal_counts: Counter[int] = Counter()
    for frame in exact_frames:
        nal_counts.update(frame.nal_types)
    timestamp_deltas = [
        (after.timestamp_ms_candidate - before.timestamp_ms_candidate) & 0xFFFFFFFF
        for before, after in zip(exact_frames, exact_frames[1:])
    ]
    plausible_deltas = [value for value in timestamp_deltas if value < 10_000]
    summary = {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "source_file": source.name,
        "source_sha256_before": source_hash_before,
        "source_sha256_after": source_hash_after,
        "source_immutable": source_hash_before == source_hash_after,
        **metadata,
        "access_unit_start_count": len(frames),
        "exact_access_unit_count": len(exact_frames),
        "incomplete_access_unit_count": sum(not frame.complete for frame in frames),
        "length_mismatch_access_unit_count": sum(
            frame.complete and not frame.length_valid for frame in frames
        ),
        "dropped_access_unit_count": len(frames) - len(exact_frames),
        "duplicate_fragment_count": sum(frame.duplicate_count for frame in frames),
        "conflicting_duplicate_fragment_count": sum(
            frame.conflicting_duplicate_count for frame in frames
        ),
        "annex_b_nal_type_counts": {
            str(key): value for key, value in sorted(nal_counts.items())
        },
        "timestamp_ms_candidate": {
            "semantic": "capture-inferred milliseconds, not protocol-confirmed",
            "median_positive_delta": (
                sorted(value for value in plausible_deltas if value > 0)[
                    len([value for value in plausible_deltas if value > 0]) // 2
                ]
                if any(value > 0 for value in plausible_deltas)
                else None
            ),
        },
        "elementary_stream": {
            "path": bitstream_path.name,
            "bytes": bitstream_path.stat().st_size,
            "sha256": _sha256(bitstream_path),
            "format": "H.264 Annex B byte-stream",
        },
    }
    summary_path = output / "summary.json"
    summary_path.write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
    report_path = output / "report.md"
    report_path.write_text(
        "# DJI Wi-Fi media extraction\n\n"
        f"- Source SHA-256 unchanged: `{summary['source_immutable']}`\n"
        f"- WhType 02 datagrams: `{summary['media_datagram_count']}`\n"
        f"- Exact access units: `{summary['exact_access_unit_count']}`\n"
        f"- Dropped incomplete/mismatched units: `{summary['dropped_access_unit_count']}`\n"
        f"- Byte-identical retransmission fragments: `{summary['duplicate_fragment_count']}`\n"
        f"- Output: H.264 Annex B, `{summary['elementary_stream']['bytes']}` bytes\n\n"
        "The four-byte timestamp field is only a capture-derived millisecond candidate. "
        "Unknown metadata and frame flags remain uninterpreted.\n",
        encoding="utf-8",
    )
    manifest_paths = [bitstream_path, records_path, summary_path, report_path]
    manifest_path = output / "checksums.sha256"
    manifest_path.write_text(
        "".join(
            f"{_sha256(item)}  {item.name}\n" for item in manifest_paths
        ),
        encoding="ascii",
        newline="\n",
    )
    return summary
