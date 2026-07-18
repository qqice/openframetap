"""Human-confirmed, session-bound Pocket 3 application pairing.

Every outgoing frame is displayed and independently confirmed by SHA-256.  The
session never guesses a response field and never sends from a notification
callback.  Keeping the BLE connection open allows a response sequence to be
mirrored without carrying a stale transaction into a new connection.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
import hashlib
import inspect
import json
from pathlib import Path
import time
from typing import Awaitable, Callable

from openframetap.devices.pocket3 import (
    POCKET3_PROFILE,
    build_pairing_stage1_frame,
    build_pairing_stage2_frame,
    build_set_pairing_pin_frame,
)
from openframetap.pairing.state_machine import PairingState, PairingStateMachine
from openframetap.protocol.commands import (
    PAIRING_COMMANDS,
    SendAuthorization,
    assert_send_allowed,
    validate_command_frame,
)
from openframetap.protocol.duml import DumlFrame, decode_duml_frame
from openframetap.protocol.reassembly import DumlStreamReassembler
from openframetap.telemetry.recorder import TelemetryRecorder
from openframetap.transport.bluez_ble import BluezBleTransport

ConfirmationCallback = Callable[[dict], bool | Awaitable[bool]]


def _jsonl(path: Path, payload: dict) -> None:
    with path.open("a", encoding="utf-8", newline="\n") as stream:
        stream.write(json.dumps(payload, ensure_ascii=False, sort_keys=True) + "\n")


def _now_event(event: str, **fields) -> dict:
    return {
        "wall_timestamp": datetime.now(timezone.utc).isoformat(),
        "monotonic_ns": time.monotonic_ns(),
        "event": event,
        **fields,
    }


async def _confirmed(callback: ConfirmationCallback, candidate: dict) -> bool:
    result = callback(candidate)
    if inspect.isawaitable(result):
        return bool(await result)
    return bool(result)


async def run_manual_pairing_session(
    address: str,
    *,
    output_dir: Path,
    confirmation_callback: ConfirmationCallback,
    telemetry_seconds: float = 60.0,
    response_timeout: float = 15.0,
    approval_timeout: float = 60.0,
    transport_factory: Callable = BluezBleTransport,
) -> tuple[dict, bool]:
    """Run at most one pairing attempt with a human confirmation per frame."""

    if telemetry_seconds < 0 or response_timeout <= 0 or approval_timeout <= 0:
        raise ValueError("timeouts must be positive and telemetry_seconds non-negative")
    output_dir.mkdir(parents=True, exist_ok=True)
    candidates_path = output_dir / "pairing-candidates.jsonl"
    pairing_events_path = output_dir / "pairing-events.jsonl"
    candidates_path.write_text("", encoding="utf-8")
    pairing_events_path.write_text("", encoding="utf-8")
    recorder = TelemetryRecorder(
        output_dir,
        address=address,
        operation="human-confirmed-session-bound-pairing",
    )
    reassembler = DumlStreamReassembler()
    frames: asyncio.Queue[DumlFrame] = asyncio.Queue()

    def pairing_event(event: str, **fields) -> None:
        payload = _now_event(event, **fields)
        _jsonl(pairing_events_path, payload)
        print(f"PAIRING_EVENT: {event} {json.dumps(fields, ensure_ascii=False)}", flush=True)

    async def notification_handler(notification) -> None:
        await recorder.record_notification(notification)
        for item in reassembler.feed(notification.data):
            if item.kind == "frame" and item.frame is not None:
                frames.put_nowait(item.frame)

    transport = transport_factory(
        address,
        POCKET3_PROFILE,
        event_handler=recorder.record_event,
    )
    state = PairingStateMachine(timeout_seconds=response_timeout, max_attempts=1)
    sent: list[dict] = []
    result = "not_started"
    error: str | None = None
    connected_at_end = False
    stage2_response: dict | None = None
    pocket_approval: dict | None = None
    started = time.monotonic()

    async def propose_and_send(
        command_name: str,
        raw: bytes,
        *,
        source: str,
        expected_effect: str,
        risk: str,
    ) -> bool:
        command = PAIRING_COMMANDS[command_name]
        frame = decode_duml_frame(raw)
        authorization = SendAuthorization.pairing(
            approval_reference=f"interactive-frame-sha256:{hashlib.sha256(raw).hexdigest()}"
        )
        assert_send_allowed(command, authorization)
        validate_command_frame(command, frame)
        actual = (frame.sender, frame.receiver, frame.cmd_set, frame.cmd_id)
        expected = (command.sender, command.receiver, command.cmd_set, command.cmd_id)
        if actual != expected or not (frame.crc8_valid and frame.crc16_valid):
            raise ValueError(f"candidate metadata/CRC rejected: {actual!r} != {expected!r}")
        candidate = {
            **_now_event("candidate_proposed"),
            "command": command_name,
            "frame_sha256": hashlib.sha256(raw).hexdigest(),
            "frame_hex": raw.hex(),
            "decoded": frame.to_dict(),
            "source": source,
            "expected_effect": expected_effect,
            "risk": risk,
            "human_confirmed": False,
        }
        _jsonl(candidates_path, candidate)
        if not await _confirmed(confirmation_callback, candidate):
            pairing_event(
                "candidate_declined_or_confirmation_mismatch",
                command=command_name,
                frame_sha256=candidate["frame_sha256"],
            )
            return False
        candidate["human_confirmed"] = True
        candidate["confirmed_at"] = datetime.now(timezone.utc).isoformat()
        _jsonl(candidates_path, candidate)
        await transport.send_frame(raw, command=command, authorization=authorization)
        sent.append(
            {
                "command": command_name,
                "frame_sha256": candidate["frame_sha256"],
                "frame_hex": raw.hex(),
            }
        )
        pairing_event(
            "human_confirmed_frame_written",
            command=command_name,
            frame_sha256=candidate["frame_sha256"],
        )
        return True

    async def wait_command(cmd_set: int, cmd_id: int, timeout: float) -> DumlFrame:
        deadline = time.monotonic() + timeout
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError(f"timeout waiting for {cmd_set:02X}/{cmd_id:02X}")
            frame = await asyncio.wait_for(frames.get(), timeout=remaining)
            if (frame.cmd_set, frame.cmd_id) == (cmd_set, cmd_id):
                return frame

    try:
        await transport.connect()
        await transport.subscribe(notification_handler)
        await asyncio.wait_for(frames.get(), timeout=5.0)
        pairing_event("fff4_ready")
        state.subscribed()
        state.begin_authorized_attempt()

        first = build_set_pairing_pin_frame()
        if not await propose_and_send(
            "set_pairing_pin",
            first,
            source="reviewed local proposal plus public implementations",
            expected_effect="request DJI application pairing status",
            risk="may create or replace this app-level pairing session; no system bonding",
        ):
            result = "cancelled_before_first_write"
        else:
            status = await wait_command(0x07, 0x45, response_timeout)
            if not (
                status.sender == 0x07
                and status.receiver == 0x02
                and status.sequence == 0x72AA
                and status.flags == 0xC0
                and status.payload in {b"\x00\x01", b"\x00\x02"}
            ):
                raise RuntimeError(f"unexpected pairing-status frame: {status.raw.hex()}")
            transition = state.pairing_status(status.payload)
            pairing_event(
                "pairing_status",
                status="already_paired" if status.payload[1] == 1 else "confirmation_required",
                raw_hex=status.raw.hex(),
            )
            if transition.action == "complete":
                result = "already_paired"
            else:
                print(
                    "USER_ACTION_REQUIRED: confirm pairing on the Pocket screen now; "
                    "the BLE connection remains open.",
                    flush=True,
                )
                approval = await wait_command(0x07, 0x46, approval_timeout)
                if not (
                    approval.sender == 0x07
                    and approval.receiver == 0x02
                    and approval.flags == 0x40
                    and approval.payload == b"\x01"
                ):
                    raise RuntimeError(f"unexpected Pocket approval frame: {approval.raw.hex()}")
                pocket_approval = approval.to_dict()
                state.device_approved(approval.payload)
                pairing_event(
                    "pocket_screen_approval",
                    sequence=approval.sequence,
                    raw_hex=approval.raw.hex(),
                )

                stage1 = build_pairing_stage1_frame(sequence=approval.sequence)
                if not await propose_and_send(
                    "pairing_stage1_ack",
                    stage1,
                    source="mirrors the exact live 400746 approval request in this connection",
                    expected_effect="acknowledge the Pocket screen approval request",
                    risk="advances DJI application pairing; no stage2 is sent without another prompt",
                ):
                    result = "cancelled_before_stage1"
                else:
                    state.stage1_sent()
                    stage2 = build_pairing_stage2_frame()
                    if not await propose_and_send(
                        "pairing_stage2",
                        stage2,
                        source="published Mimo capture plus djictl pairing finalization",
                        expected_effect="complete the captured application pairing finalization",
                        risk="reference-derived finalization; response semantics remain locally unverified",
                    ):
                        result = "cancelled_before_stage2"
                    else:
                        state.stage2_sent()
                        result = "pocket_approved_and_finalization_sent"
                        try:
                            response = await wait_command(0x00, 0x32, 5.0)
                        except TimeoutError:
                            pairing_event("stage2_response_timeout")
                        else:
                            if not (
                                response.sender == 0x88
                                and response.receiver == 0x02
                                and response.sequence == 0x74AA
                                and response.flags & 0x80
                            ):
                                raise RuntimeError(
                                    f"unexpected pairing-stage2 response: {response.raw.hex()}"
                                )
                            stage2_response = response.to_dict()
                            result = "paired_with_explicit_stage2_response"
                            pairing_event("stage2_response", raw_hex=response.raw.hex())

        if telemetry_seconds and not result.startswith("cancelled_"):
            pairing_event("passive_post_pairing_listen", seconds=telemetry_seconds)
            await asyncio.sleep(telemetry_seconds)
        connected_at_end = transport.is_connected
    except (KeyboardInterrupt, asyncio.CancelledError):
        error = "cancelled"
        state.cancel()
        raise
    except Exception as exc:
        error = f"{type(exc).__name__}: {exc}"
        state.fail_unexpected(error)
        result = "failed"
        pairing_event("failed", error=error)
    finally:
        actual_seconds = time.monotonic() - started
        try:
            await transport.disconnect()
        except Exception as exc:
            disconnect_error = f"{type(exc).__name__}: {exc}"
            error = f"{error}; disconnect: {disconnect_error}" if error else disconnect_error
        summary = recorder.finalize(
            actual_seconds=actual_seconds,
            disconnect_count=transport.active_disconnect_count,
            setup_disconnect_count=transport.setup_disconnect_count,
            unintentional_disconnect_callbacks_total=transport.disconnect_count,
            connected=connected_at_end,
            error=error,
            writes_attempted=len(sent),
            pairing_requested=any(item["command"] == "set_pairing_pin" for item in sent),
            extra_summary={
                "manual_interactive_pairing": True,
                "each_frame_human_confirmed": True,
                "attempts_in_this_session": 1 if sent else 0,
                "sent_frames": sent,
                "automatic_follow_up_frames": 0,
                "bluez_pairing_requested": False,
                "pairing_state": state.state.value,
                "pairing_result": result,
                "pocket_approval": pocket_approval,
                "stage2_response": stage2_response,
            },
        )
    return summary, error is None and state.state is PairingState.PAIRED
