"""Bridge the normal-mode network owner to the existing GTK video and stick UI."""

import asyncio
from pathlib import Path
import struct
import threading
import time

from openframetap.app.input import ControlInput
from openframetap.control.live_wifi import LiveWifiControlSession
from openframetap.video.normal_stream import NormalVideoPlayer
from openframetap.video.pipelines import PipelineSpec


def normal_gui_spec():
    tokens = ('appsrc', 'name=normal_source', 'is-live=true', 'format=time',
              'do-timestamp=true', 'block=false', 'max-bytes=4194304',
              'caps=video/x-h264,stream-format=byte-stream', '!',
              'h264parse', 'config-interval=-1', '!',
              'video/x-h264,stream-format=byte-stream,alignment=au', '!',
              'mppvideodec', 'name=app_decoder', '!', 'fpsdisplaysink',
              'name=app_fps_sink', 'text-overlay=false', 'sync=false',
              'video-sink=gtkwaylandsink name=video_sink sync=false')
    return PipelineSpec('normal', 'mppvideodec', 'gtkwayland', 'low-latency',
                        ('appsrc','h264parse','mppvideodec','gtkwaylandsink'),
                        ('gst-launch-1.0','-e',*tokens), fullscreen=True)


def update_telemetry(state, frame, now):
    if not (frame.crc8_valid and frame.crc16_valid):
        return
    if (frame.cmd_set,frame.cmd_id) == (13,2) and len(frame.payload)>20 and frame.payload[20]<=100:
        state.update(battery_percent=frame.payload[20], battery_provenance={
            'source_cmd_set':13, 'source_cmd_id':2, 'source_offset':20,
            'raw_value':frame.payload[20], 'confidence':'high', 'last_updated':now})
    elif (frame.cmd_set,frame.cmd_id) == (4,5) and len(frame.payload)>=24:
        state.update(gimbal_yaw_raw_candidate=str(struct.unpack_from('<h',frame.payload,16)[0]),
                     gimbal_pitch_raw_candidate=str(struct.unpack_from('<h',frame.payload,20)[0]),
                     gimbal_roll_raw_candidate=str(struct.unpack_from('<h',frame.payload,22)[0]))


class EmbeddedNormalPlayer(NormalVideoPlayer):
    def __init__(self, pipeline, Gst):
        super().__init__()
        self.Gst = Gst
        self.source = pipeline.get_by_name('normal_source')
        self.fps = pipeline.get_by_name('app_fps_sink')
        if self.source is None or self.fps is None:
            raise RuntimeError('normal GUI has no appsrc / FPS sink')

    def start(self):
        self.started = time.monotonic()

    def poll(self):
        self.poll_final()

    def close(self):
        # GTK owns pipeline lifecycle and the compositor surface.
        self.poll_final()


class NormalGuiSession:
    def __init__(self, state, address, output, *, control_enabled, event_handler,
                 datagram_handler, transition_handler, minimum_offset=32, maximum_offset=188,
                 leave_livestream=False):
        self.state, self.address, self.output = state, address, Path(output)
        self.control_enabled = control_enabled
        self.leave_livestream = leave_livestream
        self.event_handler = event_handler
        self.control = LiveWifiControlSession(state, event_handler=event_handler,
            datagram_handler=datagram_handler, transition_handler=transition_handler,
            minimum_offset=minimum_offset, maximum_offset=maximum_offset)
        self._stop = threading.Event()
        self._thread = None
        self._loop = None
        self._task = None
        self.summary = {}

    def snapshot(self):
        return self.control.snapshot()

    def submit(self, value):
        if self.control_enabled:
            self.control.submit(value)

    def rearm(self):
        if self.control_enabled and self.snapshot()['state']=='disabled' and self._thread and self._thread.is_alive():
            self.control.rearm_requested.set()

    def start(self, pipeline, Gst, seconds):
        from openframetap.workflows.pocket3_normal import run_normal_session
        player = EmbeddedNormalPlayer(pipeline, Gst)
        self.control._set(state='connecting')
        self.state.update(connection_stage='reading_credentials')
        def main():
            async def run():
                self._loop = asyncio.get_running_loop()
                self._task = asyncio.current_task()
                try:
                    from openframetap.transport.dji_wifi_udp import list_rtmp_server_peer_ips
                    self.summary = await run_normal_session(self.address, seconds=seconds,
                        output=self.output, display=False, managed=True, player_override=player,
                        state_store=self.state, control_session=self.control if self.control_enabled else None,
                        stop_event=self._stop,
                        leave_livestream=self.leave_livestream or bool(list_rtmp_server_peer_ips()))
                    if self.summary.get('error'):
                        raise RuntimeError(self.summary['error'])
                    if not self.summary.get('network_restored'):
                        raise RuntimeError('normal network rollback incomplete')
                except Exception as exc:
                    self.summary['error'] = str(exc)
                    self.state.update(media_error=str(exc), connection_stage='fault')
                    self.control._set(state='fault', fault=str(exc), yaw=0.,pitch=0.)
                    self.event_handler(dict(kind='normal_gui_error',error=str(exc)))
                finally:
                    self.state.update(ble_connected=False, normal_video_online=False)
            asyncio.run(run())
        self._thread = threading.Thread(target=main,name='normal-gui-session',daemon=False)
        self._thread.start()

    def stop(self, reason='mode_switch', timeout=55):
        if not self._thread or not self._thread.is_alive():
            return
        self._stop.set()
        self.control.submit(ControlInput(emergency_stop=True, exit_requested=True,
                             source=reason, monotonic_ns=time.monotonic_ns()))
        # Cancellation wakes BLE/DHCP waits; coroutine finally centers while UDP is alive.
        if self._loop and self._task:
            self._loop.call_soon_threadsafe(self._task.cancel)
        self._thread.join(timeout)
        if self._thread.is_alive():
            raise TimeoutError('normal mode has not completed network rollback')
