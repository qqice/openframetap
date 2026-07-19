from __future__ import annotations

import asyncio
import time

from openframetap.app.input import ControlInput
from openframetap.app.state import AppStateSnapshot, StateStore
from openframetap.control.gimbal_profile import Pocket3StickCommand
from openframetap.control.live_wifi import LiveWifiControlSession
from openframetap.protocol.duml import encode_duml_frame
from openframetap.transport.dji_wifi_udp import UdpReceiveRecord


def test_live_stop_is_prioritized_and_finishes_with_redundant_center(monkeypatch) -> None:
    class FakeTransport:
        instances = []

        def __init__(self, target_ip, *, local_port, event_handler) -> None:
            self.target_ip = target_ip
            self.local_port = local_port
            self.event_handler = event_handler
            self.is_open = False
            self.commands: list[Pocket3StickCommand] = []
            self.received = 0
            self.__class__.instances.append(self)

        async def open(self) -> None:
            self.is_open = True

        async def send_stick(self, command, *, reason):
            self.commands.append(command)
            return {"reason": reason}

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
    session.start()
    deadline = time.monotonic() + 2
    while session.snapshot()["state"] != "armed" and time.monotonic() < deadline:
        time.sleep(0.01)
    assert session.snapshot()["state"] == "armed"
    session.submit(
        ControlInput(yaw=0.20, source="test", monotonic_ns=time.monotonic_ns(), active=True)
    )
    deadline = time.monotonic() + 1
    while not any(not item.is_center for item in FakeTransport.instances[0].commands) and time.monotonic() < deadline:
        time.sleep(0.01)
    session.stop("test_stop")
    commands = FakeTransport.instances[0].commands
    assert any(not item.is_center for item in commands)
    assert commands[-1].is_center
    assert sum(item.is_center for item in commands) >= 9
    assert session.snapshot()["state"] == "disabled", session.snapshot()
