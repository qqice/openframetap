from __future__ import annotations

import asyncio
import time
import pytest

from openframetap.app.input import ControlInput
from openframetap.app.state import AppStateSnapshot, StateStore
from openframetap.control.gimbal_profile import Pocket3StickCommand
from openframetap.control.live_wifi import LiveWifiControlSession
from openframetap.protocol.duml import encode_duml_frame, decode_duml_frame
from openframetap.transport.dji_wifi_udp import UdpReceiveRecord


@pytest.mark.parametrize('actions',[False,True])
def test_live_stop_is_prioritized_and_finishes_with_redundant_center(monkeypatch,actions) -> None:
    class FakeTransport:
        instances = []

        def __init__(self, target_ip, *, local_port, event_handler) -> None:
            self.target_ip = target_ip
            self.local_port = local_port
            self.event_handler = event_handler
            self.is_open = False
            self.commands: list[Pocket3StickCommand] = []
            self.received = 0
            self.ack_observed_count = 0
            self.last_ack_sequence = None
            self.response_packet_count = 0
            self.last_response_sequence = None
            self.ambiguous_window_count = 0
            self.flow_ack_sent_count = 0
            self.keepalive_sent_count = 0
            self.keepalive_response_count = 0
            self.last_keepalive_response_ns = None
            self.__class__.instances.append(self)

        async def open(self) -> None:
            self.is_open = True

        async def send_stick(self, command, *, reason):
            self.commands.append(command)
            return {"reason": reason}

        async def send_control_keepalive(self):
            self.keepalive_sent_count += 1
            self.keepalive_response_count += 1
            self.last_keepalive_response_ns = time.monotonic_ns()
            return {"kind": "control_keepalive"}

        async def send_flow_ack_if_due(self):
            self.flow_ack_sent_count += 1
            return {"kind": "transport_flow_ack"}

        async def receive_datagram(self):
            if self.received == 0:
                self.received += 1
                raw = encode_duml_frame(
                    sender=4,
                    receiver=2,
                    sequence=1,
                    flags=0,
                    cmd_set=4,
                    cmd_id=5,
                    payload=b"\0" * 24,
                )
                return UdpReceiveRecord("fixture", time.monotonic_ns(), self.target_ip, 9004, raw)
            await asyncio.Event().wait()

        async def close(self) -> None:
            self.is_open = False

    monkeypatch.setattr(
        "openframetap.control.live_wifi.discover_rtmp_publisher_ip",
        lambda: "192.168.1.223",
    )
    monkeypatch.setattr(
        "openframetap.control.live_wifi.list_rtmp_server_peer_ips",
        lambda: ("192.168.1.223", "192.168.1.229"),
    )
    state = StateStore(AppStateSnapshot(ble_connected=True, rtmp_publisher_online=True))
    session = LiveWifiControlSession(state, transport_factory=FakeTransport)
    if actions:
        async def send_action(self,action):
            target,cs,ci,_=action.fields()
            # A fast ACK can precede return from send; it must not replace the
            # receive task handle with a numeric DUML receiver address.
            session.observe_action_frame(decode_duml_frame(encode_duml_frame(
                sender=target,receiver=2,sequence=123,flags=0xC0,
                cmd_set=cs,cmd_id=ci,payload=b'\0')))
            return 123
        FakeTransport.send_camera_action=send_action
    session.start()
    deadline = time.monotonic() + 2
    while session.snapshot()["state"] != "armed" and time.monotonic() < deadline:
        time.sleep(0.01)
    assert session.snapshot()["state"] == "armed"
    assert session.snapshot()["keepalive_response_count"] >= 1
    if actions:
        deadline=time.monotonic()+2
        while session.snapshot().get('last_action')!='query_formats' and time.monotonic()<deadline:
            time.sleep(.01)
        assert session.snapshot()['last_action_ok'] is True
        time.sleep(.3)
    # A non-zero radial value starts at the configured minimum protocol speed.
    session.submit(
        ControlInput(yaw=0.001, source="test", monotonic_ns=time.monotonic_ns(), active=True)
    )
    time.sleep(0.15)
    assert session.snapshot()["state"] == "active"
    assert any(item.yaw == 1024 + 32 for item in FakeTransport.instances[0].commands)
    session.submit(
        ControlInput(yaw=1.0, source="test", monotonic_ns=time.monotonic_ns(), active=True)
    )
    deadline = time.monotonic() + 1
    while (
        not any(item.yaw == 1024 + 188 for item in FakeTransport.instances[0].commands)
        and time.monotonic() < deadline
    ):
        time.sleep(0.01)
    session.stop("test_stop")
    commands = FakeTransport.instances[0].commands
    assert any(not item.is_center for item in commands)
    assert any(item.yaw == 1024 + 188 for item in commands)
    assert session.snapshot()["minimum_offset"] == 32
    assert session.snapshot()["maximum_offset"] == 188
    assert commands[-1].is_center
    assert sum(item.is_center for item in commands) >= 9
    assert session.snapshot()["state"] == "disabled", session.snapshot()
