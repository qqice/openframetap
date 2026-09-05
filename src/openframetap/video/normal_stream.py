"""Bounded online AVC fragment assembly and MPP/Wayland appsrc presentation."""

from collections import Counter
import time

from openframetap.analysis.dji_wifi_media import annex_b_nal_types
from openframetap.protocol.dji_wifi_media import DjiWifiMediaFragment, DjiWifiMediaAccessUnitHeader


class OnlineMediaAssembler:
    def __init__(self):
        self.pending = {}
        self.finished = {}
        self.stats = Counter()

    def feed(self, data: bytes, now: float):
        fragment = DjiWifiMediaFragment.parse(data)
        self.stats['fragments'] += 1
        for key, item in list(self.pending.items()):
            if now - item['time'] > 0.5:
                del self.pending[key]
                self.stats['incomplete_dropped'] += 1
        self.finished = {k: t for k, t in self.finished.items() if now - t < 2}
        key = fragment.frame_id
        if key in self.finished:
            self.stats['late_duplicates'] += 1
            return None
        item = self.pending.setdefault(key, dict(time=now, count=fragment.fragment_count, parts={}))
        if item['count'] != fragment.fragment_count:
            self.stats['conflicts'] += 1
            del self.pending[key]
            self.finished[key] = now
            return None
        parts = item['parts']
        if fragment.fragment_index in parts:
            if parts[fragment.fragment_index] != fragment.payload:
                self.stats['conflicts'] += 1
                del self.pending[key]
                self.finished[key] = now
            else:
                self.stats['duplicates'] += 1
            return None
        parts[fragment.fragment_index] = fragment.payload
        if len(parts) != item['count']:
            return None
        del self.pending[key]
        self.finished[key] = now
        raw = b''.join(parts[n] for n in range(item['count']))
        try:
            header = DjiWifiMediaAccessUnitHeader.parse(raw)
        except ValueError:
            self.stats['invalid_header'] += 1
            return None
        if len(raw) - 16 != header.declared_length:
            self.stats['length_mismatch'] += 1
            return None
        self.stats['access_units'] += 1
        return raw[16:], header.timestamp_ms_candidate


class NormalVideoPlayer:
    def __init__(self):
        self.pipeline = None
        self.stats = dict(decoder='mppvideodec', pushed_units=0, rendered_frames=0)
        self.started = None
        self.sps_seen = False
        self.pps_seen = False
        self.picture_ready = False
        self.overview_initial = False
        self.environment = None

    def start(self):
        import os
        from openframetap.display.session import discover_active_wayland_session, gnome_overview_active, set_gnome_overview_active
        from openframetap.video.gst_player import _load_gstreamer
        self.environment = discover_active_wayland_session().environment()
        os.environ.update(self.environment)
        self.overview_initial = gnome_overview_active(self.environment)
        if self.overview_initial:
            set_gnome_overview_active(False, self.environment)
        self.Gst, _ = _load_gstreamer()
        self.pipeline = self.Gst.parse_launch(
            'appsrc name=source is-live=true format=time do-timestamp=true block=false '
            'max-bytes=4194304 caps="video/x-h264,stream-format=byte-stream" ! '
            'h264parse config-interval=-1 ! video/x-h264,stream-format=byte-stream,alignment=au ! '
            'mppvideodec name=normal_decoder ! '
            'fpsdisplaysink name=normal_fps text-overlay=false sync=false '
            'video-sink="waylandsink sync=false fullscreen=false"')
        self.source = self.pipeline.get_by_name('source')
        self.fps = self.pipeline.get_by_name('normal_fps')
        self.sink = self.fps.get_property('video-sink')
        self.bus = self.pipeline.get_bus()
        if self.pipeline.set_state(self.Gst.State.PLAYING) == self.Gst.StateChangeReturn.FAILURE:
            raise RuntimeError('normal MPP pipeline failed to enter PLAYING')
        self.started = time.monotonic()

    def push(self, raw: bytes):
        nals = annex_b_nal_types(raw)
        if 7 in nals:
            self.sps_seen = True
        if 8 in nals:
            self.pps_seen = True
        if 5 in nals and self.sps_seen and self.pps_seen:
            self.picture_ready = True
        if not self.picture_ready and not (7 in nals or 8 in nals):
            return
        if self.source.get_property('current-level-bytes') > 4194304:
            raise RuntimeError('normal MPP input is stalled; bounded queue exceeded')
        buf = self.Gst.Buffer.new_allocate(None, len(raw), None)
        buf.fill(0, raw)
        if self.source.emit('push-buffer', buf) != self.Gst.FlowReturn.OK:
            raise RuntimeError('normal MPP appsrc rejected access unit')
        self.stats['pushed_units'] += 1

    def poll(self):
        message = self.bus.pop_filtered(self.Gst.MessageType.ERROR | self.Gst.MessageType.EOS)
        if message:
            raise RuntimeError('normal MPP pipeline stopped: ' + str(message.type))
        rendered = self.fps.get_property('frames-rendered')
        self.stats['rendered_frames'] = rendered
        self.stats['dropped_frames'] = self.fps.get_property('frames-dropped')
        if rendered:
            self.sink.set_property('fullscreen', True)
        self.stats['fullscreen'] = bool(self.sink.get_property('fullscreen'))

    def close(self):
        try:
            if self.pipeline:
                try:
                    self.poll_final()
                finally:
                    self.pipeline.set_state(self.Gst.State.NULL)
                    self.pipeline = None
        finally:
            if self.overview_initial:
                from openframetap.display.session import set_gnome_overview_active
                set_gnome_overview_active(True, self.environment)
                self.overview_initial = False

    def poll_final(self):
        self.stats['rendered_frames'] = self.fps.get_property('frames-rendered')
        self.stats['dropped_frames'] = self.fps.get_property('frames-dropped')
