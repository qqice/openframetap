from __future__ import annotations

from pathlib import Path
import time

from openframetap.app.input import ControlInput
from openframetap.app.runtime import GtkReadOnlyApp
from openframetap.app.ble_status import ReadOnlyBleMonitor
from openframetap.app.state import AppStateSnapshot, StateStore
from openframetap.protocol.duml import encode_duml_frame
from openframetap.transport.bluez_ble import NotificationRecord
from openframetap.video.pipelines import PipelineProfile, live_pipeline


def test_ui_state_serialization_and_update_coalescing() -> None:
    store = StateStore(AppStateSnapshot())
    revision, initial = store.snapshot()
    assert initial.to_dict()["control_state"] == "DISABLED"
    changed = store.update(ble_connected=True, battery_percent=72)
    assert changed == revision + 1
    assert store.changed_since(revision)[1].battery_percent == 72
    current, _snapshot = store.snapshot()
    assert store.changed_since(current) is None


def test_gtk_pipeline_keeps_mpp_and_never_uses_appsink() -> None:
    spec = live_pipeline(
        "rtmp://192.168.1.2:1935/live/fixture",
        source="rtmp",
        decoder="mppvideodec",
        sink="gtkwayland",
        fullscreen=True,
        profile=PipelineProfile.LOW_LATENCY,
    )
    rendered = " ".join(spec.argv)
    assert "mppvideodec name=app_decoder" in rendered
    assert "gtkwaylandsink name=video_sink sync=false" in rendered
    assert "appsink" not in rendered


class _ReadOnlyFakeTransport:
    instances = []

    def __init__(self, _address, _profile, *, event_handler) -> None:
        self.event_handler = event_handler
        self.is_connected = False
        self.fff5_write_count = 0
        self.cccd_write_count = 0
        self.__class__.instances.append(self)

    async def connect(self) -> None:
        self.is_connected = True
        self.event_handler({"event": "connected", "monotonic_ns": time.monotonic_ns()})

    async def subscribe(self, handler) -> None:
        self.cccd_write_count += 1
        payload = bytearray(34)
        payload[20] = 72
        raw = encode_duml_frame(
            sender=5,
            receiver=2,
            sequence=1,
            flags=0,
            cmd_set=0x0D,
            cmd_id=0x02,
            payload=payload,
        )
        await handler(
            NotificationRecord(
                wall_timestamp="fixture",
                monotonic_ns=time.monotonic_ns(),
                characteristic_uuid="fff4",
                characteristic_handle=44,
                data=raw,
            )
        )

    async def disconnect(self) -> None:
        self.cccd_write_count += 1
        self.is_connected = False
        self.event_handler({"event": "disconnect_complete", "monotonic_ns": time.monotonic_ns()})


def test_readonly_ui_monitor_preserves_battery_provenance_and_never_writes() -> None:
    _ReadOnlyFakeTransport.instances.clear()
    store = StateStore()
    monitor = ReadOnlyBleMonitor(
        "fixture",
        store,
        transport_factory=_ReadOnlyFakeTransport,
    )
    monitor.start()
    deadline = time.monotonic() + 2
    while store.snapshot()[1].battery_percent is None and time.monotonic() < deadline:
        time.sleep(0.01)
    monitor.stop()
    snapshot = store.snapshot()[1]
    assert snapshot.battery_percent == 72
    assert snapshot.battery_provenance["source_cmd_set"] == 0x0D
    assert snapshot.battery_provenance["source_offset"] == 20
    assert snapshot.battery_provenance["confidence"] == "confirmed"
    assert monitor.summary["fff5_write_count"] == 0
    assert _ReadOnlyFakeTransport.instances[0].fff5_write_count == 0


def test_readonly_ui_without_pocket_keeps_disabled_control_state() -> None:
    snapshot = StateStore().snapshot()[1]
    assert not snapshot.ble_connected
    assert snapshot.battery_percent is None
    assert snapshot.control_state == "DISABLED"
    assert snapshot.yaw == snapshot.pitch == 0.0


class _UnusedSpec:
    argv = []


def test_mock_app_installs_controller_without_fff5_path(tmp_path: Path) -> None:
    private = tmp_path / "artifacts" / "private" / "session"
    sanitized = tmp_path / "artifacts" / "sanitized" / "session"
    app = GtkReadOnlyApp(
        _UnusedSpec(),
        address="fixture",
        private_output=private,
        sanitized_output=sanitized,
        duration_seconds=10,
        enable_ble=False,
        control_mode="mock",
        joystick_config_path=Path("config/control-ui.json"),
        registry_path=tmp_path / "runtime.json",
    )
    app._submit_control(ControlInput(yaw=0.25, active=True, source="keyboard"))
    assert app.control.latest_input.yaw == 0.15
    app._mock_emergency()
    assert app.mock_sink.fff5_write_count == 0
    assert app.mock_sink.records[-1]["is_zero"] is True
    for writer in (app.events, app.states, app.metrics_writer, app.input_events, app.sent_commands, app.control_states):
        writer.close()
