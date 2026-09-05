from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import signal
import subprocess
import sys
import time
from typing import Any

from openframetap.app.artifacts import JsonlWriter, write_manifest
from openframetap.app.ble_status import ReadOnlyBleMonitor
from openframetap.app.input import ControlInput, JoystickConfig, KeyboardInput, TouchJoystickInput
from openframetap.app.state import AppStateSnapshot, StateStore
from openframetap.control.safety import (
    ControlPrerequisites,
    ControlState,
    FailClosedController,
    MockCommandSink,
)
from openframetap.control.live_wifi import LiveWifiControlSession
from openframetap.display.session import (
    discover_active_wayland_session,
    gnome_overview_active,
    set_gnome_overview_active,
)
from openframetap.network.secrets import require_private_directory
from openframetap.video.metrics import ProcessMetrics, summarize_metrics
from openframetap.video.player_process import ProcessRegistry


def _load_gtk_gst() -> tuple[Any, Any, Any, Any]:
    system_packages = "/usr/lib/python3/dist-packages"
    if system_packages not in sys.path and Path(system_packages).is_dir():
        sys.path.append(system_packages)
    try:
        import gi
    except ImportError as exc:
        raise RuntimeError("PyGObject is unavailable to the app runtime") from exc
    gi.require_version("Gtk", "3.0")
    gi.require_version("Gdk", "3.0")
    gi.require_version("Gst", "1.0")
    from gi.repository import Gdk, GLib, Gst, Gtk

    Gst.init(None)
    return Gtk, Gdk, Gst, GLib


def _redacted_spec(spec) -> dict:
    payload = spec.to_dict()
    payload["argv"] = [
        "location=<redacted>" if item.startswith("location=") else item
        for item in spec.argv
    ]
    return payload


def _parse_caps(caps: Any) -> tuple[int | None, int | None, str | None]:
    if caps is None or caps.get_size() == 0:
        return None, None, None
    structure = caps.get_structure(0)
    try:
        width = int(structure.get_value("width"))
        height = int(structure.get_value("height"))
    except (TypeError, ValueError):
        width = height = None
    return width, height, caps.to_string()


def _screenshot_allowed(control_mode: str) -> bool:
    """Avoid focus/touch disruption from desktop capture during live control."""

    return control_mode != "live"


class GtkReadOnlyApp:
    def __init__(
        self,
        spec,
        *,
        address: str,
        private_output: Path,
        sanitized_output: Path,
        duration_seconds: int,
        enable_ble: bool,
        control_mode: str = "disabled",
        session_mode: str = "livestream",
        artifact_root: Path | None = None,
        leave_livestream: bool = False,
        window_host=None,
        stream_resolution=720,
        joystick_config_path: Path = Path("config/control-ui.json"),
        registry_path: Path = Path("runtime/media-processes.json"),
    ) -> None:
        if not 1 <= duration_seconds <= 86400:
            raise ValueError("app duration must be 1..86400 seconds")
        self.spec = spec
        self.address = address
        self.private_output = require_private_directory(private_output)
        self.sanitized_output = sanitized_output.resolve()
        if "sanitized" not in {part.lower() for part in self.sanitized_output.parts}:
            raise ValueError("sanitized app output must be under artifacts/sanitized")
        self.sanitized_output.mkdir(parents=True, exist_ok=True)
        self.duration_seconds = duration_seconds
        self.enable_ble = enable_ble
        if control_mode not in {"disabled", "mock", "live"}:
            raise ValueError("GTK runtime control mode is invalid")
        self.control_mode = control_mode
        if session_mode not in {'normal','livestream'}:
            raise ValueError('invalid camera session mode')
        self.session_mode = session_mode
        self.artifact_root = artifact_root or self.private_output
        self.normal_session = None
        self.window_host=window_host
        self.stream_resolution=stream_resolution
        self.focus_mark=None
        self.focus_mark_until=0.
        self.window_handlers=[]
        self.content_shown=False
        from openframetap.video.bitrate import EncodedBitrate
        self.video_bitrate=EncodedBitrate()
        self.video_bitrate_samples=[]
        self.registry = ProcessRegistry(registry_path)
        self.state = StateStore()
        self.state.update(session_mode=session_mode)
        self.events = JsonlWriter(self.private_output / "events.jsonl")
        self.states = JsonlWriter(self.private_output / "state-snapshots.jsonl")
        self.metrics_writer = JsonlWriter(self.private_output / "media-metrics.jsonl")
        self.metric_samples = []
        self.ble: ReadOnlyBleMonitor | None = None
        self.pipeline = None
        self.window = None
        self.loop = None
        self.stop_reason = "unknown"
        self.started_monotonic_ns = time.monotonic_ns()
        self.last_metric_ns = 0
        self.last_snapshot_ns = 0
        self.last_network: tuple[int, int] | None = None
        self.last_render_sample: tuple[int, int] | None = None
        self.revision = -1
        self.labels: dict[str, Any] = {}
        self.screenshot = {"attempted": False, "saved": False, "error": None}
        self.control = None
        self.live_control = None
        self.mock_sink = None
        self.keyboard = None
        self.touch = None
        self.joystick_widget = None
        self.joystick_gesture = None
        self.joystick_popover = None
        self.joystick_drag_origin = None
        self.input_events = None
        self.sent_commands = None
        self.control_states = None
        self._mock_records_written = 0
        self.latest_ui_input = None
        if control_mode in {"mock", "live"}:
            joystick = JoystickConfig.load(joystick_config_path)
            self.keyboard = KeyboardInput(maximum_output=joystick.keyboard_output)
            self.touch = TouchJoystickInput(joystick)
            self.input_events = JsonlWriter(self.private_output / "input-events.jsonl")
            self.sent_commands = JsonlWriter(self.private_output / "sent-commands.jsonl")
            self.control_states = JsonlWriter(self.private_output / "control-state.jsonl")
        if control_mode == "mock":
            self.mock_sink = MockCommandSink()
            self.control = FailClosedController(
                self.mock_sink,
                on_transition=self.control_states.write,
                live=False,
            )
            self.control.arm(ControlPrerequisites(True, True, True, True, True, True, True))
        elif control_mode == "live":
            self.live_control = LiveWifiControlSession(
                self.state,
                event_handler=self._event,
                datagram_handler=self.sent_commands.write,
                transition_handler=self.control_states.write,
                minimum_offset=joystick.protocol_offset_min,
                maximum_offset=joystick.protocol_offset_max,
            )
        if session_mode == 'normal':
            from openframetap.app.normal_session import NormalGuiSession
            self.normal_session = NormalGuiSession(self.state, address, self.private_output/'normal',
                control_enabled=control_mode == 'live', event_handler=self._event,
                datagram_handler=self.sent_commands.write if self.sent_commands else self._event,
                transition_handler=self.control_states.write if self.control_states else self._event,
                minimum_offset=self.touch.config.protocol_offset_min if self.touch else 32,
                maximum_offset=self.touch.config.protocol_offset_max if self.touch else 188,
                leave_livestream=leave_livestream)
            self.live_control = self.normal_session if control_mode == 'live' else None

    def _event(self, payload: dict) -> None:
        self.events.write(
            {
                "wall_time_utc": datetime.now(timezone.utc).isoformat(),
                "monotonic_ns": time.monotonic_ns(),
                **payload,
            }
        )

    def _request_stop(self, reason: str) -> None:
        if self.stop_reason != "unknown":
            return
        self.stop_reason = reason
        self.latest_ui_input=ControlInput(source='shutdown',monotonic_ns=time.monotonic_ns())
        if self.window_host:
            if not reason.startswith(('mode_switch:','quality_switch:')):
                self.window_host.exit_requested=True
            if self.joystick_popover:
                self.joystick_popover.hide()
            self.window_host.show_transition('正在归零并关闭当前连接…')
        self._event({"kind": "app_stop_requested", "reason": reason})
        if self.control and self.control.state not in {
            ControlState.DISABLED,
            ControlState.FAULT,
            ControlState.DISCONNECTED,
        }:
            try:
                import asyncio

                asyncio.run(self.control.stop(f"app_exit:{reason}"))
                self._flush_mock_records()
            except Exception as exc:
                self._event({"kind": "control_stop_error", "error": str(exc)})
        if self.live_control:
            try:
                if self.window_host:
                    self.window_host.wait(lambda:self.live_control.stop(f'app_exit:{reason}'))
                else:
                    self.live_control.stop(f"app_exit:{reason}")
            except Exception as exc:
                self._event({"kind": "live_control_stop_error", "error": str(exc)})
        if self.loop is not None:
            self.loop.quit()

    def _flush_mock_records(self) -> None:
        if not self.mock_sink or not self.sent_commands:
            return
        for record in self.mock_sink.records[self._mock_records_written :]:
            self.sent_commands.write(record)
        self._mock_records_written = len(self.mock_sink.records)

    def _submit_control(self, value) -> None:
        if not self.control and not self.live_control:
            return
        self.latest_ui_input = value
        if self.input_events:
            self.input_events.write(value.to_dict())
        if self.live_control:
            self.live_control.submit(value)
            if value.exit_requested:
                self._request_stop("keyboard_exit")
            if self.joystick_widget:
                self.joystick_widget.queue_draw()
            return
        if value.emergency_stop:
            import asyncio

            asyncio.run(self.control.emergency_stop("ui_emergency_stop"))
        elif value.exit_requested:
            import asyncio

            asyncio.run(self.control.emergency_stop("ui_exit"))
            self._request_stop("keyboard_exit")
        else:
            self.control.submit(value)
        self._flush_mock_records()
        if self.joystick_widget:
            self.joystick_widget.queue_draw()

    def _build_window(self, Gtk, Gdk, Gst) -> None:
        from gi.repository import Pango

        tokens = [item for item in self.spec.argv[1:] if item not in {"-e", "-v"}]
        self.pipeline = Gst.parse_launchv(tokens)
        fps_sink = self.pipeline.get_by_name("app_fps_sink")
        decoder = self.pipeline.get_by_name("app_decoder")
        if fps_sink is None or decoder is None:
            raise RuntimeError("GTK app pipeline lacks named fps sink or decoder")
        video_sink = fps_sink.get_property("video-sink")
        if video_sink is None or video_sink.get_factory().get_name() != "gtkwaylandsink":
            raise RuntimeError("GTK app did not instantiate gtkwaylandsink")
        widget = video_sink.get_property("widget")
        if widget is None:
            raise RuntimeError("gtkwaylandsink did not expose its widget")

        window = Gtk.Window(title="OpenFrameTap")
        window.set_default_size(1280, 720)
        window.set_decorated(False)
        window.fullscreen()
        self.window_handlers.append(window.connect("delete-event", lambda *_args: (self._request_stop("window_close"), True)[1]))
        self.window_handlers.append(window.connect("focus-out-event", self._on_focus_lost))

        widget.set_hexpand(True)
        widget.set_vexpand(True)
        root = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)

        header = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=14)
        header.set_name("status-bar")
        header.set_size_request(-1, 54)
        for key in ("device", "ble", "pairing", "rtmp", "media", "video", "battery", "rock", "age"):
            label = Gtk.Label(label=key)
            label.set_xalign(0.0)
            if key=='media':
                label.set_max_width_chars(16)
                label.set_ellipsize(Pango.EllipsizeMode.END)
            header.pack_start(label, key in {"device", "media"}, key in {"device", "media"}, 8)
            self.labels[key] = label
        root.pack_start(header, False, False, 0)
        root.pack_start(widget, True, True, 0)

        controls = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        controls.set_name("button-bar")
        controls.set_size_request(-1, 72)
        controls.set_margin_start(18)
        controls.set_margin_end(18)
        controls.set_margin_top(8)
        controls.set_margin_bottom(8)
        def change_mode(combo):
            mode = combo.get_active_id()
            if mode and mode != self.session_mode:
                combo.set_sensitive(False)
                self.state.update(connection_stage='switching')
                self._request_stop('mode_switch:'+mode)
        from openframetap.app.widgets import binary_mode_switch
        selector=binary_mode_switch(Gtk,Gdk,self.bindings[3],self.session_mode,change_mode)
        self.mode_selector = selector
        controls.pack_start(selector,False,False,0)
        if self.control_mode in {"mock", "live"}:
            joystick = Gtk.DrawingArea()
            joystick.set_size_request(
                self.touch.config.overlay_size,
                self.touch.config.overlay_size,
            )
            joystick.connect("draw", self._draw_joystick)
            gesture = Gtk.GestureDrag.new(joystick)
            gesture.set_touch_only(False)
            gesture.connect("drag-begin", self._on_joystick_drag_begin)
            gesture.connect("drag-update", self._on_joystick_drag_update)
            gesture.connect("drag-end", self._on_joystick_drag_end)
            gesture.connect("cancel", self._on_joystick_drag_cancel)
            self.joystick_gesture = gesture
            self.joystick_widget = joystick
            popover = Gtk.Popover.new(widget)
            popover.set_name("joystick-popover")
            popover.set_modal(False)
            popover.set_transitions_enabled(False)
            popover.set_position(Gtk.PositionType.TOP)
            popover.add(joystick)
            self.joystick_popover = popover

            def place_joystick(_widget, allocation) -> None:
                rectangle = Gdk.Rectangle()
                rectangle.x = 18 + self.touch.config.overlay_size // 2
                rectangle.y = max(1, allocation.height - 4)
                rectangle.width = 1
                rectangle.height = 1
                popover.set_pointing_to(rectangle)

            widget.connect("size-allocate", place_joystick)
            mode = Gtk.Label(
                label="MOCK · CONTROL ARMED" if self.control_mode == "mock" else "LIVE · CONNECTING"
            )
            mode.set_name("control-mock" if self.control_mode == "mock" else "control-live")
            mode.set_size_request(360, 48)
            mode.set_xalign(0.0)
            mode.set_ellipsize(Pango.EllipsizeMode.END)
            controls.pack_start(mode, True, True, 0)
            self.labels["control"] = mode
            for label,action in [('回中','recenter'),('180°','flip')]:
                button=Gtk.Button(label=label)
                button.set_size_request(92,56)
                button.connect('clicked',lambda _,name=action:self._camera_action(name))
                controls.pack_end(button,False,False,0)
        else:
            disabled = Gtk.Label(label="JOYSTICK DISABLED · READ-ONLY")
            disabled.set_name("control-disabled")
            controls.pack_start(disabled, True, True, 0)
        exit_button = Gtk.Button(label="退出  Esc / Q")
        exit_button.connect("clicked", lambda *_args: self._request_stop("exit_button"))
        controls.pack_end(exit_button, False, False, 0)
        quality=Gtk.Button(label='图传')
        quality.set_size_request(92,56)
        quality.connect('clicked',self._show_formats)
        controls.pack_end(quality,False,False,0)
        root.pack_start(controls, False, False, 0)
        if self.window_host:
            self.window_host.mount(root)
            self.window_host.content_window=window
        window.add(root)

        css = Gtk.CssProvider()
        css.load_from_data(
            b"window { background: #000; color: #fff; } #status-bar, #button-bar { background: rgba(0,0,0,0.78); } "
            b"#joystick-popover { background: rgba(0,0,0,0.10); border: 0; box-shadow: none; padding: 0; } "
            b"label { color: #fff; font-size: 15px; } #control-disabled { color: #ffcc33; font-size: 20px; } "
            b"#control-mock, #control-live { background: rgba(0,0,0,0.58); border-radius: 8px; } "
            b"#control-mock { color: #55ddff; font-size: 20px; } #control-live { color: #66ff88; font-size: 20px; } #emergency-stop { background: #b00020; color: white; } "
            b"button { background-image:none; background-color:#253443; border:1px solid #466074; border-radius:18px; font-size:18px; padding:10px 18px; } "
            b"button:active,button:checked {background-color:#087d9a;} button label {color:#f2f7fc;} "
            b"#button-bar button label { color:#f2f7fc; } popover {background:#14202c;}"
        )
        Gtk.StyleContext.add_provider_for_screen(
            Gdk.Screen.get_default(), css, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION
        )
        self.window_handlers.append(window.connect("key-press-event", self._on_key))
        self.window_handlers.append(window.connect("key-release-event", self._on_key_release))
        self.window = window
        self.fps_sink = fps_sink
        self.decoder = decoder
        self.video_sink = video_sink
        self.video_widget=widget
        tap=Gtk.GestureMultiPress.new(widget)
        tap.set_touch_only(False)
        tap.connect('released',self._on_video_tap)
        tap.connect('pressed',lambda _,count,x,y:setattr(self,'video_press',(x,y,time.monotonic())))
        self.video_tap=tap
        self.bitrate_pad=decoder.get_static_pad('sink')
        def count_encoded(_pad,info):
            if info.type & Gst.PadProbeType.BUFFER:
                buffer=info.get_buffer()
                if buffer:
                    self.video_bitrate.record(buffer.get_size())
            elif info.type & Gst.PadProbeType.BUFFER_LIST:
                buffers=info.get_buffer_list()
                if buffers:
                    for i in range(buffers.length()):
                        self.video_bitrate.record(buffers.get(i).get_size())
            return Gst.PadProbeReturn.OK
        self.bitrate_probe=self.bitrate_pad.add_probe(Gst.PadProbeType.BUFFER | Gst.PadProbeType.BUFFER_LIST,count_encoded)

    def _on_key(self, _widget, event) -> bool:
        if (hasattr(self, 'mode_selector') and self.mode_selector.has_focus()
                and event.keyval in (65361,65363)):
            return False
        name = event.string.lower() if event.string else ""
        key_name = self.bindings[1].keyval_name(event.keyval).lower()
        if self.keyboard:
            self._submit_control(self.keyboard.key_down(key_name))
            return True
        if name == "q" or event.keyval == 65307:
            self._request_stop("keyboard_exit")
            return True
        return False

    def _on_key_release(self, _widget, event) -> bool:
        if not self.keyboard:
            return False
        key_name = self.bindings[1].keyval_name(event.keyval).lower()
        self._submit_control(self.keyboard.key_up(key_name))
        return True

    def _on_focus_lost(self, *_args) -> bool:
        if self.stop_reason != 'unknown' or (self.window_host and not self.content_shown):
            return False
        self._event({"kind": "window_focus_lost"})
        if self.keyboard:
            self.keyboard.reset()
        if self.touch:
            self.touch.touch_cancel()
        if self.control:
            import asyncio

            asyncio.run(self.control.focus_lost())
            self._flush_mock_records()
        elif self.live_control:
            value = ControlInput(
                emergency_stop=True,
                source="window_focus_lost",
                monotonic_ns=time.monotonic_ns(),
            )
            self.latest_ui_input = value
            self.live_control.submit(value)
        return False

    def _control_emergency(self) -> None:
        if self.control:
            import asyncio

            asyncio.run(self.control.emergency_stop("screen_stop"))
            self._flush_mock_records()
        elif self.live_control:
            value = ControlInput(
                emergency_stop=True,
                source="screen_stop",
                monotonic_ns=time.monotonic_ns(),
            )
            self.latest_ui_input = value
            self.live_control.submit(value)

    def _control_arm(self):
        if not self.live_control or self.live_control.snapshot()['state'] != 'disabled':
            return
        self.keyboard.reset()
        self.touch.touch_cancel()
        self.latest_ui_input = ControlInput(source='manual_arm',monotonic_ns=time.monotonic_ns())
        self.live_control.rearm()

    def _camera_action(self,name,x=0.5,y=0.5):
        from openframetap.protocol.camera_actions import CameraAction
        if self.keyboard:self.keyboard.reset()
        if self.touch:self.touch.touch_cancel()
        self._submit_control(ControlInput(source='camera_action',monotonic_ns=time.monotonic_ns()))
        action=CameraAction(name,x,y)
        if self.live_control:
            accepted=self.live_control.request_action(action)
            self.state.update(action_status='正在执行…' if accepted else '请等待当前操作完成')
        else:
            self._event(dict(kind='mock_camera_action',name=name,x=x,y=y))
            self.state.update(action_status='模拟操作')

    def _on_video_tap(self,gesture,count,x,y):
        if count!=1 or not self.content_shown and self.window_host:return
        import math
        start=getattr(self,'video_press',None)
        if not start or time.monotonic()-start[2]>0.6 or math.hypot(x-start[0],y-start[1])>12:return
        from openframetap.protocol.camera_actions import focus_point
        _,s=self.state.snapshot()
        allocation=self.video_widget.get_allocation()
        if self.touch and x<self.touch.config.overlay_size+40 and y>allocation.height-self.touch.config.overlay_size-25:return
        point=focus_point(x,y,allocation.width,allocation.height,s.video_width or 0,s.video_height or 0)
        if point is None:return
        self._camera_action('focus',*point)
        Gtk,Gdk,_,_=self.bindings
        if self.focus_mark:self.focus_mark.destroy()
        # Separate popup surface is required above gtkwaylandsink's native surface.
        mark=Gtk.Popover.new(self.video_widget);mark.set_modal(False)
        label=Gtk.Label(label='＋');label.set_size_request(40,40);mark.add(label)
        rectangle=Gdk.Rectangle();rectangle.x=int(x);rectangle.y=int(y);rectangle.width=1;rectangle.height=1
        mark.set_pointing_to(rectangle);mark.set_position(Gtk.PositionType.TOP)
        mark.show_all();mark.popup()
        self.focus_mark=mark;self.focus_mark_until=time.monotonic()+0.8

    def _show_formats(self,button):
        Gtk,_,_,_=self.bindings
        popup=Gtk.Popover.new(button)
        box=Gtk.Box(orientation=Gtk.Orientation.VERTICAL,spacing=14)
        box.set_border_width(18)
        _,s=self.state.snapshot()
        text=f'当前图传 H.264 · {s.video_width or 0} × {s.video_height or 0}'
        box.pack_start(Gtk.Label(label=text),False,False,0)
        if self.session_mode=='livestream':
            row=Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL,spacing=8)
            selected_height=s.video_height or self.stream_resolution
            for height in (480,720,1080):
                choice=Gtk.ToggleButton(label=f'{height}p')
                choice.set_size_request(100,56);choice.set_active(height==selected_height)
                def select(_,value=height):
                    popup.popdown()
                    if value!=selected_height:self._request_stop(f'quality_switch:{value}')
                choice.connect('clicked',select);row.pack_start(choice,False,False,0)
            box.pack_start(row,False,False,0)
            box.pack_start(Gtk.Label(label='H.264 / 30 fps · 切换时短暂重新连接'),False,False,0)
        else:
            box.pack_start(Gtk.Label(label='当前已验证：H.264 720p\n更多图传编码与分辨率尚无已验证查询协议'),False,False,0)
        box.pack_start(Gtk.Label(label='HEVC / MJPEG 不作为未经验证的选项开放'),False,False,0)
        query=Gtk.Button(label='读取录像规格（非图传格式）')
        query.connect('clicked',lambda *_:self._camera_action('query_formats'))
        box.pack_start(query,False,False,0)
        if s.recording_capability_raw:
            from openframetap.video.formats import parse_recording_capability
            entries=parse_recording_capability(bytes.fromhex(s.recording_capability_raw))
            detail=f'收到 {len(entries)} 档录像规格；不据此切换图传' if entries else '未解析的录像规格回复已保存'
        else:
            detail='尚未收到录像规格表；图传选项以实机验证为准'
        box.pack_start(Gtk.Label(label=detail),False,False,0)
        popup.add(box);popup.show_all();popup.popup()
        self.format_popup=popup

    def _mock_emergency(self) -> None:
        """Compatibility alias retained for existing mock-control tests."""
        self._control_emergency()

    def _local_touch_config(self):
        allocation = self.joystick_widget.get_allocation()
        base = self.touch.config
        return replace(
            base,
            logical_width=allocation.width,
            logical_height=allocation.height,
            center_x=allocation.width / 2,
            center_y=allocation.height / 2,
            radius=min(allocation.width, allocation.height) * 0.42,
        )

    def _touch_value(self, action: str, touch_id, x: float = 0, y: float = 0):
        original = self.touch.config
        self.touch.config = self._local_touch_config()
        try:
            if action == "touch_up":
                return self.touch.touch_up(touch_id)
            return getattr(self.touch, action)(touch_id, x, y)
        finally:
            self.touch.config = original

    def _on_joystick_drag_begin(self, _gesture, start_x: float, start_y: float) -> None:
        self.joystick_drag_origin = (float(start_x), float(start_y))
        self._submit_control(
            self._touch_value("touch_down", "gtk-gesture-drag", start_x, start_y)
        )

    def _on_joystick_drag_update(self, _gesture, offset_x: float, offset_y: float) -> None:
        if self.joystick_drag_origin is None:
            return
        start_x, start_y = self.joystick_drag_origin
        self._submit_control(
            self._touch_value(
                "touch_move",
                "gtk-gesture-drag",
                start_x + float(offset_x),
                start_y + float(offset_y),
            )
        )

    def _on_joystick_drag_end(self, _gesture, _offset_x: float, _offset_y: float) -> None:
        self.joystick_drag_origin = None
        self._submit_control(self.touch.touch_up("gtk-gesture-drag"))

    def _on_joystick_drag_cancel(self, *_args) -> None:
        self.joystick_drag_origin = None
        self._submit_control(self.touch.touch_cancel("gtk-gesture-drag"))

    def _draw_joystick(self, widget, cairo) -> bool:
        allocation = widget.get_allocation()
        cx, cy = allocation.width / 2, allocation.height / 2
        radius = min(allocation.width, allocation.height) * 0.42
        cairo.set_source_rgba(0.0, 0.0, 0.0, 0.22)
        cairo.arc(cx, cy, radius, 0, 6.28319)
        cairo.fill()
        cairo.set_source_rgba(0.2, 0.7, 0.9, 0.35)
        cairo.set_line_width(3)
        cairo.arc(cx, cy, radius, 0, 6.28319)
        cairo.stroke()
        if self.touch:
            cairo.set_source_rgba(0.7, 0.9, 1.0, 0.24)
            cairo.set_line_width(2)
            cairo.arc(cx, cy, radius * self.touch.config.deadzone, 0, 6.28319)
            cairo.stroke()
        value = self.touch.current() if self.touch else None
        x = cx + (value.yaw * radius if value else 0)
        y = cy - (value.pitch * radius if value else 0)
        cairo.set_source_rgba(0.2, 0.8, 1.0, 0.9)
        cairo.arc(x, y, 28, 0, 6.28319)
        cairo.fill()
        return False

    def _update_labels(self, snapshot: AppStateSnapshot) -> None:
        self.labels["device"].set_text(snapshot.device_name)
        self.labels["ble"].set_text("BLE ●" if snapshot.ble_connected else "BLE ○")
        pairing = {
            "confirmed_previous_evidence": "EVIDENCE",
            "already_paired": "PAIRED",
            "confirmation_required": "CONFIRM",
        }.get(snapshot.pairing_state, snapshot.pairing_state)
        self.labels["pairing"].set_text(f"PAIR {pairing}")
        online = snapshot.normal_video_online if self.session_mode == 'normal' else snapshot.rtmp_publisher_online
        self.labels['rtmp'].set_text(('热点 UDP ' if self.session_mode == 'normal' else 'RTMP ') + ('●' if online else '○'))
        self.labels["media"].set_text(snapshot.action_status or f"MEDIA {snapshot.media_state.upper()}")
        resolution = (
            f"{snapshot.video_width}×{snapshot.video_height}" if snapshot.video_width else "—"
        )
        fps = f"{snapshot.actual_fps:.1f}fps" if snapshot.actual_fps is not None else "—fps"
        bitrate = (
            f"{snapshot.receive_bitrate_bps / 1_000_000:.1f}Mbps"
            if snapshot.receive_bitrate_bps is not None
            else "—Mbps"
        )
        self.labels["video"].set_text(f"{resolution} {fps} {bitrate} drop={snapshot.dropped_frames}")
        self.labels["battery"].set_text(
            f"{snapshot.battery_percent}%" if snapshot.battery_percent is not None else "BAT —"
        )
        cpu = f"{snapshot.rock_cpu_percent:.0f}%" if snapshot.rock_cpu_percent is not None else "—"
        temp = (
            f"{snapshot.rock_temperature_c:.1f}°C"
            if snapshot.rock_temperature_c is not None
            else "—°C"
        )
        self.labels["rock"].set_text(f"CPU {cpu} {temp}")
        age_ms = max(0, time.monotonic_ns() - snapshot.last_update_monotonic_ns) / 1e6
        self.labels["age"].set_text(f"state {age_ms:.0f}ms")

    def _tick(self) -> bool:
        if self.stop_reason != 'unknown':
            return False
        Gtk, _Gdk, Gst, _GLib = self.bindings
        now_ns = time.monotonic_ns()
        if self.focus_mark and time.monotonic()>self.focus_mark_until:
            self.focus_mark.destroy();self.focus_mark=None
        if self.control:
            import asyncio

            asyncio.run(self.control.tick())
            self._flush_mock_records()
            control = self.control.snapshot()
            last_age = (
                (now_ns - self.control.last_send_ns) / 1e6
                if self.control.last_send_ns is not None
                else None
            )
            self.state.update(
                control_state=control["state"].upper(),
                input_source=self.control.latest_input.source,
                yaw=self.control.latest_input.yaw,
                pitch=self.control.latest_input.pitch,
                last_command_age_ms=last_age,
                watchdog_state=self.control.watchdog_state,
                last_zero_command_monotonic_ns=self.control.last_zero_ns,
            )
            if "control" in self.labels:
                self.labels["control"].set_text(
                    f"MOCK · {control['state'].upper()}  "
                    f"Y {self.control.latest_input.yaw:+.2f}  "
                    f"P {self.control.latest_input.pitch:+.2f}"
                )
        elif self.live_control:
            # GUI heartbeat keeps a held touch/key valid. Release, focus loss,
            # STOP, disconnect and a missing heartbeat still center through
            # the independent watchdog; there is no arbitrary hold timer.
            if self.latest_ui_input and self.latest_ui_input.active:
                value = self.latest_ui_input
                refreshed = ControlInput(
                    yaw=value.yaw,
                    pitch=value.pitch,
                    source=value.source,
                    monotonic_ns=now_ns,
                    active=True,
                )
                self.latest_ui_input = refreshed
                self.live_control.submit(refreshed)
            live = self.live_control.snapshot()
            last_age = (
                (now_ns - live["last_command_ns"]) / 1e6
                if live.get("last_command_ns") is not None
                else None
            )
            self.state.update(
                control_state=str(live["state"]).upper(),
                input_source=(self.latest_ui_input.source if self.latest_ui_input else "none"),
                yaw=float(live["yaw"]),
                pitch=float(live["pitch"]),
                last_command_age_ms=last_age,
                watchdog_state=str(live["watchdog"]),
                last_zero_command_monotonic_ns=live.get("last_center_ns"),
            )
            if "control" in self.labels:
                self.labels["control"].set_text(
                    f"{'常规' if self.session_mode == 'normal' else '直播'} · {str(live['state']).upper()}  "
                    f"Y {float(live['yaw']):+.2f}  P {float(live['pitch']):+.2f}  "
                    f"SPEED {int(live['current_protocol_offset'])}"
                    + (' · '+self.state.snapshot()[1].action_status if self.state.snapshot()[1].action_status else '')
                )
        bus = self.pipeline.get_bus()
        while message := bus.pop_filtered(Gst.MessageType.ERROR | Gst.MessageType.EOS):
            if message.type == Gst.MessageType.ERROR:
                error, debug = message.parse_error()
                self.state.update(media_state="error", media_error=str(error))
                self._event({"kind": "gstreamer_error", "error": str(error), "debug": debug})
            else:
                self.state.update(media_state="stopped")
                self._event({"kind": "gstreamer_eos"})

        rendered = int(self.fps_sink.get_property("frames-rendered"))
        if self.window_host and not self.content_shown:
            if rendered>0 or self.video_bitrate.total_bytes>0:
                self.window_host.show_content()
                self.content_shown=True
                if self.joystick_popover:
                    self.joystick_popover.show_all()
                    self.joystick_popover.popup()
            else:
                stages={'reading_credentials':'正在读取相机热点信息…','joining_hotspot':'正在连接相机热点…',
                        'udp_handshake':'正在建立视频连接…','connected':'正在等待第一帧画面…',
                        'starting':'正在接收直播画面…','fault':'连接失败，请退出后查看日志'}
                snapshot=self.state.snapshot()[1]
                self.window_host.detail.set_text(snapshot.media_error or stages.get(snapshot.connection_stage,'正在连接相机…'))
        dropped = int(self.fps_sink.get_property("frames-dropped"))
        current_fps = None
        if self.last_render_sample and now_ns > self.last_render_sample[0]:
            current_fps = (rendered - self.last_render_sample[1]) * 1e9 / (
                now_ns - self.last_render_sample[0]
            )
        self.last_render_sample = (now_ns, rendered)
        caps = self.decoder.get_static_pad("src").get_current_caps()
        width, height, caps_text = _parse_caps(caps)
        self.state.update(
            media_state="running" if rendered > 0 else "starting",
            rtmp_publisher_online=rendered > 0 and self.session_mode == 'livestream',
            normal_video_online=rendered > 0 and self.session_mode == 'normal',
            rendered_frames=rendered,
            dropped_frames=dropped,
            actual_fps=current_fps,
            video_width=width,
            video_height=height,
        )
        if now_ns - self.last_metric_ns >= 1_000_000_000:
            self.last_metric_ns = now_ns
            sample = self.metrics.sample()
            self.metric_samples.append(sample)
            bitrate = self.video_bitrate.sample(now_ns)
            if bitrate is not None:
                self.video_bitrate_samples.append(bitrate)
            self.metrics_writer.write({**sample.to_dict(),'video_bitrate_bps':bitrate,
                                      'encoded_video_bytes':self.video_bitrate.total_bytes})
            self.state.update(
                rock_cpu_percent=sample.cpu_percent,
                rock_rss_bytes=sample.rss_bytes,
                rock_temperature_c=sample.temperature_c,
                receive_bitrate_bps=bitrate,
            )
            _, snapshot = self.state.snapshot()
            self.states.write(snapshot.to_dict())
            if caps_text:
                self.current_caps = caps_text
        changed = self.state.changed_since(self.revision)
        if changed:
            self.revision, snapshot = changed
            self._update_labels(snapshot)
        if now_ns - self.started_monotonic_ns >= self.duration_seconds * 1_000_000_000:
            self._request_stop("duration")
            return False
        return self.stop_reason == "unknown"

    def run(self) -> dict:
        session = discover_active_wayland_session()
        env = session.environment()
        os.environ.update(env)
        self.bindings = _load_gtk_gst()
        Gtk, _Gdk, Gst, GLib = self.bindings
        self._build_window(Gtk, _Gdk, Gst)
        self.loop = GLib.MainLoop()
        if self.window_host:
            self.window_host.exit_callback=lambda:self._request_stop('exit_button')
        self.metrics = ProcessMetrics(os.getpid())
        self.current_caps = None
        overview_before = False if self.window_host else gnome_overview_active(env)
        overview_hidden = False
        registry_item = self.registry.load().get('app')
        if registry_item is None or registry_item.pid != os.getpid():
            registry_item = self.registry.register_pid("app", os.getpid(), sys.argv)
        (Path("runtime") / "app-last-output.txt").write_text(
            str(self.artifact_root.resolve().relative_to(Path.cwd())) + "\n", encoding="utf-8"
        )
        config = {
            "generated_at_utc": datetime.now(timezone.utc).isoformat(),
            "address": self.address,
            "control_mode": self.control_mode,
            "session_mode": self.session_mode,
            "read_only": self.control_mode != "live" and self.session_mode != 'normal',
            "duration_seconds": self.duration_seconds,
            "pipeline": _redacted_spec(self.spec),
            "display_session": session.to_dict(),
            "owned_pid": registry_item.to_public_dict(),
            "joystick": self.touch.config.to_dict() if self.touch else None,
        }
        (self.private_output / "app-config.json").write_text(
            json.dumps(config, indent=2) + "\n", encoding="utf-8"
        )
        self._event(
            {
                "kind": "app_started",
                "read_only": self.control_mode != "live" and self.session_mode != 'normal',
                "control_mode": self.control_mode,
            }
        )
        if self.enable_ble and self.session_mode != 'normal':
            self.ble = ReadOnlyBleMonitor(self.address, self.state, event_handler=self._event)
            self.ble.start()
        if self.live_control and self.session_mode != 'normal':
            self.live_control.start()

        def signal_stop(signum, _frame) -> None:
            GLib.idle_add(self._request_stop, f"signal_{signum}")

        previous_handlers = {
            signum: signal.signal(signum, signal_stop)
            for signum in (signal.SIGINT, signal.SIGTERM)
        }
        error = None
        screenshot_path = self.private_output / "screenshots" / "app.png"
        try:
            if overview_before:
                set_gnome_overview_active(False, env)
                overview_hidden = True
            self.window.show_all()
            if self.window_host:
                self.window_host.window.present()
            if self.joystick_popover is not None and not self.window_host:
                self.joystick_popover.show_all()
                self.joystick_popover.popup()
            result = self.pipeline.set_state(Gst.State.PLAYING)
            if result == Gst.StateChangeReturn.FAILURE:
                raise RuntimeError("GTK app pipeline failed to enter PLAYING")
            self.state.update(media_state="starting")
            if self.normal_session:
                self.normal_session.start(self.pipeline, Gst, self.duration_seconds)
            tick_id = GLib.timeout_add(100, self._tick)

            def screenshot() -> bool:
                self.screenshot["attempted"] = True
                screenshot_path.parent.mkdir(parents=True, exist_ok=True)
                try:
                    shot = subprocess.run(
                        ["gnome-screenshot", "-f", str(screenshot_path)],
                        env=env,
                        text=True,
                        capture_output=True,
                        check=False,
                        timeout=10,
                    )
                    self.screenshot["saved"] = shot.returncode == 0 and screenshot_path.is_file()
                    if shot.returncode != 0:
                        self.screenshot["error"] = shot.stderr.strip() or f"exit {shot.returncode}"
                except Exception as exc:
                    self.screenshot["error"] = f"{type(exc).__name__}: {exc}"
                return False

            if _screenshot_allowed(self.control_mode):
                GLib.timeout_add_seconds(5, screenshot)
            else:
                self.screenshot["skipped_reason"] = "disabled_during_live_control"
            self.loop.run()
        except Exception as exc:
            error = f"{type(exc).__name__}: {exc}"
            self._event({"kind": "app_error", "error": error})
        finally:
            if 'tick_id' in locals():
                with __import__('contextlib').suppress(Exception):
                    if GLib.MainContext.default().find_source_by_id(tick_id):
                        GLib.source_remove(tick_id)
            if self.normal_session:
                try:
                    if self.window_host:
                        self.window_host.show_transition('正在关闭热点连接并恢复网络…')
                        self.window_host.wait(lambda:self.normal_session.stop('app_finally'))
                    else:
                        self.normal_session.stop('app_finally')
                    if self.normal_session.summary.get('network_cleanup_error'):
                        error = 'normal network rollback incomplete'
                except Exception as exc:
                    error = error or str(exc)
            if self.live_control:
                try:
                    if self.window_host:
                        self.window_host.wait(lambda:self.live_control.stop('app_finally'))
                    else:
                        self.live_control.stop("app_finally")
                except Exception as exc:
                    error = error or f"live control cleanup failed: {exc}"
            if self.control and self.control.state not in {
                ControlState.DISABLED,
                ControlState.FAULT,
                ControlState.DISCONNECTED,
            }:
                try:
                    import asyncio

                    asyncio.run(self.control.stop("app_finally"))
                    self._flush_mock_records()
                except Exception as exc:
                    error = error or f"control cleanup failed: {exc}"
            self.pipeline.set_state(Gst.State.NULL)
            self.bitrate_pad.remove_probe(self.bitrate_probe)
            if self.joystick_popover:
                self.joystick_popover.destroy()
            if self.focus_mark:self.focus_mark.destroy()
            if hasattr(self,'format_popup'):self.format_popup.destroy()
            for handler in self.window_handlers:
                self.window.disconnect(handler)
            if self.window_host:
                self.window_host.unmount()
                self.window_host.exit_callback=None
            self.window.destroy()
            if self.ble:
                try:
                    if self.window_host:
                        self.window_host.wait(self.ble.stop)
                    else:
                        self.ble.stop()
                except Exception as exc:
                    error = error or f"{type(exc).__name__}: {exc}"
            if overview_before:
                set_gnome_overview_active(True, env)
            if not self.window_host:
                self.registry.unregister("app")
            for signum, handler in previous_handlers.items():
                signal.signal(signum, handler)
            self.events.close()
            self.states.close()
            self.metrics_writer.close()
            for writer in (self.input_events, self.sent_commands, self.control_states):
                if writer:
                    writer.close()

        _, final = self.state.snapshot()
        ble_summary = self.ble.summary if self.ble else {
            "notifications": 0,
            "duml_frames": 0,
            "fff5_write_count": 0,
            "cccd_write_count": 0,
            "error": None,
        }
        if self.normal_session:
            ble_summary['fff5_write_count'] = self.normal_session.summary.get('fff5_write_count',0)
            error = error or self.normal_session.summary.get('error')
        summary = {
            "generated_at_utc": datetime.now(timezone.utc).isoformat(),
            "actual_duration_seconds": (time.monotonic_ns() - self.started_monotonic_ns) / 1e9,
            "stop_reason": self.stop_reason,
            "session_mode": self.session_mode,
            "normal_session": self.normal_session.summary if self.normal_session else None,
            "error": error,
            "read_only": self.control_mode != "live" and self.session_mode != 'normal',
            "fff5_write_count": ble_summary["fff5_write_count"],
            "ble": ble_summary,
            "final_state": final.to_dict(),
            "pipeline": {
                "decoder_factory": self.decoder.get_factory().get_name(),
                "sink_factory": self.video_sink.get_factory().get_name(),
                "negotiated_caps": self.current_caps,
                "hardware_decoder_maintained": self.decoder.get_factory().get_name()
                == "mppvideodec",
            },
            "metrics": summarize_metrics(self.metric_samples),
            "video_bitrate": {'source':'decoder_sink_encoded_h264',
                'bytes':self.video_bitrate.total_bytes,
                'average_bps':sum(self.video_bitrate_samples)/len(self.video_bitrate_samples) if self.video_bitrate_samples else None},
            "screenshot": self.screenshot,
            "cleanup": {
                "app_registry_empty": "app" not in self.registry.load(),
                "pipeline_null": True,
                "gnome_overview_restored": not overview_before or overview_hidden,
            },
            "control": (
                {
                    **self.control.snapshot(),
                    "mock": True,
                    "mock_command_count": len(self.mock_sink.records),
                    "mock_zero_count": sum(
                        bool(record["is_zero"]) for record in self.mock_sink.records
                    ),
                    "fff5_write_count": self.mock_sink.fff5_write_count,
                    "all_commands_returned_to_zero": (
                        not self.mock_sink.records or self.mock_sink.records[-1]["is_zero"]
                    ),
                }
                if self.control
                else (
                    {
                        **self.live_control.snapshot(),
                        "mock": False,
                        "input_geometry": "unit_circle",
                        "speed_mapping": "linear_radial_32_to_188",
                        "rate_hz": 10,
                        "watchdog_ms": 250,
                        "control_keepalive_timeout_ms": 2500,
                        "continuous_limit_enabled": False,
                    }
                    if self.live_control
                    else None
                )
            ),
        }
        if self.session_mode != 'normal' and summary["fff5_write_count"] != 0:
            summary["error"] = summary["error"] or "read-only app FFF5 safety invariant failed"
        (self.private_output / "summary.json").write_text(
            json.dumps(summary, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
        )
        sanitized = json.loads(json.dumps(summary))
        if sanitized.get('control'):
            sanitized['control'].pop('target_ip',None)
        if sanitized.get('normal_session'):
            sanitized['normal_session'] = {k:v for k,v in sanitized['normal_session'].items()
                if k in {'mode','error','network_restored','success','actual_duration_seconds',
                         'media','video','fff5_write_count','flow_ack_count','live_enable_count'}}
        if sanitized.get("final_state", {}).get("battery_provenance"):
            sanitized["final_state"]["battery_provenance"].pop("raw_value", None)
        (self.sanitized_output / "summary.json").write_text(
            json.dumps(sanitized, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
        )
        write_manifest(self.private_output)
        write_manifest(self.sanitized_output)
        return summary


def start_background(argv: list[str], *, output: Path) -> dict:
    registry = ProcessRegistry(Path("runtime/media-processes.json"))
    if "app" in registry.load():
        raise RuntimeError("OpenFrameTap app is already running")
    output = require_private_directory(output)
    log = output / "runtime.log"
    command = [sys.executable, "-m", "openframetap", *argv]
    with log.open("ab", buffering=0) as stream:
        process = subprocess.Popen(
            command,
            stdin=subprocess.DEVNULL,
            stdout=stream,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )
    deadline = time.monotonic() + 12.0
    while time.monotonic() < deadline:
        item = registry.load().get("app")
        if item:
            return {"state": "running", "process": item.to_public_dict(), "output": str(output)}
        if process.poll() is not None:
            raise RuntimeError(f"app exited during startup with status {process.returncode}")
        time.sleep(0.1)
    process.terminate()
    raise TimeoutError("app did not register its owned PID")
