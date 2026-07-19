"""Threaded live Wi-Fi control session used by the GTK/Wayland application."""

from __future__ import annotations

import asyncio
from dataclasses import asdict
import math
from datetime import datetime, timezone
import queue
import threading
import time
from typing import Callable

from openframetap.app.input import ControlInput
from openframetap.app.state import StateStore
from openframetap.control.gimbal_controller import (
    GimbalUdpController,
    WifiControlPrerequisites,
    WifiGimbalState,
)
from openframetap.control.gimbal_profile import (
    LIVE_PROTOTYPE_MAX_OFFSET,
    LIVE_PROTOTYPE_MIN_OFFSET,
    Pocket3StickCommand,
)
from openframetap.protocol.reassembly import DumlStreamReassembler
from openframetap.transport.dji_wifi_udp import (
    DjiWifiUdpTransport,
    discover_rtmp_publisher_ip,
    list_rtmp_server_peer_ips,
)


def _utc() -> str:
    return datetime.now(timezone.utc).isoformat()


class LiveWifiControlSession:
    """Own the only UDP writer while GUI callbacks only enqueue input."""

    def __init__(
        self,
        state: StateStore,
        *,
        event_handler: Callable[[dict], None] | None = None,
        datagram_handler: Callable[[dict], None] | None = None,
        transition_handler: Callable[[dict], None] | None = None,
        transport_factory: Callable = DjiWifiUdpTransport,
        minimum_offset: int = LIVE_PROTOTYPE_MIN_OFFSET,
        maximum_offset: int = LIVE_PROTOTYPE_MAX_OFFSET,
    ) -> None:
        if not 1 <= minimum_offset < maximum_offset <= LIVE_PROTOTYPE_MAX_OFFSET:
            raise ValueError("live radial offset range is outside the captured envelope")
        self.state = state
        self.event_handler = event_handler or (lambda event: None)
        self.datagram_handler = datagram_handler or (lambda event: None)
        self.transition_handler = transition_handler or (lambda event: None)
        self.transport_factory = transport_factory
        self._inputs: queue.SimpleQueue[ControlInput] = queue.SimpleQueue()
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self._ready = threading.Event()
        self._done = threading.Event()
        self._latest = ControlInput(source="live")
        self._minimum_offset = int(minimum_offset)
        self._maximum_offset = int(maximum_offset)
        self._snapshot_lock = threading.Lock()
        self._snapshot = {
            "state": "disabled",
            "target_ip": None,
            "local_port": 54232,
            "yaw": 0.0,
            "pitch": 0.0,
            "last_command_ns": None,
            "last_center_ns": None,
            "watchdog": "inactive",
            "fault": None,
            "center_packets": 0,
            "non_center_packets": 0,
            "minimum_offset": self._minimum_offset,
            "maximum_offset": self._maximum_offset,
            "current_protocol_offset": 0,
            "transport_ack_count": 0,
            "last_ack_sequence": None,
            "transport_response_count": 0,
            "last_response_sequence": None,
            "ambiguous_window_count": 0,
            "flow_ack_sent_count": 0,
            "keepalive_sent_count": 0,
            "keepalive_response_count": 0,
        }

    def _set(self, **values) -> None:
        with self._snapshot_lock:
            self._snapshot.update(values)

    def snapshot(self) -> dict:
        with self._snapshot_lock:
            return dict(self._snapshot)

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            raise RuntimeError("live Wi-Fi control is already running")
        self._stop.clear()
        self._done.clear()
        self._thread = threading.Thread(target=self._thread_main, name="wifi-gimbal", daemon=False)
        self._thread.start()

    def submit(self, value: ControlInput) -> None:
        self._inputs.put(value)

    def stop(self, reason: str = "app_stop", timeout: float = 5.0) -> None:
        self._inputs.put(
            ControlInput(emergency_stop=True, exit_requested=True, source=reason, monotonic_ns=time.monotonic_ns())
        )
        if self._thread:
            self._thread.join(timeout)
            if self._thread.is_alive():
                raise TimeoutError("live control did not finish its center shutdown")

    def _event(self, kind: str, **values) -> None:
        self.event_handler({"wall_time_utc": _utc(), "monotonic_ns": time.monotonic_ns(), "kind": kind, **values})

    def _thread_main(self) -> None:
        try:
            asyncio.run(self._run())
        except BaseException as exc:
            self._set(
                state="fault",
                yaw=0.0,
                pitch=0.0,
                current_protocol_offset=0,
                fault=f"{type(exc).__name__}: {exc}",
            )
            self._event("live_control_thread_error", error=f"{type(exc).__name__}: {exc}")
        finally:
            self._done.set()

    async def _run(self) -> None:
        target_ip = discover_rtmp_publisher_ip()
        self._set(target_ip=target_ip, state="connecting")
        transport = self.transport_factory(
            target_ip,
            local_port=54232,
            event_handler=self.datagram_handler,
        )
        controller = GimbalUdpController(transport, on_transition=self.transition_handler)
        receiver: asyncio.Task | None = None
        udp_ready = asyncio.Event()
        neutral_required = False
        last_publisher_check_ns = 0

        async def receive() -> None:
            while transport.is_open:
                record = await transport.receive_datagram()
                self._set(
                    transport_ack_count=getattr(transport, "ack_observed_count", 0),
                    last_ack_sequence=getattr(transport, "last_ack_sequence", None),
                    transport_response_count=getattr(transport, "response_packet_count", 0),
                    last_response_sequence=getattr(transport, "last_response_sequence", None),
                    ambiguous_window_count=getattr(transport, "ambiguous_window_count", 0),
                    flow_ack_sent_count=getattr(transport, "flow_ack_sent_count", 0),
                    keepalive_sent_count=getattr(transport, "keepalive_sent_count", 0),
                    keepalive_response_count=getattr(transport, "keepalive_response_count", 0),
                )
                data = record.to_dict()
                data["kind"] = "udp_received"
                self.datagram_handler(data)
                reassembler = DumlStreamReassembler()
                for event in reassembler.feed(record.data) + reassembler.finish():
                    if event.kind != "frame" or event.frame is None:
                        continue
                    frame = event.frame
                    if frame.cmd_set == 0x04:
                        udp_ready.set()
                    if (frame.cmd_set, frame.cmd_id) == (0x04, 0x05) and len(frame.payload) >= 24:
                        import struct
                        self.state.update(
                            gimbal_yaw_raw_candidate=str(struct.unpack_from("<h", frame.payload, 16)[0]),
                            gimbal_pitch_raw_candidate=str(struct.unpack_from("<h", frame.payload, 20)[0]),
                            gimbal_roll_raw_candidate=str(struct.unpack_from("<h", frame.payload, 22)[0]),
                        )
                    elif (frame.cmd_set, frame.cmd_id) == (0x0D, 0x02) and len(frame.payload) >= 21:
                        battery = frame.payload[20]
                        if battery <= 100:
                            self.state.update(battery_percent=battery)

        try:
            deadline = time.monotonic() + 5.0
            while not self.state.snapshot()[1].ble_connected:
                if time.monotonic() >= deadline:
                    raise RuntimeError("BLE telemetry was not connected before live control")
                await asyncio.sleep(0.05)
            await transport.open()
            receiver = asyncio.create_task(receive(), name="live-wifi-telemetry")
            await asyncio.wait_for(udp_ready.wait(), timeout=1.0)
            await controller.start_watchdog()

            async def send_verified_keepalive(*, initial: bool = False) -> None:
                expected = transport.keepalive_response_count + 1
                await transport.send_control_keepalive()
                if initial:
                    deadline = time.monotonic() + 1.5
                    while transport.keepalive_response_count < expected:
                        if time.monotonic() >= deadline:
                            raise TimeoutError("Pocket initial 04/50 control keepalive response timed out")
                        await asyncio.sleep(0.01)
                self._set(
                    keepalive_sent_count=transport.keepalive_sent_count,
                    keepalive_response_count=transport.keepalive_response_count,
                )

            await send_verified_keepalive(initial=True)
            last_keepalive_send_ns = time.monotonic_ns()
            await controller.arm(WifiControlPrerequisites(*([True] * 9)))
            self._ready.set()
            self._set(
                state="armed",
                watchdog="healthy",
                current_protocol_offset=0,
                last_center_ns=controller.last_center_ns,
            )
            self._event("live_control_armed", target_ip=target_ip, local_port=54232)

            while not self._stop.is_set():
                priority_stop: ControlInput | None = None
                while True:
                    try:
                        candidate = self._inputs.get_nowait()
                        if candidate.emergency_stop or candidate.exit_requested:
                            priority_stop = candidate
                        elif priority_stop is None:
                            self._latest = candidate
                    except queue.Empty:
                        break
                if priority_stop is not None:
                    self._latest = priority_stop
                now = time.monotonic_ns()
                value = self._latest
                if value.emergency_stop or value.exit_requested:
                    await controller.emergency_stop("ui_stop")
                    self._set(
                        state="disabled",
                        yaw=0.0,
                        pitch=0.0,
                        current_protocol_offset=0,
                        last_center_ns=controller.last_center_ns,
                    )
                    self._stop.set()
                    break
                if now - last_publisher_check_ns >= 1_000_000_000:
                    # The application's own rtmpsrc connection also appears
                    # as a peer of MediaMTX's :1935 listener.  Keep the
                    # initially locked Pocket address and verify membership;
                    # requiring a singleton here faults as soon as video is
                    # embedded in the same application.
                    if target_ip not in list_rtmp_server_peer_ips():
                        await controller.emergency_stop("rtmp_publisher_lost")
                        raise RuntimeError("RTMP publisher disappeared or changed")
                    last_publisher_check_ns = now
                if not self.state.snapshot()[1].ble_connected:
                    await controller.emergency_stop("ble_disconnected")
                    raise RuntimeError("BLE disconnected during live control")
                if now - last_keepalive_send_ns >= 1_000_000_000:
                    await send_verified_keepalive()
                    last_keepalive_send_ns = time.monotonic_ns()
                flow_ack = await transport.send_flow_ack_if_due()
                if flow_ack is not None:
                    self._set(flow_ack_sent_count=transport.flow_ack_sent_count)
                last_response_ns = transport.last_keepalive_response_ns
                if (
                    last_response_ns is None
                    or now - last_response_ns > 2_500_000_000
                ):
                    await controller.emergency_stop("control_keepalive_stale")
                    raise TimeoutError("Pocket 04/50 control keepalive stale for 2.5 seconds")
                stale = not value.monotonic_ns or now - value.monotonic_ns > 250_000_000
                quantized = Pocket3StickCommand.from_radial_axes(
                    yaw_axis=value.yaw,
                    pitch_axis=value.pitch,
                    minimum_offset=self._minimum_offset,
                    maximum_offset=self._maximum_offset,
                )
                # The profile is authoritative for the exact center/non-center
                # boundary after deadzone and radial speed conversion.
                active = value.active and not quantized.is_center and not stale
                if neutral_required:
                    if not value.active or (value.yaw == 0 and value.pitch == 0):
                        neutral_required = False
                        self._set(
                            state="armed",
                            watchdog="healthy",
                            current_protocol_offset=0,
                        )
                    await asyncio.sleep(0.02)
                    continue
                if not active:
                    if controller.state == WifiGimbalState.ACTIVE:
                        await controller.release_live("input_release_or_watchdog")
                        self._set(
                            state="armed",
                            yaw=0.0,
                            pitch=0.0,
                            current_protocol_offset=0,
                            last_center_ns=controller.last_center_ns,
                        )
                    if stale and value.active:
                        neutral_required = True
                        self._set(watchdog="expired")
                    await asyncio.sleep(0.02)
                    continue
                await controller.send_live_axes(
                    yaw=value.yaw,
                    pitch=value.pitch,
                    minimum_offset=self._minimum_offset,
                    maximum_offset=self._maximum_offset,
                )
                magnitude = min(1.0, math.hypot(value.yaw, value.pitch))
                current_offset = round(
                    self._minimum_offset
                    + magnitude * (self._maximum_offset - self._minimum_offset)
                )
                self._set(
                    state="active",
                    yaw=value.yaw,
                    pitch=value.pitch,
                    current_protocol_offset=current_offset,
                    last_command_ns=controller.last_send_ns,
                    center_packets=controller.center_packets,
                    non_center_packets=controller.non_center_packets,
                )
                await asyncio.sleep(0.1)
        except BaseException:
            if transport.is_open and controller.state not in {WifiGimbalState.DISABLED, WifiGimbalState.FAULT}:
                try:
                    await controller.emergency_stop("live_exception")
                except BaseException:
                    pass
            raise
        finally:
            await controller.stop_watchdog()
            if receiver:
                receiver.cancel()
                try:
                    await receiver
                except BaseException:
                    pass
            await transport.close()
            self._set(
                center_packets=controller.center_packets,
                non_center_packets=controller.non_center_packets,
                last_center_ns=controller.last_center_ns,
            )
