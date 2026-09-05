import asyncio
import time
from types import SimpleNamespace

import pytest

from openframetap.app.modes import next_mode
from openframetap.app.normal_session import normal_gui_spec
from openframetap.app.state import StateStore, AppStateSnapshot
from openframetap.app.input import ControlInput
from openframetap.control.live_wifi import LiveWifiControlSession
from openframetap.protocol.dji_wifi import DjiWifiOperatorSequencer
from openframetap.protocol.duml import decode_duml_frame
from openframetap.control.gimbal_profile import CENTER_STICK_COMMAND
from openframetap.transport.normal_udp import NormalControlUdpTransport, NormalUdpTransport


def test_mode_switch_requires_successful_teardown():
    assert next_mode(dict(stop_reason='mode_switch:normal',error=None))=='normal'
    assert next_mode(dict(stop_reason='duration',error=None)) is None
    assert next_mode(dict(stop_reason='mode_switch:normal',error='stop failed')) is None
    with pytest.raises(RuntimeError):
        next_mode(dict(stop_reason='mode_switch:livestream',normal_session={'network_restored':False}))


def test_normal_gui_uses_same_named_mpp_and_gtk_surface():
    spec=normal_gui_spec()
    assert spec.decoder=='mppvideodec' and spec.source_kind=='normal'
    assert 'name=app_decoder' in spec.argv and 'name=app_fps_sink' in spec.argv
    assert 'name=normal_source' in spec.argv
    assert not any('rtmp' in token or 'appsink' in token for token in spec.argv)


def test_control_and_registration_share_serialized_transport():
    async def scenario():
        udp=NormalControlUdpTransport('192.168.2.2')
        udp.socket=object()
        udp.sequencer=DjiWifiOperatorSequencer(1,8,0)
        frames=[]
        async def send(data):
            frames.append(data)
            await asyncio.sleep(0.002)
        udp._sendto=send
        await asyncio.gather(udp.send_stick(CENTER_STICK_COMMAND,reason='release'),
                             udp.open_application(),udp.send_flow_ack_if_due())
        assert len(frames)==3
        assert len({int.from_bytes(data[4:6],'little') for data in frames})==3
        assert len({decode_duml_frame(data[20:]).sequence for data in frames})==3
        with pytest.raises(PermissionError):
            await NormalUdpTransport('192.168.2.2').send_stick(CENTER_STICK_COMMAND,reason='test')
    asyncio.run(scenario())


def test_attached_controller_does_not_open_second_socket_or_require_rtmp(monkeypatch):
    async def scenario():
        class Transport:
            target_ip='192.168.2.1'
            local_port=40001
            is_open=True
            keepalive_response_count=0
            keepalive_sent_count=0
            last_keepalive_response_ns=None
            def __init__(self): self.commands=[]
            async def send_stick(self,command,**kwargs):
                self.commands.append(command)
                return {}
            async def send_control_keepalive(self):
                self.keepalive_response_count+=1
                self.keepalive_sent_count+=1
                self.last_keepalive_response_ns=time.monotonic_ns()
            async def open(self): raise AssertionError('second socket')
            async def close(self): raise AssertionError('receiver owner must close socket')
            async def receive_datagram(self): raise AssertionError('second receiver')
        def forbidden(): raise AssertionError('RTMP dependency in normal mode')
        monkeypatch.setattr('openframetap.control.live_wifi.discover_rtmp_publisher_ip',forbidden)
        monkeypatch.setattr('openframetap.control.live_wifi.list_rtmp_server_peer_ips',forbidden)
        state=StateStore(AppStateSnapshot(ble_connected=True,session_mode='normal'))
        control=LiveWifiControlSession(state)
        transport=Transport(); ready=asyncio.Event();ready.set()
        task=asyncio.create_task(control._run(attached_transport=transport,attached_ready=ready))
        for _ in range(200):
            if control.snapshot()['state']=='armed': break
            await asyncio.sleep(0.01)
        assert control.snapshot()['state']=='armed'
        control.submit(ControlInput(yaw=0.05,active=True,source='mock-touch',monotonic_ns=time.monotonic_ns()))
        await asyncio.sleep(0.15)
        assert any(not c.is_center for c in transport.commands)
        control.submit(ControlInput(emergency_stop=True,source='mode_switch',monotonic_ns=time.monotonic_ns()))
        await asyncio.wait_for(task,3)
        assert transport.commands[-1].is_center
        assert control.snapshot()['state']=='disabled'
        assert transport.is_open
    asyncio.run(scenario())
