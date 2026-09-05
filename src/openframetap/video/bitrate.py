"""Encoded-video bitrate, independent of NIC or local RTMP relay routing."""
import threading


class EncodedBitrate:
    def __init__(self):
        self.total_bytes=0
        self.previous=None
        self.lock=threading.Lock()

    def record(self,size):
        if size<0:
            raise ValueError('negative buffer size')
        with self.lock:
            self.total_bytes+=size

    def sample(self,now_ns):
        with self.lock:
            total=self.total_bytes
        previous=self.previous
        self.previous=(now_ns,total)
        if previous is None or now_ns<=previous[0]:
            return None
        return max(0,total-previous[1])*8e9/(now_ns-previous[0])
