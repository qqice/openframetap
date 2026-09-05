import math
import struct

import pytest

from openframetap.protocol.camera_actions import CameraAction,focus_point
from openframetap.protocol.duml import decode_duml_frame
from openframetap.video.formats import parse_recording_capability


@pytest.mark.parametrize('name,payload',[('recenter','fe08'),('flip','fe09')])
def test_semantic_gimbal_commands_are_exact_and_not_raw_pwm(name,payload):
    f=decode_duml_frame(CameraAction(name).encode(0x1234))
    assert (f.sender,f.receiver,f.cmd_set,f.cmd_id,f.flags,f.payload.hex())==(2,4,4,0x4C,0x40,payload)
    assert f.crc8_valid and f.crc16_valid
    with pytest.raises(PermissionError):CameraAction('raw_pwm').encode(1)


def test_focus_point_payload_and_letterbox_mapping():
    f=decode_duml_frame(CameraAction('focus',.25,.75).encode(10))
    assert (f.receiver,f.cmd_set,f.cmd_id)==(1,2,0x30)
    assert f.payload==struct.pack('<ff',.25,.75)+bytes(13)
    assert focus_point(640,300,1280,600,1280,720)==(.5,.5)
    assert focus_point(10,300,1280,600,1280,720) is None
    assert focus_point(100,100,0,600,1280,720) is None
    for value in (-.1,1.1,math.nan,math.inf):
        with pytest.raises(ValueError):CameraAction('focus',value,.5).encode(1)


def test_query_is_a_named_recording_capability_not_an_invented_video_codec_query():
    f=decode_duml_frame(CameraAction('query_formats').encode(0))
    assert (f.receiver,f.cmd_set,f.cmd_id)==(0x28,0,0x99)
    assert b'camcap_video_format' in f.payload
    name=b'camcap_video_format';value=b'\1\7\0\2\x0a\3\0\x10\6\0'
    payload=b'\2\6'+bytes(9)+struct.pack('<HH',len(name)+len(value)+10,len(name))+name+bytes(6)+struct.pack('<H',len(value))+value
    table=parse_recording_capability(payload)
    assert len(table)==2 and table[0]['fps_code']==3
    assert parse_recording_capability(payload[:-1]) is None


@pytest.mark.parametrize('writes,attempts',[(0,2),(1,1)])
def test_stop_retries_disappeared_bluez_device_only_before_any_write(tmp_path,monkeypatch,writes,attempts):
    import asyncio
    from types import SimpleNamespace
    from openframetap.app.modes import stop_camera_livestream
    calls=[]
    async def disconnect():pass
    class Ble:
        def __init__(self,*args):
            self.transport=SimpleNamespace(fff5_write_count=0,disconnect=disconnect)
        async def open_authenticated(self):
            calls.append('connect')
            if len(calls)==1:
                self.transport.fff5_write_count=writes
                raise RuntimeError("device 'dev_fixture' not found")
        async def stop_livestream(self):calls.append('stop')
    monkeypatch.setattr('openframetap.workflows.pocket3_normal.BleNormalSession',Ble)
    if writes:
        with pytest.raises(RuntimeError):asyncio.run(stop_camera_livestream('fixture',tmp_path))
        assert 'stop' not in calls
    else:
        assert asyncio.run(stop_camera_livestream('fixture',tmp_path))['confirmed']
    assert calls.count('connect')==attempts


def test_resolution_selection_preserves_original_and_other_wire_fields(tmp_path):
    import hashlib
    from pathlib import Path
    from openframetap.devices.pocket3_livestream import write_stream_proposal,load_fixed_stream_proposal
    from openframetap.video.formats import make_resolution_proposal
    address='AA:BB:CC:DD:EE:FF'
    proposal=write_stream_proposal(address=address,rtmp_url='rtmp://192.168.1.2:1935/live/test-only',
        private_root=tmp_path/'artifacts/private/proposals',sanitized_root=tmp_path/'artifacts/sanitized/proposals',
        wifi_result_sha256='5'*64,server_status_sha256='6'*64)
    source=Path(proposal['private_proposal'])
    original=source.read_bytes()
    _,old=load_fixed_stream_proposal(source,expected_address=address)
    old_frame=decode_duml_frame(old)
    for height,code in ((480,0x47),(720,4),(1080,0x0A)):
        out=make_resolution_proposal(source,tmp_path/f'selected-{height}.json',address=address,height=height)
        payload,raw=load_fixed_stream_proposal(out,expected_address=address)
        frame=decode_duml_frame(raw)
        assert frame.payload[3]==code
        assert frame.payload[:3]+frame.payload[4:]==old_frame.payload[:3]+old_frame.payload[4:]
        assert payload['decoded']==frame.to_dict()
        assert payload['frame_sha256']==hashlib.sha256(raw).hexdigest()
        assert source.read_bytes()==original
    with pytest.raises(ValueError):make_resolution_proposal(source,tmp_path/'bad.json',address=address,height=2160)
