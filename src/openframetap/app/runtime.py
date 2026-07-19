from __future__ import annotations

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
from openframetap.app.state import AppStateSnapshot, StateStore
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
        self.registry = ProcessRegistry(registry_path)
        self.state = StateStore()
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
        self._event({"kind": "app_stop_requested", "reason": reason})
        if self.loop is not None:
            self.loop.quit()

    def _build_window(self, Gtk, Gdk, Gst) -> None:
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
        window.connect("delete-event", lambda *_args: (self._request_stop("window_close"), True)[1])
        window.connect("focus-out-event", lambda *_args: self._event({"kind": "window_focus_lost"}))

        root = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        header = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=14)
        header.set_size_request(-1, 54)
        for key in ("device", "ble", "pairing", "rtmp", "media", "video", "battery", "rock", "age"):
            label = Gtk.Label(label=key)
            label.set_xalign(0.0)
            header.pack_start(label, key in {"device", "media"}, key in {"device", "media"}, 8)
            self.labels[key] = label

        controls = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=20)
        controls.set_size_request(-1, 96)
        controls.set_margin_start(24)
        controls.set_margin_end(24)
        controls.set_margin_top(10)
        controls.set_margin_bottom(10)
        disabled = Gtk.Label(label="JOYSTICK DISABLED · READ-ONLY")
        disabled.set_name("control-disabled")
        controls.pack_start(disabled, False, False, 0)
        exit_button = Gtk.Button(label="退出  Esc / Q")
        exit_button.connect("clicked", lambda *_args: self._request_stop("exit_button"))
        controls.pack_end(exit_button, False, False, 0)
        root.pack_start(header, False, False, 0)
        root.pack_start(widget, True, True, 0)
        root.pack_start(controls, False, False, 0)
        window.add(root)

        css = Gtk.CssProvider()
        css.load_from_data(
            b"window { background: #000; color: #fff; } box { background: rgba(0,0,0,0.78); } "
            b"label { color: #fff; font-size: 15px; } #control-disabled { color: #ffcc33; font-size: 20px; } "
            b"button { font-size: 18px; padding: 10px 18px; }"
        )
        Gtk.StyleContext.add_provider_for_screen(
            Gdk.Screen.get_default(), css, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION
        )
        window.connect("key-press-event", self._on_key)
        self.window = window
        self.fps_sink = fps_sink
        self.decoder = decoder
        self.video_sink = video_sink

    def _on_key(self, _widget, event) -> bool:
        name = event.string.lower() if event.string else ""
        if name == "q" or event.keyval == 65307:
            self._request_stop("keyboard_exit")
            return True
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
        self.labels["rtmp"].set_text("RTMP ●" if snapshot.rtmp_publisher_online else "RTMP ○")
        self.labels["media"].set_text(f"MEDIA {snapshot.media_state.upper()}")
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
        Gtk, _Gdk, Gst, _GLib = self.bindings
        now_ns = time.monotonic_ns()
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
            rtmp_publisher_online=rendered > 0,
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
            self.metrics_writer.write(sample.to_dict())
            bitrate = None
            if sample.network_rx_bytes is not None and self.last_network:
                delta_ns = sample.monotonic_ns - self.last_network[0]
                if delta_ns > 0:
                    bitrate = (sample.network_rx_bytes - self.last_network[1]) * 8e9 / delta_ns
            if sample.network_rx_bytes is not None:
                self.last_network = (sample.monotonic_ns, sample.network_rx_bytes)
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
        self.metrics = ProcessMetrics(os.getpid())
        self.current_caps = None
        overview_before = gnome_overview_active(env)
        overview_hidden = False
        registry_item = self.registry.register_pid("app", os.getpid(), sys.argv)
        (Path("runtime") / "app-last-output.txt").write_text(
            str(self.private_output.relative_to(Path.cwd())) + "\n", encoding="utf-8"
        )
        config = {
            "generated_at_utc": datetime.now(timezone.utc).isoformat(),
            "address": self.address,
            "control_mode": "disabled",
            "read_only": True,
            "duration_seconds": self.duration_seconds,
            "pipeline": _redacted_spec(self.spec),
            "display_session": session.to_dict(),
            "owned_pid": registry_item.to_public_dict(),
        }
        (self.private_output / "app-config.json").write_text(
            json.dumps(config, indent=2) + "\n", encoding="utf-8"
        )
        self._event({"kind": "app_started", "read_only": True})
        if self.enable_ble:
            self.ble = ReadOnlyBleMonitor(self.address, self.state, event_handler=self._event)
            self.ble.start()

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
            result = self.pipeline.set_state(Gst.State.PLAYING)
            if result == Gst.StateChangeReturn.FAILURE:
                raise RuntimeError("GTK app pipeline failed to enter PLAYING")
            self.state.update(media_state="starting")
            GLib.timeout_add(250, self._tick)

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

            GLib.timeout_add_seconds(5, screenshot)
            self.loop.run()
        except Exception as exc:
            error = f"{type(exc).__name__}: {exc}"
            self._event({"kind": "app_error", "error": error})
        finally:
            self.pipeline.set_state(Gst.State.NULL)
            if self.ble:
                try:
                    self.ble.stop()
                except Exception as exc:
                    error = error or f"{type(exc).__name__}: {exc}"
            if overview_before:
                set_gnome_overview_active(True, env)
            self.registry.unregister("app")
            for signum, handler in previous_handlers.items():
                signal.signal(signum, handler)
            self.events.close()
            self.states.close()
            self.metrics_writer.close()

        _, final = self.state.snapshot()
        ble_summary = self.ble.summary if self.ble else {
            "notifications": 0,
            "duml_frames": 0,
            "fff5_write_count": 0,
            "cccd_write_count": 0,
            "error": None,
        }
        summary = {
            "generated_at_utc": datetime.now(timezone.utc).isoformat(),
            "actual_duration_seconds": (time.monotonic_ns() - self.started_monotonic_ns) / 1e9,
            "stop_reason": self.stop_reason,
            "error": error,
            "read_only": True,
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
            "screenshot": self.screenshot,
            "cleanup": {
                "app_registry_empty": "app" not in self.registry.load(),
                "pipeline_null": True,
                "gnome_overview_restored": not overview_before or overview_hidden,
            },
        }
        if summary["fff5_write_count"] != 0:
            summary["error"] = summary["error"] or "read-only app FFF5 safety invariant failed"
        (self.private_output / "summary.json").write_text(
            json.dumps(summary, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
        )
        sanitized = json.loads(json.dumps(summary))
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
