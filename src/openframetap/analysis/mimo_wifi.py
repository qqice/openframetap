"""Immutable PCAP/DLT_RAW analysis for DJI Mimo's Pocket 3 Wi-Fi session."""

from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
import socket
import statistics
import struct

from openframetap.protocol.reassembly import DumlStreamReassembler
from openframetap.protocol.dji_wifi import (
    DjiWifiBasicHeader,
    DjiWifiEnvelope,
    DjiWifiEnvelopeError,
    DjiWifiFlowStatus,
)


DLT_RAW = 101
DLT_EN10MB = 1
DLT_LINUX_SLL2 = 276
_PCAP_MAGICS = {
    b"\xd4\xc3\xb2\xa1": ("<", 1_000_000),
    b"\xa1\xb2\xc3\xd4": (">", 1_000_000),
    b"\x4d\x3c\xb2\xa1": ("<", 1_000_000_000),
    b"\xa1\xb2\x3c\x4d": (">", 1_000_000_000),
}


@dataclass(frozen=True, slots=True)
class UdpDatagram:
    index: int
    timestamp: float
    source: str
    source_port: int
    destination: str
    destination_port: int
    payload: bytes

    @property
    def flow(self) -> tuple[str, int, str, int]:
        return self.source, self.source_port, self.destination, self.destination_port


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def _read_udp(path: Path) -> tuple[list[UdpDatagram], dict]:
    datagrams: list[UdpDatagram] = []
    packet_count = 0
    first: float | None = None
    last: float | None = None
    with path.open("rb") as stream:
        header = stream.read(24)
        if len(header) != 24 or header[:4] not in _PCAP_MAGICS:
            raise ValueError("not a supported classic PCAP file")
        endian, timestamp_scale = _PCAP_MAGICS[header[:4]]
        _magic, major, minor, _zone, _sigfigs, _snaplen, link_type = struct.unpack(
            endian + "IHHIIII", header
        )
        if (major, minor) != (2, 4) or link_type not in {DLT_RAW, DLT_LINUX_SLL2, DLT_EN10MB}:
            raise ValueError(f"unsupported PCAP version/link type: {major}.{minor}/{link_type}")
        while record_header := stream.read(16):
            if len(record_header) != 16:
                raise ValueError("truncated PCAP record header")
            seconds, fraction, included, original = struct.unpack(
                endian + "IIII", record_header
            )
            if included > original:
                raise ValueError("PCAP included length exceeds original length")
            packet = stream.read(included)
            if len(packet) != included:
                raise ValueError("truncated PCAP record payload")
            timestamp = seconds + fraction / timestamp_scale
            first = timestamp if first is None else min(first, timestamp)
            last = timestamp if last is None else max(last, timestamp)
            index = packet_count
            packet_count += 1
            if link_type == DLT_EN10MB:
                if len(packet) < 14:
                    continue
                ethertype = int.from_bytes(packet[12:14], 'big')
                offset = 14
                while ethertype in (0x8100, 0x88A8) and offset + 4 <= len(packet):
                    ethertype = int.from_bytes(packet[offset+2:offset+4], 'big')
                    offset += 4
                if ethertype != 0x0800:
                    continue
                packet = packet[offset:]
            if link_type == DLT_LINUX_SLL2:
                if len(packet) < 20 or packet[0:2] != b"\x08\x00":
                    continue
                packet = packet[20:]
            if len(packet) < 28 or packet[0] >> 4 != 4:
                continue
            if int.from_bytes(packet[6:8], 'big') & 0x3FFF:
                continue  # IP fragment reassembly is outside this UDP reader.
            ihl = (packet[0] & 0x0F) * 4
            if ihl < 20 or len(packet) < ihl + 8 or packet[9] != 17:
                continue
            source = socket.inet_ntoa(packet[12:16])
            destination = socket.inet_ntoa(packet[16:20])
            source_port, destination_port, udp_length = struct.unpack_from(
                "!HHH", packet, ihl
            )
            if udp_length < 8 or len(packet) < ihl + udp_length:
                continue
            datagrams.append(
                UdpDatagram(
                    index,
                    timestamp,
                    source,
                    source_port,
                    destination,
                    destination_port,
                    packet[ihl + 8 : ihl + udp_length],
                )
            )
    if first is None or last is None:
        raise ValueError("PCAP contains no records")
    return datagrams, {
        "pcap_packet_count": packet_count,
        "capture_start_unix": first,
        "capture_end_unix": last,
        "capture_duration_seconds": last - first,
        "link_type": link_type,
    }


def _frames(datagram: UdpDatagram) -> list[dict]:
    reassembler = DumlStreamReassembler()
    output = []
    for event in reassembler.feed(datagram.payload) + reassembler.finish():
        if event.kind != "frame" or event.frame is None:
            continue
        frame = event.frame
        if not frame.crc8_valid or not frame.crc16_valid:
            continue
        output.append(
            {
                "datagram": datagram,
                "offset": datagram.payload.find(frame.raw),
                "frame": frame,
            }
        )
    return output


def _split_actions(records: list[dict], *, gap_seconds: float = 0.5) -> list[list[dict]]:
    if not records:
        return []
    groups = [[records[0]]]
    for record in records[1:]:
        if record["datagram"].timestamp - groups[-1][-1]["datagram"].timestamp > gap_seconds:
            groups.append([])
        groups[-1].append(record)
    return groups


def _median(records: list[tuple], index: int) -> float | None:
    return statistics.median(item[index] for item in records) if records else None


def _wrapped_delta(after: float | None, before: float | None, period: int) -> float | None:
    if after is None or before is None:
        return None
    return (after - before + period / 2) % period - period / 2


def analyze_mimo_wifi_gimbal(
    path: Path,
    *,
    action_labels: tuple[str, ...] = ("yaw_right", "yaw_left", "pitch_up", "pitch_down"),
) -> dict:
    datagrams, metadata = _read_udp(path)
    parsed: list[dict] = []
    control_by_flow: dict[tuple[str, int, str, int], list[dict]] = defaultdict(list)
    for datagram in datagrams:
        for item in _frames(datagram):
            parsed.append(item)
            frame = item["frame"]
            if (
                frame.sender == 0x02
                and frame.receiver == 0x04
                and frame.cmd_set == 0x04
                and frame.cmd_id == 0x01
            ):
                control_by_flow[datagram.flow].append(item)
    if not control_by_flow:
        raise ValueError("no app-to-gimbal 04/01 flow found")
    upstream_flow, controls = max(control_by_flow.items(), key=lambda item: len(item[1]))
    reverse_flow = (
        upstream_flow[2], upstream_flow[3], upstream_flow[0], upstream_flow[1]
    )
    selected = [
        item
        for item in parsed
        if item["datagram"].flow in {upstream_flow, reverse_flow}
    ]
    selected.sort(key=lambda item: item["datagram"].timestamp)
    direction_counts: Counter[str] = Counter()
    for item in selected:
        direction = "app_to_pocket" if item["datagram"].flow == upstream_flow else "pocket_to_app"
        frame = item["frame"]
        direction_counts[f"{direction}:{frame.cmd_set:02X}/{frame.cmd_id:02X}"] += 1

    groups = _split_actions(controls)
    action_summaries = []
    telemetry = []
    for item in selected:
        frame = item["frame"]
        if item["datagram"].flow != reverse_flow or (frame.cmd_set, frame.cmd_id) != (4, 5):
            continue
        payload = frame.payload
        if len(payload) >= 24:
            telemetry.append(
                (
                    item["datagram"].timestamp,
                    struct.unpack_from("<h", payload, 16)[0],
                    struct.unpack_from("<h", payload, 20)[0],
                    struct.unpack_from("<h", payload, 22)[0],
                )
            )

    neutral = bytes.fromhex("00040000000400804200")
    for index, group in enumerate(groups):
        label = action_labels[index] if index < len(action_labels) else f"action_{index + 1}"
        layouts = [struct.unpack("<5H", item["frame"].payload) for item in group if len(item["frame"].payload) == 10]
        if not layouts:
            continue
        axis_index = 2 if label.startswith("yaw") else 0
        active = [
            (item, values)
            for item, values in zip(group, layouts, strict=True)
            if abs(values[axis_index] - 1024) > 20
        ]
        active_start = active[0][0]["datagram"].timestamp if active else group[0]["datagram"].timestamp
        active_end = active[-1][0]["datagram"].timestamp if active else group[-1]["datagram"].timestamp
        pre = [row for row in telemetry if active_start - 1 <= row[0] < active_start]
        post = [row for row in telemetry if active_end < row[0] <= active_end + 1]
        before_yaw, after_yaw = _median(pre, 1), _median(post, 1)
        before_pitch, after_pitch = _median(pre, 2), _median(post, 2)
        action_summaries.append(
            {
                "label": label,
                "frame_count": len(group),
                "active_frame_count": len(active),
                "start_seconds": group[0]["datagram"].timestamp - metadata["capture_start_unix"],
                "end_seconds": group[-1]["datagram"].timestamp - metadata["capture_start_unix"],
                "active_duration_seconds": active_end - active_start,
                "send_rate_hz": (
                    (len(group) - 1)
                    / (group[-1]["datagram"].timestamp - group[0]["datagram"].timestamp)
                    if len(group) > 1
                    and group[-1]["datagram"].timestamp > group[0]["datagram"].timestamp
                    else None
                ),
                "exact_neutral_frame_count": sum(item["frame"].payload == neutral for item in group),
                "first_payload_hex": group[0]["frame"].payload.hex(),
                "last_payload_hex": group[-1]["frame"].payload.hex(),
                "uint16_le_fields": [
                    {
                        "offset": field * 2,
                        "minimum": min(values[field] for values in layouts),
                        "maximum": max(values[field] for values in layouts),
                        "median": statistics.median(values[field] for values in layouts),
                        "unique_count": len({values[field] for values in layouts}),
                    }
                    for field in range(5)
                ],
                "telemetry_before": {"yaw_offset16": before_yaw, "pitch_offset20": before_pitch},
                "telemetry_after": {"yaw_offset16": after_yaw, "pitch_offset20": after_pitch},
                "telemetry_delta": {
                    "yaw_offset16_wrapped_period36000": _wrapped_delta(after_yaw, before_yaw, 36000),
                    "pitch_offset20": None if after_pitch is None or before_pitch is None else after_pitch - before_pitch,
                },
            }
        )

    companion_up = [
        item for item in selected
        if item["datagram"].flow == upstream_flow
        and (item["frame"].cmd_set, item["frame"].cmd_id) == (4, 0x50)
    ]
    companion_down = [
        item for item in selected
        if item["datagram"].flow == reverse_flow
        and (item["frame"].cmd_set, item["frame"].cmd_id) == (4, 0x50)
    ]
    return {
        "source_file": path.name,
        "source_sha256": _sha256(path),
        **metadata,
        "udp_datagram_count": len(datagrams),
        "selected_flow": {
            "phone_ip": upstream_flow[0],
            "phone_port": upstream_flow[1],
            "pocket_ip": upstream_flow[2],
            "pocket_port": upstream_flow[3],
        },
        "selected_duml_frame_count": len(selected),
        "command_counts": dict(sorted(direction_counts.items())),
        "gimbal_control": {
            "cmd_set": 4,
            "cmd_id": 1,
            "sender": 2,
            "receiver": 4,
            "flags": sorted({item["frame"].flags for item in controls}),
            "frame_count": len(controls),
            "payload_lengths": dict(Counter(len(item["frame"].payload) for item in controls)),
            "duml_offsets": dict(Counter(item["offset"] for item in controls)),
            "neutral_payload_hex": neutral.hex(),
            "neutral_payload_count": sum(item["frame"].payload == neutral for item in controls),
            "action_groups": action_summaries,
        },
        "companion_04_50": {
            "request_count": len(companion_up),
            "response_count": len(companion_down),
            "request_payloads": sorted({item["frame"].payload.hex() for item in companion_up}),
            "response_payloads": sorted({item["frame"].payload.hex() for item in companion_down}),
        },
    }


def write_mimo_wifi_analysis(
    source: Path,
    output: Path,
    *,
    action_labels: tuple[str, ...] = ("yaw_right", "yaw_left", "pitch_up", "pitch_down"),
) -> dict:
    result = analyze_mimo_wifi_gimbal(source, action_labels=action_labels)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    return result


def _wrapped_uint16_delta(after: int, before: int) -> int:
    return (after - before) & 0xFFFF


def analyze_dji_wifi_envelope(path: Path) -> dict:
    """Validate Mimo's standard 20-byte envelope without mutating the PCAP."""

    datagrams, metadata = _read_udp(path)
    envelopes: list[tuple[UdpDatagram, DjiWifiEnvelope]] = []
    decoded: list[tuple[UdpDatagram, DjiWifiEnvelope, object]] = []
    for datagram in datagrams:
        if len(datagram.payload) < 20:
            continue
        try:
            envelope = DjiWifiEnvelope.parse(datagram.payload)
        except DjiWifiEnvelopeError:
            continue
        envelopes.append((datagram, envelope))
        if not envelope.payload.startswith(b"\x55"):
            continue
        frames = _frames(datagram)
        exact = [item for item in frames if item["offset"] == 20]
        if len(exact) != 1:
            continue
        decoded.append((datagram, envelope, exact[0]["frame"]))

    control_by_flow: dict[tuple[str, int, str, int], list] = defaultdict(list)
    for item in decoded:
        datagram, _envelope, frame = item
        if (
            frame.sender == 0x02
            and frame.receiver == 0x04
            and frame.cmd_set == 0x04
            and frame.cmd_id == 0x01
        ):
            control_by_flow[datagram.flow].append(item)
    if not control_by_flow:
        raise ValueError("no enveloped app-to-gimbal 04/01 flow found")
    flow, controls = max(control_by_flow.items(), key=lambda item: len(item[1]))
    upstream = [item for item in envelopes if item[0].flow == flow and item[1].wh_type == 5]
    selected = [
        item
        for item in decoded
        if item[0].flow == flow
        if (item[2].cmd_set, item[2].cmd_id) in {(0x04, 0x01), (0x04, 0x50)}
    ]
    selected.sort(key=lambda item: item[0].timestamp)
    sequences = [item[1].transport_sequence for item in upstream]
    message_sequences = [item[1].message_sequence for item in upstream]
    sequence_deltas = [
        _wrapped_uint16_delta(after, before)
        for before, after in zip(sequences, sequences[1:])
    ]
    message_deltas = [
        ((after & 0xFF) - (before & 0xFF)) & 0xFF
        for before, after in zip(message_sequences, message_sequences[1:])
    ]
    reencoded_matches = sum(envelope.encode() == datagram.payload for datagram, envelope, _ in selected)
    length_matches = sum(
        envelope.total_length == 20 + frame.total_length
        for _datagram, envelope, frame in selected
    )
    checksum_matches = sum(envelope.checksum_valid for _datagram, envelope, _frame in selected)
    field_values = {
        "format_nibble": sorted({item[1].format_nibble for item in selected}),
        "session_id": sorted({item[1].session_id for item in selected}),
        "wh_type": sorted({item[1].wh_type for item in selected}),
        "reserved_12_15_hex": sorted({item[1].reserved_12_15.hex() for item in selected}),
        "message_sequence_high_byte": sorted({item[1].message_sequence >> 8 for item in selected}),
        "delivery_flags": dict(Counter(item[1].delivery_flags for item in selected)),
        "reserved_19": sorted({item[1].reserved_19 for item in selected}),
    }
    command_counts = Counter(f"{item[2].cmd_set:02X}/{item[2].cmd_id:02X}" for item in selected)
    delivery_by_command: dict[str, Counter] = defaultdict(Counter)
    for _datagram, envelope, frame in selected:
        delivery_by_command[f"{frame.cmd_set:02X}/{frame.cmd_id:02X}"][envelope.delivery_flags] += 1

    # Reconstruct the cumulative receipt source used by Mimo for the next
    # outbound peer_sequence.  This is intentionally independent of DUML
    # command semantics: WhType 01 range 3 advances for both no-ACK 04/01 and
    # ACK-requesting traffic, whereas WhType 03 only identifies an individual
    # response packet.
    reverse_flow = (flow[2], flow[3], flow[0], flow[1])
    selected_session_id = controls[0][1].session_id
    latest_status_sequence: int | None = None
    latest_response_sequence: int | None = None
    status_packet_count = 0
    status_transition_count = 0
    ambiguous_status_count = 0
    peer_equals_latest_status = 0
    peer_equals_latest_response = 0
    peer_status_lag_steps: Counter[int] = Counter()
    comparable_outbound_count = 0
    for datagram in datagrams:
        if datagram.flow == reverse_flow:
            try:
                basic = DjiWifiBasicHeader.parse(datagram.payload)
            except DjiWifiEnvelopeError:
                continue
            if (
                not basic.checksum_valid
                or basic.session_id != selected_session_id
                or basic.format_nibble != 8
            ):
                continue
            if basic.wh_type == 1:
                try:
                    status = DjiWifiFlowStatus.parse(datagram.payload)
                except DjiWifiEnvelopeError:
                    continue
                status_packet_count += 1
                sequence = status.operator_peer_sequence
                if sequence is None:
                    ambiguous_status_count += 1
                else:
                    if sequence != latest_status_sequence:
                        status_transition_count += 1
                    latest_status_sequence = sequence
            elif basic.wh_type == 3:
                latest_response_sequence = basic.transport_sequence
        elif datagram.flow == flow:
            try:
                envelope = DjiWifiEnvelope.parse(datagram.payload)
            except DjiWifiEnvelopeError:
                continue
            if envelope.wh_type != 5 or envelope.session_id != selected_session_id:
                continue
            if latest_status_sequence is not None:
                comparable_outbound_count += 1
                if envelope.peer_sequence == latest_status_sequence:
                    peer_equals_latest_status += 1
                delta = (latest_status_sequence - envelope.peer_sequence) & 0xFFFF
                if delta < 0x8000 and delta % 8 == 0:
                    peer_status_lag_steps[delta // 8] += 1
            if envelope.peer_sequence == latest_response_sequence:
                peer_equals_latest_response += 1
    return {
        "source_file": path.name,
        "source_sha256": _sha256(path),
        **metadata,
        "selected_flow": {
            "source_ip": flow[0],
            "source_port": flow[1],
            "destination_ip": flow[2],
            "destination_port": flow[3],
        },
        "upstream_standard_envelope_count": len(upstream),
        "target_command_count": len(selected),
        "command_counts": dict(sorted(command_counts.items())),
        "field_values": field_values,
        "dynamic_fields": {
            "transport_sequence_first": sequences[0] if sequences else None,
            "transport_sequence_last": sequences[-1] if sequences else None,
            "plus_8_transition_count": sum(delta == 8 for delta in sequence_deltas),
            "transition_count": len(sequence_deltas),
            "transport_wrap_count": sum(after < before for before, after in zip(sequences, sequences[1:])),
            "message_plus_1_transition_count": sum(delta == 1 for delta in message_deltas),
            "message_transition_count": len(message_deltas),
            "message_low_byte_wrap_count": sum(
                (after & 0xFF) < (before & 0xFF)
                for before, after in zip(message_sequences, message_sequences[1:])
            ),
        },
        "validation": {
            "header_checksum_algorithm": "xor bytes 0..7 equals zero",
            "checksum_valid_count": checksum_matches,
            "length_equals_20_plus_duml_count": length_matches,
            "reencoded_byte_equal_count": reencoded_matches,
            "target_count": len(selected),
            "all_target_checksums_valid": checksum_matches == len(selected),
            "all_target_lengths_valid": length_matches == len(selected),
            "all_target_reencoded_equal": reencoded_matches == len(selected),
        },
        "delivery_flags_by_command": {
            key: dict(value) for key, value in sorted(delivery_by_command.items())
        },
        "flow_control": {
            "wh_type_01_status_packet_count": status_packet_count,
            "wh_type_01_range_3_transition_count": status_transition_count,
            "wh_type_01_range_3_ambiguous_count": ambiguous_status_count,
            "comparable_outbound_wh_type_05_count": comparable_outbound_count,
            "peer_equals_latest_wh_type_01_range_3_count": peer_equals_latest_status,
            "peer_equals_latest_wh_type_03_sequence_count": peer_equals_latest_response,
            "peer_lag_from_latest_wh_type_01_range_3_steps": dict(
                sorted(peer_status_lag_steps.items())
            ),
            "maximum_observed_lag_steps": (
                max(peer_status_lag_steps) if peer_status_lag_steps else None
            ),
        },
        "provenance": {
            "unknown_delivery_flag": (
                "0x60 is captured but its trigger/semantics are unresolved; generator is fail-closed to 0x00"
            ),
            "session_id": "capture/session-specific; not treated as a universal DJI signature",
        },
    }


def write_dji_wifi_envelope_analysis(source: Path, output: Path) -> dict:
    result = analyze_dji_wifi_envelope(source)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    return result
