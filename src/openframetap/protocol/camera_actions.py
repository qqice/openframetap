"""Narrow camera/semantic gimbal actions; wire schemas, never arbitrary payloads."""
from dataclasses import dataclass
import math
import struct

from openframetap.protocol.duml import encode_duml_frame

REFERENCE = 'OpenPocketCine 47762e913ac6297fdf90cf7f643423d2d8274c2b Commands.swift'


@dataclass(frozen=True)
class CameraAction:
    name: str
    x: float = 0.5
    y: float = 0.5

    def fields(self):
        if self.name=='recenter': return 4,4,0x4C,b'\xfe\x08'
        if self.name=='flip': return 4,4,0x4C,b'\xfe\x09'
        if self.name=='focus':
            if not all(math.isfinite(v) and 0<=v<=1 for v in (self.x,self.y)):
                raise ValueError('focus point must be within the image')
            return 1,2,0x30,struct.pack('<ff',self.x,self.y)+bytes(13)
        if self.name=='query_formats':
            # This documented table describes RECORDING, not the monitor codec.
            key=b'camcap_video_format'
            p=b'\x02\x02\0\0'+struct.pack('<I',0x6ADF)+bytes(3)
            p+=struct.pack('<HH',len(key)+6,len(key))+key+bytes(4)
            return 0x28,0,0x99,p
        raise PermissionError('unknown camera action')

    def encode(self,sequence):
        receiver,cmd_set,cmd_id,payload=self.fields()
        return encode_duml_frame(sender=2,receiver=receiver,sequence=sequence,flags=0x40,
                                cmd_set=cmd_set,cmd_id=cmd_id,payload=payload)


def focus_point(x,y,widget_width,widget_height,video_width,video_height):
    """Map only the aspect-fit picture; letterboxes are deliberately not focusable."""
    if min(widget_width,widget_height,video_width,video_height)<=0:return None
    scale=min(widget_width/video_width,widget_height/video_height)
    width,height=video_width*scale,video_height*scale
    left,top=(widget_width-width)/2,(widget_height-height)/2
    if not left<=x<=left+width or not top<=y<=top+height:return None
    return (x-left)/width,(y-top)/height
