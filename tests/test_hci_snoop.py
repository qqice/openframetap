from __future__ import annotations

from pathlib import Path
import struct

from openframetap.analysis.hci_snoop import analyze_hci_gimbal
from openframetap.protocol.gimbal_commands import build_speed_frame


def _record(packet: bytes, *, flags: int, timestamp: int) -> bytes:
    return struct.pack(">IIIIQ", len(packet), len(packet), flags, 0, timestamp) + packet


def _acl_att(opcode: int, handle: int, value: bytes) -> bytes:
    att = bytes((opcode,)) + struct.pack("<H", handle) + value
    l2cap = struct.pack("<HH", len(att), 4) + att
    return b"\x02" + struct.pack("<HH", 0x2001, len(l2cap)) + l2cap


def test_extracts_outgoing_gimbal_duml_from_hci_uart_snoop(tmp_path: Path) -> None:
    frame = build_speed_frame(sequence=7, yaw=0.05, pitch=0.0, live=True)
    raw = b"btsnoop\0" + struct.pack(">II", 1, 1002)
    raw += _record(_acl_att(0x52, 0x002F, frame), flags=0, timestamp=1)
    raw += _record(_acl_att(0x1B, 0x002C, frame), flags=1, timestamp=2)
    path = tmp_path / "fixture.btsnoop"
    path.write_bytes(raw)
    result = analyze_hci_gimbal(path)
    assert result["att_record_count"] == 2
    assert result["duml_frame_count"] == 2
    assert result["gimbal_write_count"] == 1
    assert result["gimbal_writes"][0]["payload_hex"] == "000000000f0001"
    assert result["reassembly_error_count"] == 0


def test_extracts_btmon_linux_monitor_acl_records(tmp_path: Path) -> None:
    frame = build_speed_frame(sequence=8, yaw=-0.05, pitch=0.0, live=True)
    raw = b"btsnoop\0" + struct.pack(">II", 1, 2001)
    raw += _record(_acl_att(0x52, 0x0030, frame)[1:], flags=4, timestamp=3)
    path = tmp_path / "btmon.btsnoop"
    path.write_bytes(raw)
    result = analyze_hci_gimbal(path)
    assert result["btsnoop_datalink"] == 2001
    assert result["gimbal_write_count"] == 1
    assert result["gimbal_writes"][0]["payload_hex"] == "00000000f1ff01"


def test_rejects_non_btsnoop_file(tmp_path: Path) -> None:
    path = tmp_path / "bad.btsnoop"
    path.write_bytes(b"not snoop")
    try:
        analyze_hci_gimbal(path)
    except ValueError as exc:
        assert "BTSnoop" in str(exc)
    else:
        raise AssertionError("invalid file was accepted")
