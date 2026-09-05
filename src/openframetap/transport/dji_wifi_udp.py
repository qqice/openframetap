"""Single-writer UDP transport for structured Pocket 3 stick commands."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
import ipaddress
import json
import re
import secrets
import socket
import subprocess
import time
from datetime import datetime, timezone
from typing import Callable

from openframetap.control.gimbal_profile import (
    Pocket3StickCommand,
    decode_control_keepalive_response,
    encode_control_keepalive,
    validate_stick_duml,
)
from openframetap.protocol.dji_wifi import (
    DjiWifiBasicHeader,
    DjiWifiEnvelope,
    DjiWifiFlowStatus,
    DjiWifiOperatorSequencer,
    encode_operator_flow_ack,
    DJI_WIFI_FORMAT_NIBBLE,
    DJI_WIFI_HANDSHAKE,
    DJI_WIFI_TARGET_PORT,
)


DEFAULT_LOCAL_PORT = 54232
FLOW_ACK_INTERVAL_NS = 25_000_000
CAPTURED_SESSION_ID = 0x7055
CAPTURED_SEQUENCE_SEED = 0x82A8


def wifi_duml_wire_sequence(counter: int) -> int:
    """Map Mimo's little-endian incrementing counter to DUML wire order."""

    if not 0 <= counter <= 0xFFFF:
        raise ValueError("Wi-Fi DUML counter outside uint16")
    return ((counter & 0xFF) << 8) | (counter >> 8)


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def validate_pocket_target_ip(value: str) -> str:
    try:
        address = ipaddress.ip_address(value)
    except ValueError as exc:
        raise ValueError(f"invalid Pocket publisher IP: {value}") from exc
    tailscale = ipaddress.ip_network("100.64.0.0/10")
    if address.version != 4 or not address.is_private or address in tailscale:
        raise ValueError("Pocket target must be a private IPv4 address outside Tailscale")
    if address.is_loopback or address.is_link_local or address.is_multicast or address.is_unspecified:
        raise ValueError("Pocket target IP is not a usable publisher address")
    return str(address)


def parse_rtmp_publisher_ips(ss_output: str) -> tuple[str, ...]:
    """Extract peer IPs from ``ss -Htn state established sport = :1935``."""

    found: set[str] = set()
    for line in ss_output.splitlines():
        columns = line.split()
        if len(columns) < 4:
            continue
        peer = columns[-1]
        match = re.match(r"^\[?([^\]]+)\]?:([0-9]+)$", peer)
        if not match:
            continue
        try:
            found.add(validate_pocket_target_ip(match.group(1)))
        except ValueError:
            continue
    return tuple(sorted(found))


def discover_rtmp_publisher_ip(
    *, runner: Callable[..., subprocess.CompletedProcess] = subprocess.run
) -> str:
    addresses = list_rtmp_server_peer_ips(runner=runner)
    # A persistent GUI can start its reader before the control thread runs.
    # MediaMTX lists that local reader beside the real camera publisher.
    result = runner(['ip','-j','-4','address','show'],text=True,capture_output=True,check=True,timeout=5)
    local_addresses = {item['local'] for interface in json.loads(result.stdout)
                       for item in interface.get('addr_info',[]) if item.get('family')=='inet'}
    addresses = tuple(address for address in addresses if address not in local_addresses)
    if len(addresses) != 1:
        raise RuntimeError(
            f"expected exactly one current private RTMP publisher, found {len(addresses)}"
        )
    return addresses[0]


def list_rtmp_server_peer_ips(
    *, runner: Callable[..., subprocess.CompletedProcess] = subprocess.run
) -> tuple[str, ...]:
    """Return private peers connected to MediaMTX's RTMP listener.

    This includes the Pocket publisher and may also include a local playback
    client.  Callers that already locked a Pocket target must therefore check
    membership instead of requiring this list to contain exactly one address.
    """

    result = runner(
        ["ss", "-Htn", "state", "established", "sport", "=", ":1935"],
        text=True,
        capture_output=True,
        check=False,
        timeout=5,
    )
    if result.returncode != 0:
        raise RuntimeError(f"unable to inspect MediaMTX publisher: {result.stderr.strip()}")
    return parse_rtmp_publisher_ips(result.stdout)


@dataclass(frozen=True, slots=True)
class DjiWifiHandshakeProfile:
    """Structured values from the Mimo session setup datagram.

    The final capability bytes are preserved as provenance-bearing profile
    data, not used as an opaque standard-envelope header.
    """

    session_id: int = CAPTURED_SESSION_ID
    sequence_seed: int = CAPTURED_SEQUENCE_SEED
    link_quality: int = 100
    secondary_quality: int = 100
    mtu: int = 1472
    frame_interval_ms: int = 20
    trailing_quality_be: int = 100
    product_prefix: bytes = b"\x00\x00\x01\x90"
    capability_payload: bytes = bytes.fromhex(
        "01c005140000640014006400c00514000064000101040102"
    )
    provenance: str = "Mimo Pocket 3 PCAP SHA256 8e7c7eb6...139a181c"

    @classmethod
    def fresh(cls) -> "DjiWifiHandshakeProfile":
        """Create a new client-owned UDP session identity.

        The capture proves both fields are session state. Reusing them also
        reuses DUML sequence zero, which Pocket treats as an old transaction
        after a previous control socket closes.
        """

        return cls(
            session_id=secrets.randbelow(0xFFFF) + 1,
            sequence_seed=secrets.randbelow(0x2000) * 8,
            provenance="fresh per-connection identity; structure from Mimo capture",
        )

    def encode(self) -> bytes:
        metadata = (
            self.sequence_seed.to_bytes(2, "little")
            + self.link_quality.to_bytes(2, "little")
            + self.secondary_quality.to_bytes(2, "little")
            + self.mtu.to_bytes(2, "little")
            + self.frame_interval_ms.to_bytes(2, "little")
            + self.trailing_quality_be.to_bytes(2, "big")
            + self.product_prefix
        )
        payload = metadata + self.capability_payload
        basic = DjiWifiBasicHeader(
            total_length=8 + len(payload),
            format_nibble=DJI_WIFI_FORMAT_NIBBLE,
            session_id=self.session_id,
            transport_sequence=0,
            wh_type=DJI_WIFI_HANDSHAKE,
            checksum=0,
            checksum_valid=True,
        ).encode()
        return basic + payload


@dataclass(frozen=True, slots=True)
class UdpReceiveRecord:
    wall_time_utc: str
    monotonic_ns: int
    peer_ip: str
    peer_port: int
    data: bytes

    def to_dict(self) -> dict:
        return {
            "wall_time_utc": self.wall_time_utc,
            "monotonic_ns": self.monotonic_ns,
            "peer_ip": self.peer_ip,
            "peer_port": self.peer_port,
            "length": len(self.data),
            "data_hex": self.data.hex(),
        }


class DjiWifiUdpTransport:
    """Own one bound socket and expose only structured 04/01 sends."""

    def __init__(
        self,
        target_ip: str,
        *,
        target_port: int = DJI_WIFI_TARGET_PORT,
        local_port: int = DEFAULT_LOCAL_PORT,
        local_ip: str = "0.0.0.0",
        handshake_profile: DjiWifiHandshakeProfile | None = None,
        socket_factory: Callable[..., socket.socket] = socket.socket,
        event_handler: Callable[[dict], None] | None = None,
        clock_ns: Callable[[], int] = time.monotonic_ns,
    ) -> None:
        self.target_ip = validate_pocket_target_ip(target_ip)
        if target_port != DJI_WIFI_TARGET_PORT:
            raise ValueError("DJI Wi-Fi gimbal target port is fixed at 9004")
        if not 0 <= local_port <= 65535:
            raise ValueError("local UDP port outside 0..65535")
        self.local_ip = str(ipaddress.IPv4Address(local_ip))
        self.target_port = target_port
        self.local_port = local_port
        self.handshake_profile = handshake_profile or DjiWifiHandshakeProfile.fresh()
        self.socket_factory = socket_factory
        self.event_handler = event_handler or (lambda event: None)
        self.clock_ns = clock_ns
        self.socket: socket.socket | None = None
        self.sequencer: DjiWifiOperatorSequencer | None = None
        self.duml_sequence = 0
        self._writer_lock = asyncio.Lock()
        self._owner_task: asyncio.Task | None = None
        self.sent_records: list[dict] = []
        self.ack_observed_count = 0
        self.last_ack_sequence: int | None = None
        self.response_packet_count = 0
        self.last_response_sequence: int | None = None
        self.ambiguous_window_count = 0
        self.latest_flow_status: DjiWifiFlowStatus | None = None
        self.flow_ack_sent_count = 0
        self.last_flow_ack_ns: int | None = None
        self.keepalive_sent_count = 0
        self.keepalive_response_count = 0
        self.last_keepalive_response_ns: int | None = None
        self.pending_keepalives: dict[int, int] = {}
        self.last_sent_transport_sequence = self.handshake_profile.sequence_seed

    @property
    def is_open(self) -> bool:
        return self.socket is not None

    async def _sendto(self, data: bytes) -> None:
        if self.socket is None:
            raise RuntimeError("UDP socket is not open")
        await asyncio.get_running_loop().sock_sendto(
            self.socket, data, (self.target_ip, self.target_port)
        )

    async def _recvfrom(self, size: int) -> tuple[bytes, tuple[str, int]]:
        if self.socket is None:
            raise RuntimeError("UDP socket is not open")
        return await asyncio.get_running_loop().sock_recvfrom(self.socket, size)

    async def open(self, *, handshake_timeout: float = 1.0) -> None:
        if self.socket is not None:
            raise RuntimeError("UDP control socket is already open")
        sock = self.socket_factory(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            sock.setblocking(False)
            sock.bind((self.local_ip, self.local_port))
            if self.local_port == 0:
                self.local_port = sock.getsockname()[1]
            self.socket = sock
            request = self.handshake_profile.encode()
            await self._sendto(request)
            handshake_record = {
                "wall_time_utc": _utc_now(),
                "monotonic_ns": time.monotonic_ns(),
                "kind": "transport_handshake",
                "data_hex": request.hex(),
            }
            self.sent_records.append(handshake_record)
            self.event_handler(handshake_record)
            deadline = asyncio.get_running_loop().time()+handshake_timeout
            while True:
                remaining=deadline-asyncio.get_running_loop().time()
                if remaining<=0:
                    raise TimeoutError('Pocket UDP handshake response timed out')
                response, peer = await asyncio.wait_for(self._recvfrom(2048),remaining)
                if peer[0] != self.target_ip or peer[1] != self.target_port:
                    raise RuntimeError("handshake response came from an unexpected UDP peer")
                basic = DjiWifiBasicHeader.parse(response)
                # Re-arming reuses the local port; late telemetry from the old
                # session can precede the new handshake ACK. It cannot satisfy it.
                if basic.session_id != self.handshake_profile.session_id or basic.wh_type != DJI_WIFI_HANDSHAKE:
                    continue
                break
            if (
                not basic.checksum_valid
                or basic.format_nibble != DJI_WIFI_FORMAT_NIBBLE
                or basic.session_id != self.handshake_profile.session_id
                or basic.wh_type != DJI_WIFI_HANDSHAKE
                or response[8:] != b"\x01"
            ):
                raise RuntimeError("Pocket UDP handshake response did not match the captured state")
            self.sequencer = DjiWifiOperatorSequencer(
                session_id=self.handshake_profile.session_id,
                next_transport_sequence=(self.handshake_profile.sequence_seed + 8) & 0xFFFF,
                peer_sequence=self.handshake_profile.sequence_seed,
            )
            self.event_handler(
                {
                    "wall_time_utc": _utc_now(),
                    "monotonic_ns": time.monotonic_ns(),
                    "kind": "transport_handshake_response",
                    "data_hex": response.hex(),
                    "peer_ip": peer[0],
                    "peer_port": peer[1],
                }
            )
        except BaseException:
            sock.close()
            self.socket = None
            self.sequencer = None
            raise

    async def send_stick(self, command: Pocket3StickCommand, *, reason: str) -> dict:
        if type(command) is not Pocket3StickCommand:
            raise TypeError("transport accepts only a Pocket3StickCommand object")
        if self.socket is None or self.sequencer is None:
            raise RuntimeError("UDP control transport is not open and handshaken")
        task = asyncio.current_task()
        if self._owner_task is not None and self._owner_task is not task:
            raise RuntimeError("single-writer violation")
        async with self._writer_lock:
            self._owner_task = task
            try:
                wire_sequence = wifi_duml_wire_sequence(self.duml_sequence)
                duml = command.encode_duml(sequence=wire_sequence)
                validate_stick_duml(duml)
                envelope = self.sequencer.build(duml)
                envelope.validate_operator_policy()
                datagram = envelope.encode()
                await self._sendto(datagram)
                self.last_sent_transport_sequence = envelope.transport_sequence
                self.duml_sequence = (self.duml_sequence + 1) & 0xFFFF
                record = {
                    "wall_time_utc": _utc_now(),
                    "monotonic_ns": time.monotonic_ns(),
                    "kind": "stick_center" if command.is_center else "stick_non_center",
                    "reason": reason,
                    "target_ip": self.target_ip,
                    "target_port": self.target_port,
                    "local_port": self.local_port,
                    "transport_sequence": envelope.transport_sequence,
                    "message_sequence": envelope.message_sequence,
                    "duml_sequence": wire_sequence,
                    "duml_counter": (self.duml_sequence - 1) & 0xFFFF,
                    "pitch": command.pitch,
                    "yaw": command.yaw,
                    "duml_hex": duml.hex(),
                    "datagram_hex": datagram.hex(),
                }
                self.sent_records.append(record)
                self.event_handler(record)
                return record
            finally:
                self._owner_task = None

    async def send_control_keepalive(self) -> dict:
        """Send only the exact capture-verified 04/50 control keepalive."""

        if self.socket is None or self.sequencer is None:
            raise RuntimeError("UDP control transport is not open and handshaken")
        task = asyncio.current_task()
        if self._owner_task is not None and self._owner_task is not task:
            raise RuntimeError("single-writer violation")
        async with self._writer_lock:
            self._owner_task = task
            duml_counter = self.duml_sequence
            duml_sequence = wifi_duml_wire_sequence(duml_counter)
            self.pending_keepalives[duml_sequence] = time.monotonic_ns()
            try:
                duml = encode_control_keepalive(sequence=duml_sequence)
                envelope = self.sequencer.build(duml)
                envelope.validate_operator_policy()
                datagram = envelope.encode()
                await self._sendto(datagram)
                self.last_sent_transport_sequence = envelope.transport_sequence
                self.duml_sequence = (self.duml_sequence + 1) & 0xFFFF
                self.keepalive_sent_count += 1
                record = {
                    "wall_time_utc": _utc_now(),
                    "monotonic_ns": time.monotonic_ns(),
                    "kind": "control_keepalive",
                    "target_ip": self.target_ip,
                    "target_port": self.target_port,
                    "local_port": self.local_port,
                    "transport_sequence": envelope.transport_sequence,
                    "message_sequence": envelope.message_sequence,
                    "duml_sequence": duml_sequence,
                    "duml_counter": duml_counter,
                    "duml_hex": duml.hex(),
                    "datagram_hex": datagram.hex(),
                }
                self.sent_records.append(record)
                self.event_handler(record)
                return record
            except BaseException:
                self.pending_keepalives.pop(duml_sequence, None)
                raise
            finally:
                self._owner_task = None

    async def send_flow_ack_if_due(self) -> dict | None:
        """Send Mimo-shaped WhType 04 flow state at no more than 40 Hz."""

        status = self.latest_flow_status
        if status is None:
            return None
        if self.socket is None or self.sequencer is None:
            raise RuntimeError("UDP control transport is not open and handshaken")
        now = self.clock_ns()
        if self.last_flow_ack_ns is not None and now - self.last_flow_ack_ns < FLOW_ACK_INTERVAL_NS:
            return None
        task = asyncio.current_task()
        if self._owner_task is not None and self._owner_task is not task:
            raise RuntimeError("single-writer violation")
        async with self._writer_lock:
            self._owner_task = task
            try:
                datagram = encode_operator_flow_ack(
                    status, last_sent_sequence=self.last_sent_transport_sequence
                )
                await self._sendto(datagram)
                self.last_flow_ack_ns = now
                self.flow_ack_sent_count += 1
                record = {
                    "wall_time_utc": _utc_now(),
                    "monotonic_ns": now,
                    "kind": "transport_flow_ack",
                    "flow_ack_sent_count": self.flow_ack_sent_count,
                    "processed_range_1": status.range_1.end,
                    "processed_range_2": status.range_2.end,
                    "operator_window_start": status.range_3.end,
                    "operator_window_end": self.last_sent_transport_sequence,
                    "data_hex": datagram.hex(),
                }
                self.sent_records.append(record)
                self.event_handler(record)
                return record
            finally:
                self._owner_task = None

    async def receive_datagram(self, *, maximum_size: int = 65535) -> UdpReceiveRecord:
        """Receive only from the already validated Pocket peer.

        UDP delivery remains unacknowledged; this method exists to preserve
        the downlink telemetry that moves from FFF4 to the Wi-Fi session after
        the transport handshake.
        """

        if self.socket is None:
            raise RuntimeError("UDP control transport is not open")
        data, peer = await self._recvfrom(maximum_size)
        if peer != (self.target_ip, self.target_port):
            raise RuntimeError("received a control-session datagram from an unexpected peer")
        try:
            basic = DjiWifiBasicHeader.parse(data)
        except Exception:
            basic = None
        valid_session_packet = (
            basic is not None
            and basic.checksum_valid
            and basic.format_nibble == DJI_WIFI_FORMAT_NIBBLE
            and basic.session_id == self.handshake_profile.session_id
        )
        if valid_session_packet and basic is not None and basic.wh_type == 0x03:
            # WhType 03 carries a response packet's own transport sequence. It
            # is not Mimo's cumulative peer_sequence source; retain it as
            # response provenance without advancing the outbound window.
            self.response_packet_count += 1
            self.last_response_sequence = basic.transport_sequence
            self.event_handler(
                {
                    "wall_time_utc": _utc_now(),
                    "monotonic_ns": time.monotonic_ns(),
                    "kind": "transport_response_observed",
                    "response_sequence": basic.transport_sequence,
                    "response_packet_count": self.response_packet_count,
                }
            )
        if valid_session_packet and basic is not None and basic.wh_type == 0x01:
            try:
                status = DjiWifiFlowStatus.parse(data)
            except Exception:
                status = None
            if status is not None:
                self.latest_flow_status = status
                peer_sequence = status.operator_peer_sequence
                if peer_sequence is None:
                    self.ambiguous_window_count += 1
                    self.event_handler(
                        {
                            "wall_time_utc": _utc_now(),
                            "monotonic_ns": time.monotonic_ns(),
                            "kind": "transport_window_ambiguous",
                            "range_start": status.range_3.start,
                            "range_end": status.range_3.end,
                            "ambiguous_window_count": self.ambiguous_window_count,
                        }
                    )
                elif self.sequencer is not None and self.sequencer.observe_peer_sequence(
                    peer_sequence
                ):
                    self.ack_observed_count += 1
                    self.last_ack_sequence = peer_sequence
                    self.event_handler(
                        {
                            "wall_time_utc": _utc_now(),
                            "monotonic_ns": time.monotonic_ns(),
                            "kind": "transport_ack_observed",
                            "ack_source": "wh_type_01_range_3",
                            "ack_sequence": peer_sequence,
                            "ack_observed_count": self.ack_observed_count,
                        }
                    )
        try:
            envelope = DjiWifiEnvelope.parse(data)
            if envelope.session_id != self.handshake_profile.session_id:
                raise ValueError("keepalive response belongs to another UDP session")
            keepalive_sequence = decode_control_keepalive_response(envelope.payload)
        except Exception:
            keepalive_sequence = None
        if keepalive_sequence is not None and keepalive_sequence in self.pending_keepalives:
            self.pending_keepalives.pop(keepalive_sequence, None)
            self.keepalive_response_count += 1
            self.last_keepalive_response_ns = time.monotonic_ns()
            self.event_handler(
                {
                    "wall_time_utc": _utc_now(),
                    "monotonic_ns": self.last_keepalive_response_ns,
                    "kind": "control_keepalive_response",
                    "duml_sequence": keepalive_sequence,
                    "response_count": self.keepalive_response_count,
                }
            )
        return UdpReceiveRecord(
            wall_time_utc=_utc_now(),
            monotonic_ns=time.monotonic_ns(),
            peer_ip=peer[0],
            peer_port=peer[1],
            data=bytes(data),
        )

    async def close(self) -> None:
        if self.socket is not None:
            self.socket.close()
            self.socket = None
        self.sequencer = None
        self.latest_flow_status = None
