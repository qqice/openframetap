import asyncio
import json
from pathlib import Path

import pytest

from openframetap.app.widgets import selected_mode
from openframetap.video.bitrate import EncodedBitrate
from openframetap.app.modes import stop_camera_livestream
from openframetap.transport.dji_wifi_udp import discover_rtmp_publisher_ip


def test_binary_selection_left_normal_right_live():
    assert selected_mode(0,170)=='normal'
    assert selected_mode(84,170)=='normal'
    assert selected_mode(85,170)=='livestream'
    assert selected_mode(169,170)=='livestream'


def test_encoded_bitrate_follows_video_not_network_interface():
    meter=EncodedBitrate()
    assert meter.sample(0) is None
    meter.record(500000)
    assert meter.sample(1_000_000_000)==4_000_000
    meter.record(125000)
    assert meter.sample(1_500_000_000)==2_000_000
    assert meter.sample(2_500_000_000)==0


def test_publisher_discovery_excludes_local_gui_reader():
    from types import SimpleNamespace
    def runner(argv,**kwargs):
        if argv[0]=='ss':
            return SimpleNamespace(returncode=0,stdout='0 0 192.168.1.229:1935 192.168.1.223:44000\n0 0 192.168.1.229:1935 192.168.1.229:55000')
        return SimpleNamespace(returncode=0,stdout='[{"addr_info":[{"family":"inet","local":"192.168.1.229"}]}]')
    assert discover_rtmp_publisher_ip(runner=runner)=='192.168.1.223'


def test_reconnect_ignores_old_telemetry_before_new_handshake():
    from openframetap.transport.dji_wifi_udp import DjiWifiUdpTransport,DjiWifiHandshakeProfile
    from openframetap.protocol.dji_wifi import DjiWifiBasicHeader
    class Socket:
        def setblocking(self,_):pass
        def bind(self,_):pass
        def close(self):pass
    async def scenario():
        transport=DjiWifiUdpTransport('192.168.2.1',socket_factory=lambda *_:Socket(),
            handshake_profile=DjiWifiHandshakeProfile(session_id=9))
        packets=[DjiWifiBasicHeader(8,8,8,0,1,0,True).encode(),
                 DjiWifiBasicHeader(9,8,9,0,0,0,True).encode()+b'\1']
        async def send(_):pass
        async def receive(_):return packets.pop(0),('192.168.2.1',9004)
        transport._sendto=send;transport._recvfrom=receive
        await transport.open()
        assert transport.is_open and not packets
        await transport.close()
    asyncio.run(scenario())


@pytest.mark.parametrize('fail',[False,True])
def test_camera_stop_once_and_disconnect_even_on_failure(monkeypatch,tmp_path,fail):
    calls=[]
    class Transport:
        fff5_write_count=3
        async def disconnect(self):calls.append('disconnect')
    class Ble:
        def __init__(self,*args):self.transport=Transport()
        async def open_authenticated(self):calls.append('authenticate')
        async def stop_livestream(self):
            calls.append('stop')
            if fail:raise RuntimeError('stop refused')
    monkeypatch.setattr('openframetap.workflows.pocket3_normal.BleNormalSession',Ble)
    if fail:
        with pytest.raises(RuntimeError,match='stop refused'):
            asyncio.run(stop_camera_livestream('test',tmp_path))
    else:
        assert asyncio.run(stop_camera_livestream('test',tmp_path))['confirmed']
    assert calls==['authenticate','stop','disconnect']
    summary=json.loads((tmp_path/'summary.json').read_text())
    assert summary['confirmed'] is (not fail)
