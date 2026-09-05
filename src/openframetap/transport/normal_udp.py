"""Normal-view UDP session. Only one profile-validated enable write is exposed."""

from dataclasses import replace
import asyncio
import time

from openframetap.devices.pocket3_normal import build_normal_frame, validate_normal_frame, registration_reply
from openframetap.protocol.duml import decode_duml_frame
from openframetap.protocol.dji_wifi import DjiWifiBasicHeader, DjiWifiSequenceWindow
from openframetap.transport.dji_wifi_udp import DjiWifiUdpTransport, wifi_duml_wire_sequence


class NormalUdpTransport(DjiWifiUdpTransport):
    def __init__(self, local_ip: str, **kwargs):
        super().__init__('192.168.2.1', local_ip=local_ip, local_port=0, **kwargs)
        self.enable_count = 0
        self.received_windows = {}
        self.registration_count = 0
        self.registration_keys = set()
        self.session_open_sent = False
        self.last_presence_time = None
        self._serial_send = asyncio.Lock()

    async def send_flow_ack_if_due(self):
        async with self._serial_send:
            return await super().send_flow_ack_if_due()

    async def _send_profile_frame(self, raw, name):
        if self.sequencer is None:
            raise RuntimeError('normal UDP session is not handshaken')
        async with self._serial_send, self._writer_lock:
            if callable(raw):
                raw = raw()  # Allocate the DUML sequence only while owning the writer.
            envelope = self.sequencer.build(raw)
            await self._sendto(envelope.encode())
            self.last_sent_transport_sequence = envelope.transport_sequence
            self.duml_sequence = (self.duml_sequence + 1) & 65535
            self.event_handler(dict(kind=name, monotonic_ns=time.monotonic_ns(),
                                    data_hex=envelope.encode().hex()))

    async def open_application(self):
        if self.session_open_sent:
            raise RuntimeError('application open already sent')
        self.session_open_sent = True
        await self._send_profile_frame(lambda: build_normal_frame('normal_session_open',
            wifi_duml_wire_sequence(self.duml_sequence)), 'normal_udp_session_open')
        await self.presence()

    async def presence(self):
        now = time.monotonic()
        if self.last_presence_time is not None and now-self.last_presence_time < 1:
            return
        self.last_presence_time = now
        await self._send_profile_frame(lambda: build_normal_frame('normal_app_presence',
            wifi_duml_wire_sequence(self.duml_sequence)), 'normal_app_presence')

    async def answer_registration(self, frame):
        raw = registration_reply(frame)
        key = (frame.cmd_id,frame.sequence)
        if key in self.registration_keys:
            return
        self.registration_keys.add(key)
        if len(self.registration_keys)>128:
            self.registration_keys = {key}
        await self._send_profile_frame(raw, 'normal_registration_reply')
        self.registration_count += 1

    async def send_stick(self, *args, **kwargs):
        raise PermissionError('normal-view session exposes no gimbal writer')

    async def send_control_keepalive(self, *args, **kwargs):
        raise PermissionError('normal-view session exposes no gimbal query')

    async def enable_once(self):
        if self.enable_count or self.sequencer is None:
            raise RuntimeError('normal-view enable requires handshake and can run only once')
        async with self._serial_send, self._writer_lock:
            raw = build_normal_frame('normal_live_enable', wifi_duml_wire_sequence(self.duml_sequence))
            validate_normal_frame('normal_live_enable', decode_duml_frame(raw))
            envelope = self.sequencer.build(raw)
            self.enable_count += 1  # attempted writes consume the gate too
            await self._sendto(envelope.encode())
            self.last_sent_transport_sequence = envelope.transport_sequence
            self.duml_sequence = (self.duml_sequence + 1) & 65535
            self.event_handler(dict(kind='normal_live_enable', monotonic_ns=time.monotonic_ns(),
                                    data_hex=envelope.encode().hex()))

    async def receive_datagram(self, **kwargs):
        record = await super().receive_datagram(**kwargs)
        header = DjiWifiBasicHeader.parse(record.data)
        if (not header.checksum_valid or header.total_length != len(record.data)
                or header.session_id != self.handshake_profile.session_id
                or header.format_nibble != 8):
            raise RuntimeError('invalid normal-view UDP session envelope')
        if header.wh_type in (2, 3):
            seq = header.transport_sequence & 0xFFF8
            last = self.received_windows.get(header.wh_type)
            if last is None or 0 < (seq - last) & 65535 < 32768:
                self.received_windows[header.wh_type] = seq
        if self.latest_flow_status:
            status = self.latest_flow_status
            def observed(kind, fallback):
                seq = self.received_windows.get(kind)
                return fallback if seq is None else DjiWifiSequenceWindow(seq, seq, bytes(4))
            self.latest_flow_status = replace(status, range_1=observed(2, status.range_1),
                                              range_2=observed(3, status.range_2))
        return record


class NormalControlUdpTransport(NormalUdpTransport):
    """GUI-only capability: reuse the captured Pocket 3 controller on this socket."""

    async def send_stick(self, command, *, reason):
        async with self._serial_send:
            return await DjiWifiUdpTransport.send_stick(self, command, reason=reason)

    async def send_control_keepalive(self):
        async with self._serial_send:
            return await DjiWifiUdpTransport.send_control_keepalive(self)
