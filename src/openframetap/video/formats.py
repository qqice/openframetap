"""Monitor output choices, kept distinct from the camera's recording table."""
import hashlib
import json
from pathlib import Path

from openframetap.devices.pocket3_livestream import load_fixed_stream_proposal
from openframetap.protocol.duml import decode_duml_frame
from openframetap.protocol.livestream_commands import build_configure_live_stream_frame

RTMP_HEIGHTS=(480,720,1080)


def parse_recording_capability(payload):
    if len(payload)<24 or payload[:2]!=b'\x02\x06':return None
    n=int.from_bytes(payload[13:15],'little')
    if not 0<n<80 or len(payload)<23+n:return None
    if payload[15:15+n]!=b'camcap_video_format':return None
    length=int.from_bytes(payload[21+n:23+n],'little')
    value=payload[23+n:23+n+length]
    if len(value)!=length or len(value)<4 or value[0]!=1:return None
    inner=int.from_bytes(value[1:3],'little')
    if inner+3>len(value):return None
    count=value[3]
    if not count or inner!=1+count*3:return None
    return [{'resolution_code':value[4+i*3],'fps_code':value[5+i*3],
             'flags':value[6+i*3]} for i in range(count)]


def make_resolution_proposal(source: Path,output: Path,*,address: str,height: int):
    if height not in RTMP_HEIGHTS:raise ValueError('unsupported RTMP output height')
    proposal,raw=load_fixed_stream_proposal(source,expected_address=address)
    frame=decode_duml_frame(raw)
    url=frame.payload[14:].decode('utf-8')
    raw=build_configure_live_stream_frame(rtmp_url=url,resolution=height,fps=30,
                                         bitrate_kbps=int.from_bytes(frame.payload[4:6],'little'))
    result={**proposal,'frame_hex':raw.hex(),'frame_sha256':hashlib.sha256(raw).hexdigest(),
            'resolution':height,'fps':30,'selection_source':'user GUI monitor output selection'}
    result['decoded']=decode_duml_frame(raw).to_dict()
    output.parent.mkdir(parents=True,exist_ok=True)
    output.write_text(json.dumps(result,indent=2)+'\n')
    output.chmod(0o600)
    load_fixed_stream_proposal(output,expected_address=address)
    return output
