import asyncio
from types import SimpleNamespace
import pytest

from openframetap.app.artifacts import JsonlWriter
from openframetap.transport.bluez_ble import BluezBleTransport
from openframetap.devices.pocket3 import POCKET3_PROFILE
from openframetap.workflows.pocket3_normal import BleNormalSession
from openframetap.protocol.duml import encode_duml_frame,decode_duml_frame
from openframetap.video.pipelines import live_pipeline


def test_live_h264_queue_never_drops_reference_frames():
    spec=live_pipeline('rtmp://192.168.1.2:1935/live/test',source='rtmp',decoder='mppvideodec')
    a=spec.argv;i=a.index('name=encoded_queue');j=a.index('mppvideodec')
    assert 'leaky=no' in a[i:j] and 'leaky=downstream' not in a[i:j]
    assert a.index('name=display_queue')>j
    assert 'leaky=downstream' in a[j:]


def test_unsubscribe_failure_still_disconnects_owned_client():
    class Client:
        is_connected=True
        async def stop_notify(self,_):raise RuntimeError('UnknownObject')
        async def disconnect(self):self.is_connected=False
    t=BluezBleTransport('fixture',POCKET3_PROFILE);t._client=Client();t._subscribed=True
    asyncio.run(t.disconnect())
    assert not t.is_connected and t._disconnect_requested


def test_waiting_for_ssid_does_not_starve_ble_presence_heartbeat():
    async def run():
        class Transport:
            pending=None
            async def send_frame(self,raw,**kw):
                frame=decode_duml_frame(raw)
                if kw['command'].name=='normal_get_ssid':self.pending=frame
                if kw['command'].name=='normal_session_keepalive' and self.pending:
                    f=self.pending
                    session.notification(SimpleNamespace(data=encode_duml_frame(sender=7,receiver=2,
                        sequence=f.sequence,cmd_set=7,cmd_id=7,flags=0xC0,payload=b'\0\1x')))
        transport=Transport();session=BleNormalSession('fixture',lambda _:None,transport=transport)
        request=asyncio.create_task(session.send('normal_get_ssid',timeout=.5))
        await asyncio.sleep(.02)
        await session.send('normal_session_keepalive',reply=False)
        assert await request==b'\0\1x'
        assert not session.pending
    asyncio.run(run())


def test_unlimited_logs_rotate_instead_of_growing_forever(tmp_path):
    writer=JsonlWriter(tmp_path/'events.jsonl',max_bytes=80)
    for i in range(100):writer.write({'n':i})
    writer.close()
    assert len(list(tmp_path.iterdir()))==3
    assert all(p.stat().st_size<100 for p in tmp_path.iterdir())
