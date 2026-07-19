from __future__ import annotations

from pathlib import Path
import socket
import struct

import pytest

from openframetap.analysis.mimo_wifi import analyze_mimo_wifi_gimbal
from openframetap.protocol.duml import encode_duml_frame


PHONE = "192.168.2.224"
POCKET = "192.168.2.1"


def _udp(source: str, source_port: int, destination: str, destination_port: int, payload: bytes) -> bytes:
    udp = struct.pack("!HHHH", source_port, destination_port, len(payload) + 8, 0) + payload
    ip = bytearray(20)
    ip[0] = 0x45
    struct.pack_into("!H", ip, 2, len(ip) + len(udp))
    ip[8] = 64
    ip[9] = 17
    ip[12:16] = socket.inet_aton(source)
    ip[16:20] = socket.inet_aton(destination)
    return bytes(ip) + udp


def _pcap(path: Path, records: list[tuple[float, bytes]]) -> None:
    raw = bytearray(struct.pack("<IHHIIII", 0xA1B2C3D4, 2, 4, 0, 0, 65535, 101))
    for timestamp, packet in records:
        seconds = int(timestamp)
        micros = round((timestamp - seconds) * 1_000_000)
        raw.extend(struct.pack("<IIII", seconds, micros, len(packet), len(packet)))
        raw.extend(packet)
    path.write_bytes(raw)


def _frame(sequence: int, payload: bytes, *, command: int = 1) -> bytes:
    return encode_duml_frame(
        sender=2,
        receiver=4,
        sequence=sequence,
        flags=0,
        cmd_set=4,
        cmd_id=command,
        payload=payload,
    )


def test_extracts_four_mimo_pwm_action_groups_without_authorizing_them(tmp_path: Path) -> None:
    neutral = struct.pack("<5H", 1024, 0, 1024, 0x8000, 0x42)
    records = []
    sequence = 1
    for start, payload in (
        (1.0, struct.pack("<5H", 1024, 0, 1250, 0x8000, 0x42)),
        (3.0, struct.pack("<5H", 1024, 0, 800, 0x8000, 0x42)),
        (5.0, struct.pack("<5H", 1250, 0, 1024, 0x8000, 0x42)),
        (7.0, struct.pack("<5H", 800, 0, 1024, 0x8000, 0x42)),
    ):
        for offset, value in ((0.0, neutral), (0.1, payload), (0.2, neutral)):
            wrapped = bytes(20) + _frame(sequence, value)
            records.append((start + offset, _udp(PHONE, 54232, POCKET, 9004, wrapped)))
            sequence += 1
    path = tmp_path / "mimo.pcap"
    _pcap(path, records)
    result = analyze_mimo_wifi_gimbal(path)
    assert result["selected_flow"]["pocket_ip"] == POCKET
    assert result["gimbal_control"]["frame_count"] == 12
    assert result["gimbal_control"]["duml_offsets"] == {20: 12}
    assert [item["label"] for item in result["gimbal_control"]["action_groups"]] == [
        "yaw_right", "yaw_left", "pitch_up", "pitch_down"
    ]
    assert result["gimbal_control"]["action_groups"][0]["uint16_le_fields"][2]["maximum"] == 1250


def test_rejects_non_pcap_and_pcap_without_control(tmp_path: Path) -> None:
    bad = tmp_path / "bad.pcap"
    bad.write_bytes(b"not pcap")
    with pytest.raises(ValueError, match="PCAP"):
        analyze_mimo_wifi_gimbal(bad)

    empty = tmp_path / "empty.pcap"
    _pcap(empty, [(1.0, _udp(PHONE, 1, POCKET, 2, b"nothing"))])
    with pytest.raises(ValueError, match="04/01"):
        analyze_mimo_wifi_gimbal(empty)
