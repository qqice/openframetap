"""Owner-confirmed, same-connection Pocket 3 prepare recovery session."""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
import hashlib
import inspect
import json
from pathlib import Path
import time
from typing import Awaitable, Callable

from openframetap.devices.pocket3 import POCKET3_PROFILE
from openframetap.protocol.commands import (
    SendAuthorization,
    assert_send_allowed,
    get_command_definition,
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


def _event(event: str, **fields) -> dict:
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


def load_prepare_recovery_proposal(
    path: Path, *, expected_address: str
) -> tuple[dict, list[tuple[dict, bytes]]]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if (
        payload.get("proposal_type") != "prepare_recovery_same_connection"
        or payload.get("target_address", "").upper() != expected_address.upper()
        or payload.get("automatic_retry") is not False
        or payload.get("automatic_follow_up") is not False
        or payload.get("human_confirmation_required_per_frame") is not True
        or payload.get("wifi_retry_included") is not False
    ):
        raise PermissionError("prepare-recovery proposal policy or address is invalid")
    stages = payload.get("stages") or []
    if len(stages) != 2:
        raise PermissionError("prepare-recovery proposal must contain exactly two stages")
    expected = (
        (
            "prepare_reentry",
            "prepare_to_live_stream",
            0xFEAB,
            0x02,
            0xE1,
            b"\x1A",
        ),
        (
            "prepare_stream_stage2",
            "prepare_stream_transport",
            0xFFAB,
            0x02,
            0x8E,
            bytes.fromhex("00011c00"),
        ),
    )
    loaded: list[tuple[dict, bytes]] = []
    for index, (stage, wanted) in enumerate(zip(stages, expected, strict=True)):
        stage_name, command_name, sequence, cmd_set, cmd_id, wanted_payload = wanted
        if (
            stage.get("stage") != stage_name
            or stage.get("command") != command_name
            or stage.get("max_send_count") != 1
            or (index == 1 and stage.get("conditional_on_stage1_ack") is not True)
        ):
            raise PermissionError(f"prepare-recovery stage {index + 1} policy is invalid")
        raw = bytes.fromhex(stage["frame_hex"])
        digest = hashlib.sha256(raw).hexdigest()
        if digest != stage.get("frame_sha256", "").lower():
            raise PermissionError(f"prepare-recovery stage {index + 1} SHA-256 mismatch")
        frame = decode_duml_frame(raw)
        command = get_command_definition(command_name)
        validate_command_frame(command, frame)
        if (
            (frame.sender, frame.receiver, frame.sequence, frame.flags)
            != (0x02, 0x08, sequence, 0x40)
            or (frame.cmd_set, frame.cmd_id, frame.payload)
            != (cmd_set, cmd_id, wanted_payload)
            or not frame.crc8_valid
            or not frame.crc16_valid
            or frame.raw != raw
        ):
            raise PermissionError(f"prepare-recovery stage {index + 1} wire frame is invalid")
        loaded.append((stage, raw))
    return payload, loaded


async def run_prepare_recovery_session(
    address: str,
    *,
    proposal_path: Path,
    output_dir: Path,
    confirmation_callback: ConfirmationCallback,
    wifi_proposal_path: Path | None = None,
    wifi_sequence: int = 0x8C1A,
    stream_proposal_path: Path | None = None,
    start_proposal_path: Path | None = None,
    response_timeout: float = 15.0,
    passive_seconds: float = 10.0,
    transport_factory: Callable = BluezBleTransport,
) -> tuple[dict, bool]:
    if response_timeout <= 0 or passive_seconds < 0:
        raise ValueError("response_timeout must be positive and passive_seconds non-negative")
    _, stages = load_prepare_recovery_proposal(
        proposal_path, expected_address=address
    )
    wifi_stage: tuple[dict, bytes] | None = None
    if wifi_proposal_path is not None:
        from openframetap.devices.pocket3_livestream import load_fixed_wifi_proposal

        wifi_proposal, wifi_raw = load_fixed_wifi_proposal(
            wifi_proposal_path,
            expected_address=address,
            expected_sequence=wifi_sequence,
        )
        wifi_stage = (
            {
                **wifi_proposal,
                "stage": "wifi_retry_after_validated_prepare",
            },
            wifi_raw,
        )
    if (stream_proposal_path is None) != (start_proposal_path is None):
        raise ValueError("stream and start proposals must be supplied together")
    stream_stage: tuple[dict, bytes] | None = None
    start_stage: tuple[dict, bytes] | None = None
    if stream_proposal_path is not None:
        if wifi_stage is None:
            raise ValueError("stream stages require the same-session Wi-Fi stage")
        from openframetap.devices.pocket3_livestream import (
            load_fixed_start_transport_proposal,
            load_fixed_stream_proposal,
        )

        stream_proposal, stream_raw = load_fixed_stream_proposal(
            stream_proposal_path, expected_address=address
        )
        start_proposal, start_raw = load_fixed_start_transport_proposal(
            start_proposal_path, expected_address=address
        )
        stream_stage = ({**stream_proposal, "stage": "stream_configure"}, stream_raw)
        start_stage = ({**start_proposal, "stage": "stream_start"}, start_raw)
    output_dir.mkdir(parents=True, exist_ok=True)
    candidates_path = output_dir / "recovery-candidates.jsonl"
    recovery_events_path = output_dir / "recovery-events.jsonl"
    candidates_path.write_text("", encoding="utf-8")
    recovery_events_path.write_text("", encoding="utf-8")
    recorder = TelemetryRecorder(
        output_dir,
        address=address,
        operation=(
            "autonomous-reversible-full-stream-session"
            if stream_stage is not None
            else (
                "owner-confirmed-prepare-and-wifi-recovery-same-connection"
                if wifi_stage is not None
                else "owner-confirmed-prepare-recovery-same-connection"
            )
        ),
    )
    reassembler = DumlStreamReassembler()
    frames: asyncio.Queue[DumlFrame] = asyncio.Queue()

    def recovery_event(event: str, **fields) -> None:
        payload = _event(event, **fields)
        _jsonl(recovery_events_path, payload)
        print(f"RECOVERY_EVENT: {event} {json.dumps(fields, ensure_ascii=False)}", flush=True)

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
    sent: list[dict] = []
    result = "not_started"
    error: str | None = None
    connected_at_end = False
    stage1_response: dict | None = None
    stage2_response: dict | None = None
    wifi_response: dict | None = None
    stream_response: dict | None = None
    start_response: dict | None = None
    started = time.monotonic()

    async def propose_and_send_stage(
        stage_and_raw: tuple[dict, bytes], *, allow_denied_command: bool
    ) -> bool:
        stage, raw = stage_and_raw
        command = get_command_definition(stage["command"])
        digest = hashlib.sha256(raw).hexdigest()
        authorization = SendAuthorization.explicit_single_frame(
            command.name,
            frame_sha256=digest,
            purpose="owner-confirmed Pocket 3 prepare recovery only",
            approval_reference=f"interactive-frame-sha256:{digest}",
            allow_denied_command=allow_denied_command,
        )
        assert_send_allowed(command, authorization)
        candidate = {
            **_event("candidate_proposed"),
            "stage": stage["stage"],
            "command": command.name,
            "frame_sha256": digest,
            "frame_hex": raw.hex(),
            "decoded": decode_duml_frame(raw).to_dict(),
            "human_confirmed": False,
            "automatic_follow_up_frames": 0,
        }
        _jsonl(candidates_path, candidate)
        if not await _confirmed(confirmation_callback, candidate):
            recovery_event(
                "candidate_declined_or_confirmation_mismatch",
                stage=stage["stage"],
                frame_sha256=digest,
            )
            return False
        candidate["human_confirmed"] = True
        candidate["confirmed_at"] = datetime.now(timezone.utc).isoformat()
        _jsonl(candidates_path, candidate)
        await transport.send_frame(raw, command=command, authorization=authorization)
        sent.append(
            {
                "stage": stage["stage"],
                "command": command.name,
                "frame_sha256": digest,
            }
        )
        recovery_event(
            (
                "autonomous_reversible_fixed_frame_written"
                if stream_stage is not None
                else "human_confirmed_frame_written"
            ),
            stage=stage["stage"],
            frame_sha256=digest,
        )
        return True

    async def propose_and_send(index: int, *, allow_denied_command: bool) -> bool:
        return await propose_and_send_stage(
            stages[index], allow_denied_command=allow_denied_command
        )

    async def wait_exact_stage1() -> DumlFrame:
        deadline = time.monotonic() + response_timeout
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError("timeout waiting for exact FEAB C0/02/E1 payload 00")
            frame = await asyncio.wait_for(frames.get(), timeout=remaining)
            if (frame.sequence, frame.cmd_set, frame.cmd_id) != (0xFEAB, 0x02, 0xE1):
                continue
            if not (
                frame.sender == 0x08
                and frame.receiver == 0x02
                and frame.flags == 0xC0
                and frame.payload == b"\x00"
                and frame.crc8_valid
                and frame.crc16_valid
            ):
                raise RuntimeError("unexpected matching-sequence prepare stage1 response")
            return frame

    async def wait_exact_stage2() -> DumlFrame:
        deadline = time.monotonic() + response_timeout
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError("timeout waiting for exact FFAB 80/02/8E response")
            frame = await asyncio.wait_for(frames.get(), timeout=remaining)
            if (frame.sequence, frame.cmd_set, frame.cmd_id) != (0xFFAB, 0x02, 0x8E):
                continue
            if not (
                frame.sender == 0x08
                and frame.receiver == 0x02
                and frame.flags == 0x80
                and frame.payload.startswith(bytes.fromhex("0000011c00"))
                and frame.crc8_valid
                and frame.crc16_valid
            ):
                raise RuntimeError("unexpected matching-sequence prepare stage2 response")
            return frame

    async def observe_wifi_response(seconds: float) -> DumlFrame | None:
        deadline = time.monotonic() + seconds
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return None
            try:
                frame = await asyncio.wait_for(frames.get(), timeout=remaining)
            except TimeoutError:
                return None
            if (frame.sequence, frame.cmd_set, frame.cmd_id) != (
                wifi_sequence,
                0x07,
                0x47,
            ):
                continue
            if not (
                frame.sender == 0x07
                and frame.receiver == 0x02
                and frame.flags == 0xC0
                and frame.crc8_valid
                and frame.crc16_valid
            ):
                raise RuntimeError("unexpected matching-sequence Wi-Fi retry response")
            return frame

    async def wait_matching_response(
        *, sequence: int, cmd_set: int, cmd_id: int, sender: int
    ) -> DumlFrame:
        deadline = time.monotonic() + response_timeout
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError(
                    f"timeout waiting for {sequence:04X} C0/{cmd_set:02X}/{cmd_id:02X}"
                )
            frame = await asyncio.wait_for(frames.get(), timeout=remaining)
            if (frame.sequence, frame.cmd_set, frame.cmd_id) != (
                sequence,
                cmd_set,
                cmd_id,
            ):
                continue
            if not (
                frame.sender == sender
                and frame.receiver == 0x02
                and frame.flags == 0xC0
                and frame.crc8_valid
                and frame.crc16_valid
            ):
                raise RuntimeError("unexpected matching-sequence stream response")
            return frame

    try:
        await transport.connect()
        await transport.subscribe(notification_handler)
        await asyncio.wait_for(frames.get(), timeout=5.0)
        recovery_event("fff4_ready")
        if not await propose_and_send(0, allow_denied_command=False):
            result = "cancelled_before_stage1"
        else:
            response1 = await wait_exact_stage1()
            stage1_response = response1.to_dict()
            recovery_event("stage1_exact_ack", raw_hex=response1.raw.hex())
            if not await propose_and_send(1, allow_denied_command=True):
                result = "cancelled_before_stage2"
            else:
                response2 = await wait_exact_stage2()
                stage2_response = response2.to_dict()
                recovery_event("stage2_exact_response", raw_hex=response2.raw.hex())
                if wifi_stage is None:
                    result = "prepare_stage2_response_validated"
                    if passive_seconds:
                        await asyncio.sleep(passive_seconds)
                elif not await propose_and_send_stage(
                    wifi_stage, allow_denied_command=False
                ):
                    result = "cancelled_before_wifi_retry"
                else:
                    result = "wifi_retry_written_after_validated_prepare"
                    recovery_event("wifi_retry_written_after_validated_prepare")
                    response3 = await observe_wifi_response(passive_seconds)
                    if response3 is not None:
                        wifi_response = response3.to_dict()
                        recovery_event(
                            "wifi_matching_response",
                            raw_hex=response3.raw.hex(),
                            payload_hex=response3.payload.hex(),
                        )
                        result = "wifi_retry_response_observed"
                    if stream_stage is not None:
                        if response3 is None or response3.payload not in {
                            b"\x00\x00",
                            b"\x00\x00\x00",
                        }:
                            raise RuntimeError(
                                "same-session stream stages require explicit Wi-Fi success"
                            )
                        if not await propose_and_send_stage(
                            stream_stage, allow_denied_command=False
                        ):
                            result = "cancelled_before_stream_configure"
                        else:
                            response4 = await wait_matching_response(
                                sequence=0x8C2C,
                                cmd_set=0x08,
                                cmd_id=0x78,
                                sender=0x08,
                            )
                            stream_response = response4.to_dict()
                            recovery_event(
                                "stream_configure_response",
                                raw_hex=response4.raw.hex(),
                                payload_hex=response4.payload.hex(),
                            )
                            if not await propose_and_send_stage(
                                start_stage, allow_denied_command=True
                            ):
                                result = "cancelled_before_stream_start"
                            else:
                                response5 = await wait_matching_response(
                                    sequence=0xB4BB,
                                    cmd_set=0x02,
                                    cmd_id=0x8E,
                                    sender=0x08,
                                )
                                start_response = response5.to_dict()
                                recovery_event(
                                    "stream_start_response",
                                    raw_hex=response5.raw.hex(),
                                    payload_hex=response5.payload.hex(),
                                )
                                result = "full_stream_sequence_responses_observed"
                                if passive_seconds:
                                    await asyncio.sleep(passive_seconds)
        connected_at_end = transport.is_connected
    except (KeyboardInterrupt, asyncio.CancelledError):
        error = "cancelled"
        result = "cancelled"
        raise
    except Exception as exc:
        error = f"{type(exc).__name__}: {exc}"
        result = "failed"
        recovery_event("failed", error=error)
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
            pairing_requested=False,
            extra_summary={
                "prepare_recovery_same_connection": True,
                "authorization_model": (
                    "fixed_hash_autonomous_reversible_wrapper"
                    if stream_stage is not None
                    else "owner_invoked_fixed_wrapper"
                ),
                "command_invocation_authorized": True,
                "interactive_sha_prompts": 0,
                "each_fixed_frame_preapproved": True,
                "sent_frames": sent,
                "automatic_follow_up_frames": 0,
                "unapproved_follow_up_frames": 0,
                "preapproved_conditional_stage2": True,
                "preapproved_conditional_frame_count": (
                    4 if stream_stage is not None else (2 if wifi_stage is not None else 1)
                ),
                "wifi_frames_sent": sum(
                    item["command"] == "wifi_connect" for item in sent
                ),
                "rtmp_configuration_frames_sent": sum(
                    item["command"] == "configure_live_stream" for item in sent
                ),
                "rtmp_start_frames_sent": sum(
                    item["command"] == "start_live_stream_transport" for item in sent
                ),
                "bluez_pairing_requested": False,
                "recovery_result": result,
                "stage1_response": stage1_response,
                "stage2_response": stage2_response,
                "wifi_response": wifi_response,
                "stream_response": stream_response,
                "start_response": start_response,
            },
        )
    successful_results = {"prepare_stage2_response_validated"}
    if wifi_stage is not None:
        successful_results = {
            "wifi_retry_written_after_validated_prepare",
            "wifi_retry_response_observed",
        }
    if stream_stage is not None:
        successful_results = {"full_stream_sequence_responses_observed"}
    return summary, error is None and result in successful_results
