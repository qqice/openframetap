from __future__ import annotations

from pathlib import Path
import socket
import struct

import pytest

from openframetap.analysis.dji_wifi_media import (
    DjiWifiMediaCollector,
    MediaFragmentObservation,
    annex_b_nal_types,
    extract_dji_wifi_media,
)
from openframetap.protocol.dji_wifi import DjiWifiBasicHeader
from openframetap.protocol.dji_wifi_media import (
    DjiWifiMediaError,
    DjiWifiMediaFragment,
)


POCKET = "192.168.2.1"
PHONE = "192.168.2.224"


def _fragment(
    *,
    frame_id: int,
    count: int,
    index: int,
    payload: bytes,
    sequence: int,
    flags: int = 0,
) -> bytes:
    total_length = 20 + len(payload)
    basic = DjiWifiBasicHeader(
        total_length=total_length,
        format_nibble=8,
        session_id=7,
        transport_sequence=sequence,
        wh_type=2,
        checksum=0,
        checksum_valid=True,
    ).encode()
    split_count = count | ((index & 1) << 7)
    split_index = flags | (index // 2)
    return basic + bytes(8) + bytes((frame_id, split_count, split_index, 0)) + payload


def _unit_payload(bitstream: bytes, *, timestamp: int = 1000) -> bytes:
    return (
        b"\x00\x00\x01\xff"
        + len(bitstream).to_bytes(4, "little")
        + bytes.fromhex("90115700")
        + timestamp.to_bytes(4, "little")
        + bitstream
    )


def _observe(raw: bytes, packet: int, timestamp: float):
    return MediaFragmentObservation(packet, timestamp, DjiWifiMediaFragment.parse(raw))


def _udp(payload: bytes) -> bytes:
    udp = struct.pack("!HHHH", 9004, 54232, len(payload) + 8, 0) + payload
    ip = bytearray(20)
    ip[0] = 0x45
    struct.pack_into("!H", ip, 2, len(ip) + len(udp))
    ip[8] = 64
    ip[9] = 17
    ip[12:16] = socket.inet_aton(POCKET)
    ip[16:20] = socket.inet_aton(PHONE)
    return bytes(ip) + udp


def _pcap(path: Path, records: list[tuple[float, bytes]]) -> None:
    raw = bytearray(struct.pack("<IHHIIII", 0xA1B2C3D4, 2, 4, 0, 0, 65535, 101))
    for timestamp, packet in records:
        seconds = int(timestamp)
        micros = round((timestamp - seconds) * 1_000_000)
        raw.extend(struct.pack("<IIII", seconds, micros, len(packet), len(packet)))
        raw.extend(packet)
    path.write_bytes(raw)


def test_fragment_split_index_and_retransmission_generation() -> None:
    fragment = DjiWifiMediaFragment.parse(
        _fragment(
            frame_id=9,
            count=19,
            index=17,
            payload=b"data",
            sequence=0x100A,
            flags=0x60,
        )
    )
    assert fragment.frame_id == 9
    assert fragment.fragment_count == 19
    assert fragment.fragment_index == 17
    assert fragment.frame_flags == 0x60
    assert fragment.retransmission_generation == 2
    with pytest.raises(DjiWifiMediaError, match="index"):
        DjiWifiMediaFragment.parse(
            _fragment(
                frame_id=1,
                count=2,
                index=3,
                payload=b"x",
                sequence=0,
            )
        )


def test_collector_repairs_out_of_order_and_byte_identical_retransmission() -> None:
    bitstream = b"\x00\x00\x00\x01\x65" + b"picture"
    raw = _unit_payload(bitstream)
    first, second = raw[:20], raw[20:]
    collector = DjiWifiMediaCollector()
    collector.feed(
        _observe(
            _fragment(frame_id=255, count=2, index=1, payload=second, sequence=8),
            1,
            1.00,
        )
    )
    collector.feed(
        _observe(
            _fragment(frame_id=255, count=2, index=0, payload=first, sequence=16),
            2,
            1.01,
        )
    )
    collector.feed(
        _observe(
            _fragment(frame_id=255, count=2, index=1, payload=second, sequence=10),
            3,
            1.02,
        )
    )
    # Frame identifiers wrap; this must not merge frame 0 with frame 255.
    next_raw = _unit_payload(b"\x00\x00\x00\x01\x61next", timestamp=1033)
    collector.feed(
        _observe(
            _fragment(frame_id=0, count=1, index=0, payload=next_raw, sequence=24),
            4,
            1.04,
        )
    )
    frames = collector.finish()
    assert [frame.frame_id for frame in frames] == [255, 0]
    assert frames[0].length_valid
    assert frames[0].bitstream == bitstream
    assert frames[0].duplicate_count == 1
    assert frames[0].conflicting_duplicate_count == 0
    assert frames[0].nal_types == [5]


def test_same_frame_id_and_payload_after_window_is_a_new_wrapped_frame() -> None:
    raw = _unit_payload(b"\x00\x00\x00\x01\x67config")
    collector = DjiWifiMediaCollector(orphan_window_seconds=2.0)
    collector.feed(
        _observe(
            _fragment(frame_id=7, count=1, index=0, payload=raw, sequence=8),
            1,
            1.0,
        )
    )
    collector.feed(
        _observe(
            _fragment(frame_id=7, count=1, index=0, payload=raw, sequence=16),
            2,
            9.5,
        )
    )
    frames = collector.finish()
    assert len(frames) == 2
    assert all(frame.length_valid for frame in frames)


def test_collector_preserves_missing_and_length_mismatch_fail_closed() -> None:
    collector = DjiWifiMediaCollector()
    raw = _unit_payload(b"\x00\x00\x00\x01\x61payload")
    collector.feed(
        _observe(
            _fragment(frame_id=1, count=2, index=0, payload=raw[:18], sequence=8),
            1,
            1.0,
        )
    )
    mismatch = bytearray(_unit_payload(b"\x00\x00\x00\x01\x61ok"))
    mismatch[4:8] = (99).to_bytes(4, "little")
    collector.feed(
        _observe(
            _fragment(frame_id=2, count=1, index=0, payload=bytes(mismatch), sequence=16),
            2,
            1.1,
        )
    )
    frames = collector.finish()
    assert frames[0].missing_fragments == (1,)
    assert not frames[0].length_valid
    assert frames[1].complete
    assert not frames[1].length_valid


def test_annex_b_parser_handles_three_and_four_byte_start_codes() -> None:
    assert annex_b_nal_types(
        b"\x00\x00\x00\x01\x67abc\x00\x00\x01\x68def\x00\x00\x01\x65x"
    ) == [7, 8, 5]


def test_pcap_extraction_keeps_source_immutable_and_drops_bad_units(tmp_path: Path) -> None:
    good = _unit_payload(b"\x00\x00\x00\x01\x67sps\x00\x00\x01\x65idr")
    bad = bytearray(_unit_payload(b"\x00\x00\x01\x61bad", timestamp=1033))
    bad[4:8] = (100).to_bytes(4, "little")
    capture = tmp_path / "capture.pcap"
    _pcap(
        capture,
        [
            (1.0, _udp(_fragment(frame_id=1, count=1, index=0, payload=good, sequence=8))),
            (1.1, _udp(_fragment(frame_id=2, count=1, index=0, payload=bytes(bad), sequence=16))),
        ],
    )
    before = capture.read_bytes()
    output = tmp_path / "artifacts" / "private" / "extract"
    result = extract_dji_wifi_media(capture, output)
    assert capture.read_bytes() == before
    assert result["source_immutable"]
    assert result["exact_access_unit_count"] == 1
    assert result["length_mismatch_access_unit_count"] == 1
    assert (output / "video.h264").read_bytes() == good[16:]
    assert (output / "checksums.sha256").is_file()
