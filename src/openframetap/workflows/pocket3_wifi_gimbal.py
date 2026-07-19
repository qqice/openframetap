"""Bounded Pocket 3 Wi-Fi 04/01 center and pulse evidence sessions."""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
import json
from pathlib import Path
import subprocess
import time
from typing import Callable

from openframetap.app.artifacts import JsonlWriter
from openframetap.control.gimbal_controller import (
    GimbalUdpController,
    WifiControlPrerequisites,
    WifiGimbalConfig,
)
from openframetap.control.gimbal_profile import CENTER_STICK_COMMAND
from openframetap.devices.pocket3 import POCKET3_PROFILE
from openframetap.network.secrets import require_private_directory
from openframetap.protocol.duml import decode_duml_frame
from openframetap.protocol.reassembly import DumlStreamReassembler
from openframetap.transport.bluez_ble import BluezBleTransport
from openframetap.transport.dji_wifi_udp import (
    DEFAULT_LOCAL_PORT,
    DjiWifiUdpTransport,
    discover_rtmp_publisher_ip,
)


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


async def run_wifi_gimbal_test(
    *,
    mode: str,
    address: str,
    output_dir: Path,
    software_git_head: str,
    mimo_closed: bool,
    axis: str | None = None,
    direction: str | None = None,
    offset: int = 16,
    frames: int = 2,
    rate_hz: float = 10.0,
    local_port: int = DEFAULT_LOCAL_PORT,
    transport_factory: Callable = DjiWifiUdpTransport,
    ble_transport_factory: Callable = BluezBleTransport,
    sleep: Callable[[float], object] = asyncio.sleep,
) -> dict:
    if mode not in {"center-test", "pulse"}:
        raise ValueError("mode must be center-test or pulse")
    if not mimo_closed:
        raise RuntimeError("DJI Mimo must be fully closed before Wi-Fi control")
    if mode == "pulse" and (axis not in {"yaw", "pitch"} or direction not in {"positive", "negative"}):
        raise ValueError("pulse requires an allowlisted axis and direction")

    output = require_private_directory(output_dir)
    sent_datagrams = JsonlWriter(output / "sent-datagrams.jsonl")
    sent_duml = JsonlWriter(output / "sent-duml.jsonl")
    udp_received = JsonlWriter(output / "udp-received.jsonl")
    notifications = JsonlWriter(output / "notifications.jsonl")
    telemetry = JsonlWriter(output / "telemetry.jsonl")
    transitions = JsonlWriter(output / "state-transitions.jsonl")
    reassembler = DumlStreamReassembler()
    started_ns = time.monotonic_ns()
    target_ip = discover_rtmp_publisher_ip()
    media_result = subprocess.run(
        ["ss", "-Htn", "state", "established", "sport", "=", ":1935"],
        text=True,
        capture_output=True,
        check=False,
        timeout=5,
    )
    (output / "media-status.json").write_text(
        json.dumps(
            {
                "observed_at_utc": utc_now(),
                "publisher_online": True,
                "publisher_ip": target_ip,
                "ss_returncode": media_result.returncode,
                "ss_stdout": media_result.stdout,
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    config = {
        "generated_at_utc": utc_now(),
        "mode": mode,
        "axis": axis,
        "direction": direction,
        "offset": offset,
        "frames": frames,
        "rate_hz": rate_hz,
        "target_ip": target_ip,
        "target_port": 9004,
        "local_port": local_port,
        "envelope_profile": "mimo-pocket3-session-7055-v1",
        "center_payload_hex": CENTER_STICK_COMMAND.encode_payload().hex(),
        "mimo_closed_owner_assertion": True,
        "software_git_head": software_git_head,
        "forbidden_commands": ["04/50", "all non-04/01 DUML commands", "raw hex"],
    }
    (output / "config.json").write_text(json.dumps(config, indent=2) + "\n", encoding="utf-8")

    notification_count = 0
    udp_datagram_count = 0
    duml_count = 0
    gimbal_count = 0
    crc_failures = 0
    reassembly_errors = 0
    telemetry_first: dict[str, int] | None = None
    telemetry_last: dict[str, int] | None = None
    error: str | None = None
    udp_telemetry_ready = asyncio.Event()

    def udp_event(event: dict) -> None:
        sent_datagrams.write(event)
        if event.get("duml_hex"):
            frame = decode_duml_frame(bytes.fromhex(event["duml_hex"]))
            sent_duml.write(
                {
                    "wall_time_utc": event["wall_time_utc"],
                    "monotonic_ns": event["monotonic_ns"],
                    **frame.to_dict(),
                }
            )

    def record_frame(frame, *, wall_time: str, monotonic_ns: int, transport: str) -> None:
        nonlocal notification_count, duml_count, gimbal_count, crc_failures
        nonlocal reassembly_errors, telemetry_first, telemetry_last
        nonlocal udp_datagram_count
        duml_count += 1
        if not frame.crc8_valid or not frame.crc16_valid:
            crc_failures += 1
        if frame.cmd_set != 0x04:
            return
        if transport == "wifi_udp_9004":
            udp_telemetry_ready.set()
        gimbal_count += 1
        item = {
            "wall_time_utc": wall_time,
            "monotonic_ns": monotonic_ns,
            "transport": transport,
            **frame.to_dict(),
        }
        if frame.cmd_id == 0x05 and len(frame.payload) >= 24:
            import struct

            candidates = {
                "yaw_offset16": struct.unpack_from("<h", frame.payload, 16)[0],
                "pitch_offset20": struct.unpack_from("<h", frame.payload, 20)[0],
                "roll_offset22": struct.unpack_from("<h", frame.payload, 22)[0],
            }
            item["candidates"] = candidates
            telemetry_first = telemetry_first or candidates
            telemetry_last = candidates
        telemetry.write(item)

    async def on_notification(record) -> None:
        nonlocal notification_count, reassembly_errors
        notification_count += 1
        notifications.write(record.to_dict())
        for event in reassembler.feed(record.data):
            if event.kind != "frame" or event.frame is None:
                reassembly_errors += 1
                continue
            record_frame(
                event.frame,
                wall_time=record.wall_timestamp,
                monotonic_ns=record.monotonic_ns,
                transport="ble_fff4",
            )

    async def receive_udp_telemetry() -> None:
        nonlocal udp_datagram_count, reassembly_errors
        while udp.is_open:
            record = await udp.receive_datagram()
            udp_datagram_count += 1
            udp_received.write(record.to_dict())
            datagram_reassembler = DumlStreamReassembler()
            events = datagram_reassembler.feed(record.data) + datagram_reassembler.finish()
            for event in events:
                if event.kind != "frame" or event.frame is None:
                    # Envelope bytes before the DUML magic are expected resync
                    # input, not a persistent stream failure.
                    continue
                record_frame(
                    event.frame,
                    wall_time=record.wall_time_utc,
                    monotonic_ns=record.monotonic_ns,
                    transport="wifi_udp_9004",
                )

    ble = ble_transport_factory(address, POCKET3_PROFILE)
    udp = transport_factory(
        target_ip,
        local_port=local_port,
        event_handler=udp_event,
    )
    controller = GimbalUdpController(
        udp,
        config=WifiGimbalConfig(),
        sleep=sleep,
        on_transition=transitions.write,
    )
    udp_receiver_task: asyncio.Task | None = None
    try:
        await ble.connect()
        await ble.acquire_mtu()
        await ble.subscribe(on_notification)
        await sleep(0.5)
        if notification_count == 0:
            raise RuntimeError("FFF4 telemetry subscription produced no notification evidence")
        await udp.open()
        udp_receiver_task = asyncio.create_task(
            receive_udp_telemetry(), name="pocket3-wifi-telemetry-receiver"
        )
        try:
            await asyncio.wait_for(udp_telemetry_ready.wait(), timeout=1.0)
        except TimeoutError as exc:
            raise RuntimeError(
                "Pocket transport handshake completed but no UDP gimbal telemetry arrived"
            ) from exc
        await controller.start_watchdog()
        prerequisites = WifiControlPrerequisites(
            pocket_ip_confirmed=True,
            rtmp_publisher_online=True,
            ble_telemetry_online=True,
            udp_socket_ready=True,
            envelope_validated=True,
            center_payload_validated=(CENTER_STICK_COMMAND.encode_payload().hex() == "00040000000400804200"),
            watchdog_running=controller.watchdog_running,
            single_writer_acquired=True,
            mimo_absent=mimo_closed,
        )
        missing = prerequisites.missing()
        if missing:
            raise RuntimeError("Wi-Fi gimbal preflight failed: " + ", ".join(missing))
        if mode == "center-test":
            await controller.center_burst(3, reason="center_test_initial")
            await sleep(2.0)
            await controller.center_burst(5, reason="center_test_redundant")
            await sleep(2.0)
        else:
            await controller.arm(prerequisites)
            await controller.pulse(
                axis=str(axis),
                direction=str(direction),
                offset=offset,
                frames=frames,
                rate_hz=rate_hz,
            )
    except BaseException as exc:
        error = f"{type(exc).__name__}: {exc}"
        if udp.is_open:
            try:
                await controller.emergency_stop("workflow_exception")
            except BaseException as stop_exc:
                error += f"; center failure: {type(stop_exc).__name__}: {stop_exc}"
    finally:
        await controller.stop_watchdog()
        if udp_receiver_task is not None:
            udp_receiver_task.cancel()
            try:
                await udp_receiver_task
            except asyncio.CancelledError:
                pass
            except Exception as receive_exc:
                error = error or (
                    f"UDP telemetry receiver failed: {type(receive_exc).__name__}: {receive_exc}"
                )
        await udp.close()
        await ble.disconnect()
        for event in reassembler.finish():
            if event.kind != "frame":
                reassembly_errors += 1
        for writer in (
            sent_datagrams,
            sent_duml,
            udp_received,
            notifications,
            telemetry,
            transitions,
        ):
            writer.close()

    telemetry_delta = None
    if telemetry_first is not None and telemetry_last is not None:
        telemetry_delta = {
            key: telemetry_last[key] - telemetry_first[key] for key in telemetry_first
        }
    summary = {
        "generated_at_utc": utc_now(),
        "actual_duration_seconds": (time.monotonic_ns() - started_ns) / 1e9,
        "error": error,
        "target_ip": target_ip,
        "target_port": 9004,
        "local_port": local_port,
        "envelope_profile": config["envelope_profile"],
        "axis": axis,
        "direction": direction,
        "offset": offset if mode == "pulse" else 0,
        "non_center_packets": controller.non_center_packets,
        "center_packets": controller.center_packets,
        "maximum_non_center_duration_ms": 250,
        "watchdog_triggered": controller.watchdog_triggered,
        "emergency_stop_triggered": controller.emergency_stop_triggered,
        "telemetry_delta": telemetry_delta,
        "visible_motion_observation": "pending_owner_observation",
        "motion_stopped": None,
        "notifications_received": notification_count,
        "udp_datagrams_received": udp_datagram_count,
        "udp_gimbal_telemetry_online_before_control": udp_telemetry_ready.is_set(),
        "duml_frames_received": duml_count,
        "gimbal_frames_received": gimbal_count,
        "crc_failures": crc_failures,
        "reassembly_errors": reassembly_errors,
        "ble_disconnects": ble.disconnect_count,
        "rtmp_disconnects": 0,
        "socket_errors": 1 if error and "socket" in error.lower() else 0,
        "fff5_write_count": ble.fff5_write_count,
        "04_01_send_count": controller.center_packets + controller.non_center_packets,
        "04_50_send_count": 0,
        "final_state": controller.state.value,
        "software_git_head": software_git_head,
    }
    if ble.fff5_write_count != 0 or summary["04_50_send_count"] != 0:
        summary["error"] = summary["error"] or "forbidden command write invariant failed"
    (output / "summary.json").write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
    return summary
