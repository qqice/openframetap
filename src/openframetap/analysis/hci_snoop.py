"""Minimal immutable BTSnoop -> ATT -> DUML extractor.

The parser intentionally covers only HCI UART (DLT 1002), ACL/L2CAP and ATT.
It is sufficient for BlueZ btmon captures and Android Bluetooth HCI snoops
without requiring Wireshark or modifying the source capture.
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
import struct

from openframetap.protocol.reassembly import DumlStreamReassembler


BTSNOOP_MAGIC = b"btsnoop\0"
HCI_UART_DATALINK = 1002
LINUX_MONITOR_DATALINK = 2001
ATT_CID = 0x0004


@dataclass(frozen=True, slots=True)
class SnoopRecord:
    index: int
    flags: int
    timestamp: int
    packet: bytes


def read_btsnoop(path: Path) -> tuple[list[SnoopRecord], str, int]:
    raw = path.read_bytes()
    digest = hashlib.sha256(raw).hexdigest()
    if len(raw) < 16 or raw[:8] != BTSNOOP_MAGIC:
        raise ValueError("not a BTSnoop file")
    version, datalink = struct.unpack_from(">II", raw, 8)
    if version != 1 or datalink not in {HCI_UART_DATALINK, LINUX_MONITOR_DATALINK}:
        raise ValueError(f"unsupported BTSnoop version/datalink: {version}/{datalink}")
    records: list[SnoopRecord] = []
    offset = 16
    while offset < len(raw):
        if len(raw) - offset < 24:
            raise ValueError("truncated BTSnoop record header")
        original, included, flags, _drops, timestamp = struct.unpack_from(
            ">IIIIQ", raw, offset
        )
        offset += 24
        if included > original or included > len(raw) - offset:
            raise ValueError("invalid or truncated BTSnoop record payload")
        packet = raw[offset : offset + included]
        offset += included
        records.append(SnoopRecord(len(records), flags, timestamp, packet))
    return records, digest, datalink


def _att_records(records: list[SnoopRecord], *, datalink: int) -> list[dict]:
    output: list[dict] = []
    # Current captures fit one L2CAP PDU per ACL packet. Continuation packets are
    # retained as unsupported rather than guessed; DUML has its own reassembly.
    for record in records:
        packet = record.packet
        if datalink == LINUX_MONITOR_DATALINK:
            # btmon stores ACL TX/RX as monitor opcodes 4/5 and omits H4 type.
            if record.flags not in {4, 5}:
                continue
            packet = b"\x02" + packet
        if len(packet) < 9 or packet[0] != 0x02:  # H4 ACL data
            continue
        handle_flags, acl_length = struct.unpack_from("<HH", packet, 1)
        acl = packet[5 : 5 + acl_length]
        if len(acl) != acl_length or len(acl) < 5:
            continue
        pb_flag = (handle_flags >> 12) & 0x03
        if pb_flag == 0x01:  # continuation fragment
            continue
        l2cap_length, cid = struct.unpack_from("<HH", acl, 0)
        if cid != ATT_CID or len(acl) < 4 + l2cap_length or l2cap_length < 1:
            continue
        att = acl[4 : 4 + l2cap_length]
        opcode = att[0]
        kind = {
            0x12: "write_request",
            0x13: "write_response",
            0x1B: "notification",
            0x1D: "indication",
            0x1E: "confirmation",
            0x52: "write_command",
        }.get(opcode, "other")
        handle = struct.unpack_from("<H", att, 1)[0] if len(att) >= 3 and opcode in {0x12, 0x1B, 0x1D, 0x52} else None
        value = att[3:] if handle is not None else b""
        output.append(
            {
                "record_index": record.index,
                "timestamp": record.timestamp,
                "record_flags": record.flags,
                "acl_handle": handle_flags & 0x0FFF,
                "att_opcode": opcode,
                "att_kind": kind,
                "attribute_handle": handle,
                "value_hex": value.hex(),
                "value": value,
            }
        )
    return output


def analyze_hci_gimbal(path: Path) -> dict:
    records, digest, datalink = read_btsnoop(path)
    att = _att_records(records, datalink=datalink)
    directions = {
        "app_to_device": DumlStreamReassembler(),
        "device_to_app": DumlStreamReassembler(),
    }
    frames: list[dict] = []
    for item in att:
        if item["att_kind"] in {"write_request", "write_command"}:
            direction = "app_to_device"
        elif item["att_kind"] in {"notification", "indication"}:
            direction = "device_to_app"
        else:
            continue
        value = item["value"]
        if not value:
            continue
        for event in directions[direction].feed(value):
            if event.kind == "frame" and event.frame is not None:
                frame = event.frame
                frames.append(
                    {
                        "direction": direction,
                        "record_index": item["record_index"],
                        "timestamp": item["timestamp"],
                        "attribute_handle": item["attribute_handle"],
                        "cmd_set": frame.cmd_set,
                        "cmd_id": frame.cmd_id,
                        "sequence": frame.sequence,
                        "flags": frame.flags,
                        "payload_hex": frame.payload.hex(),
                        "raw_hex": frame.raw.hex(),
                        "crc8_valid": frame.crc8_valid,
                        "crc16_valid": frame.crc16_valid,
                    }
                )
    errors = []
    for direction, reassembler in directions.items():
        for event in reassembler.finish():
            if event.kind != "frame":
                errors.append(
                    {"direction": direction, "reason": event.reason, "raw_hex": event.raw.hex()}
                )
    counts: dict[str, int] = {}
    for frame in frames:
        key = f"{frame['direction']}:{frame['cmd_set']:02X}/{frame['cmd_id']:02X}"
        counts[key] = counts.get(key, 0) + 1
    gimbal_writes = [
        frame
        for frame in frames
        if frame["direction"] == "app_to_device" and frame["cmd_set"] == 0x04
    ]
    return {
        "source_file": path.name,
        "source_sha256": digest,
        "btsnoop_datalink": datalink,
        "btsnoop_record_count": len(records),
        "att_record_count": len(att),
        "duml_frame_count": len(frames),
        "reassembly_error_count": len(errors),
        "command_counts": counts,
        "gimbal_write_count": len(gimbal_writes),
        "gimbal_writes": gimbal_writes,
        "frames": frames,
        "reassembly_errors": errors,
    }


def write_hci_analysis(source: Path, output: Path) -> dict:
    result = analyze_hci_gimbal(source)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    return result
