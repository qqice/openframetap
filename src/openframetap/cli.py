from __future__ import annotations

import argparse
import asyncio
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import subprocess
import sys

from openframetap.ble_scan import load_replay, render_scan, scan, scan_payload
from openframetap.devices.pocket3 import POCKET3_PROFILE
from openframetap.gatt_probe import probe, write_probe
from openframetap.pairing.pocket3 import (
    write_pairing_proposal,
    write_pairing_stage1_proposal,
)
from openframetap.pairing.manual_session import run_manual_pairing_session
from openframetap.experiments.session import run_passive_experiment
from openframetap.protocol.commands import PAIRING_COMMANDS
from openframetap.protocol.duml import decode_duml_frame
from openframetap.protocol.reassembly import DumlStreamReassembler
from openframetap.session import listen_pocket3, manual_send_pocket3_frame


def _write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def _stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")


def _decode_file(path: Path) -> bytes:
    raw = path.read_bytes()
    try:
        text = raw.decode("ascii").strip()
    except UnicodeDecodeError:
        return raw
    compact = "".join(text.replace("0x", "").split())
    if compact and len(compact) % 2 == 0:
        try:
            return bytes.fromhex(compact)
        except ValueError:
            pass
    return raw


def _decode_stream(raw: bytes) -> tuple[list[dict], list[dict]]:
    reassembler = DumlStreamReassembler()
    events = reassembler.feed(raw) + reassembler.finish()
    frames = [event.frame.to_dict() for event in events if event.frame and event.kind == "frame"]
    errors = [
        {"reason": event.reason, "raw_hex": event.raw.hex()}
        for event in events
        if event.kind != "frame"
    ]
    return frames, errors


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="openframetap")
    subcommands = parser.add_subparsers(dest="command", required=True)
    ble = subcommands.add_parser("ble", help="BLE observation and human-gated single-frame tools")
    ble_commands = ble.add_subparsers(dest="ble_command", required=True)

    scan_parser = ble_commands.add_parser("scan", help="passively observe advertisements")
    scan_parser.add_argument("--seconds", type=int, default=30)
    scan_parser.add_argument("--json", dest="json_path", type=Path)
    scan_parser.add_argument("--replay", type=Path, help=argparse.SUPPRESS)

    probe_parser = ble_commands.add_parser("probe", help="enumerate GATT metadata without pairing")
    probe_parser.add_argument("address")
    probe_parser.add_argument("--json", dest="json_path", type=Path)
    probe_parser.add_argument("--timeout", type=float, default=20.0)

    listen_parser = ble_commands.add_parser("listen", help="subscribe to FFF4 without FFF5 writes")
    listen_parser.add_argument("address")
    listen_parser.add_argument("--seconds", type=int, default=60)
    listen_parser.add_argument("--output-dir", type=Path)

    manual_write = ble_commands.add_parser(
        "manual-write",
        help="internal runtime for one locally confirmed allowlisted frame",
    )
    manual_write.add_argument("address")
    manual_write.add_argument("--hex", dest="hex_data", required=True)
    manual_write.add_argument(
        "--command", dest="frame_command", choices=sorted(PAIRING_COMMANDS), required=True
    )
    manual_write.add_argument("--confirmed-sha256", required=True)
    manual_write.add_argument("--require-incoming-hex")
    manual_write.add_argument("--seconds", type=int, default=20)
    manual_write.add_argument("--output-dir", type=Path, required=True)

    duml = subcommands.add_parser("duml", help="offline DUML operations")
    duml_commands = duml.add_subparsers(dest="duml_command", required=True)
    decode_parser = duml_commands.add_parser("decode", help="decode one or more DUML frames")
    decode_source = decode_parser.add_mutually_exclusive_group(required=True)
    decode_source.add_argument("--hex", dest="hex_data")
    decode_source.add_argument("--file", type=Path)

    analyze = subcommands.add_parser("analyze", help="offline immutable-evidence analysis")
    analyze_commands = analyze.add_subparsers(dest="analyze_command", required=True)
    analyze_telemetry = analyze_commands.add_parser(
        "telemetry", help="analyze one passive Pocket 3 experiment directory"
    )
    analyze_telemetry.add_argument("experiment_directory", type=Path)
    analyze_telemetry.add_argument("--windows-ms", default="50,100,250")
    analyze_telemetry.add_argument("--battery-window-ms", type=float, default=1000.0)
    analyze_telemetry.add_argument(
        "--analysis-location", default=os.environ.get("OPENFRAMETAP_ANALYSIS_LOCATION", "local")
    )

    video = subcommands.add_parser("video", help="user-space RTMP ingest tools")
    video_commands = video.add_subparsers(dest="video_command", required=True)
    video_server = video_commands.add_parser("server", help="MediaMTX lifecycle")
    video_server_commands = video_server.add_subparsers(
        dest="video_server_command", required=True
    )
    video_server_commands.add_parser("doctor", help="read-only LAN audit plus start/stop test")
    video_server_commands.add_parser("start", help="start the user-owned RTMP listener")
    video_server_commands.add_parser("stop", help="stop only the PID owned by this runtime")
    video_server_commands.add_parser("status", help="report listener and PID status")
    video_self_test = video_commands.add_parser(
        "self-test", help="publish a local test source and read it back from RTMP"
    )
    video_self_test.add_argument("--url", required=True)
    video_self_test.add_argument("--private-output", type=Path, required=True)
    video_self_test.add_argument("--sanitized-output", type=Path, required=True)
    video_self_test.add_argument("--ffmpeg-bin")
    video_self_test.add_argument("--ffprobe-bin")

    pocket3 = subcommands.add_parser("pocket3", help="Pocket 3 application-layer operations")
    pocket3_commands = pocket3.add_subparsers(dest="pocket3_command", required=True)
    pair = pocket3_commands.add_parser("pair", help="DJI application-layer pairing")
    pair_commands = pair.add_subparsers(dest="pair_command", required=True)
    pair_status = pair_commands.add_parser(
        "status", help="listen for unsolicited pairing state without sending a query"
    )
    pair_status.add_argument("address")
    pair_status.add_argument("--seconds", type=int, default=10)
    pair_status.add_argument("--output-dir", type=Path)
    pair_start = pair_commands.add_parser(
        "start", help="prepare the first-write proposal; transmission requires approval"
    )
    pair_start.add_argument("address")
    pair_start.add_argument("--pin", default="5160")
    pair_start.add_argument("--proposal-dir", type=Path, default=Path("artifacts/local"))
    pair_stage1 = pair_commands.add_parser(
        "propose-stage1", help="offline-generate an ACK tied to one captured approval frame"
    )
    pair_stage1.add_argument("address")
    pair_stage1.add_argument("--approval-hex", required=True)
    pair_stage1.add_argument("--source-file", required=True)
    pair_stage1.add_argument("--source-sha256", required=True)
    pair_stage1.add_argument("--source-note", required=True)
    pair_stage1.add_argument("--proposal-dir", type=Path, default=Path("artifacts/local"))
    pair_manual = pair_commands.add_parser(
        "manual-session",
        help="session-bound pairing with a separate interactive SHA confirmation per frame",
    )
    pair_manual.add_argument("address")
    pair_manual.add_argument("--output-dir", type=Path, required=True)
    pair_manual.add_argument("--telemetry-seconds", type=float, default=60.0)

    telemetry = pocket3_commands.add_parser(
        "telemetry", help="subscribe and record notifications without active queries"
    )
    telemetry.add_argument("address")
    telemetry.add_argument("--seconds", type=int, default=60)
    telemetry.add_argument("--output-dir", type=Path)

    experiment = pocket3_commands.add_parser(
        "experiment", help="interactive FFF4-only telemetry experiment with human markers"
    )
    experiment.add_argument("--duration", type=int, default=180)
    experiment.add_argument("--output", type=Path, required=True)
    experiment.add_argument(
        "--address",
        default=os.environ.get(
            "POCKET3_BLE_ADDRESS", POCKET3_PROFILE.default_address
        ),
    )
    rtmp = pocket3_commands.add_parser("rtmp", help="human-gated Pocket 3 RTMP workflow")
    rtmp_commands = rtmp.add_subparsers(dest="rtmp_command", required=True)
    rtmp_commands.add_parser("plan", help="show the fail-closed persistent workflow")
    rtmp_status = rtmp_commands.add_parser("status", help="show persisted workflow state")
    rtmp_status.add_argument(
        "--state-file",
        type=Path,
        default=Path("artifacts/private/pocket3-rtmp-workflow.json"),
    )
    rtmp_propose = rtmp_commands.add_parser("propose", help="generate one offline proposal")
    rtmp_propose.add_argument("stage", choices=("prepare",))
    rtmp_propose.add_argument(
        "--address",
        default=os.environ.get("POCKET3_BLE_ADDRESS", POCKET3_PROFILE.default_address),
    )
    rtmp_propose.add_argument(
        "--private-root", type=Path, default=Path("artifacts/private/proposals")
    )
    rtmp_propose.add_argument(
        "--sanitized-root", type=Path, default=Path("artifacts/sanitized/proposals")
    )
    rtmp_propose.add_argument("--server-evidence-sha256", required=True)
    rtmp_propose.add_argument("--pairing-evidence", required=True)
    rtmp_propose.add_argument(
        "--state-file",
        type=Path,
        default=Path("artifacts/private/pocket3-rtmp-workflow.json"),
    )
    rtmp_send = rtmp_commands.add_parser(
        "send-approved", help="send one fixed proposal after full-SHA owner confirmation"
    )
    rtmp_send.add_argument("proposal", type=Path)
    rtmp_send.add_argument(
        "--address",
        default=os.environ.get("POCKET3_BLE_ADDRESS", POCKET3_PROFILE.default_address),
    )
    rtmp_send.add_argument("--seconds", type=int, default=15)
    rtmp_send.add_argument("--output-dir", type=Path, required=True)
    rtmp_send.add_argument(
        "--state-file",
        type=Path,
        default=Path("artifacts/private/pocket3-rtmp-workflow.json"),
    )
    rtmp_observe = rtmp_commands.add_parser(
        "observe", help="passively observe FFF4 without a DJI query"
    )
    rtmp_observe.add_argument(
        "--address",
        default=os.environ.get("POCKET3_BLE_ADDRESS", POCKET3_PROFILE.default_address),
    )
    rtmp_observe.add_argument("--seconds", type=int, default=30)
    rtmp_observe.add_argument("--output-dir", type=Path, required=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.command == "video" and args.video_command == "server":
        from openframetap.network.interfaces import NetworkPreflightError
        from openframetap.video.rtmp_server import make_server, server_doctor

        try:
            if args.video_server_command == "doctor":
                payload = server_doctor(lifecycle_check=True)
                ok = bool(payload.get("lifecycle_check", {}).get("started")) and bool(
                    payload.get("lifecycle_check", {}).get("stopped")
                )
            else:
                server = make_server()
                if args.video_server_command == "start":
                    payload = server.start().to_dict()
                    ok = payload["state"] == "running"
                elif args.video_server_command == "stop":
                    payload = server.stop().to_dict()
                    ok = payload["state"] == "stopped"
                else:
                    payload = server.status().to_dict()
                    ok = payload["state"] in {"running", "stopped"}
        except (FileNotFoundError, NetworkPreflightError, RuntimeError, TimeoutError) as exc:
            print(f"RTMP_SERVER_FAILED: {exc}")
            return 1
        print(json.dumps(payload, indent=2, ensure_ascii=False))
        return 0 if ok else 1
    if args.command == "video" and args.video_command == "self-test":
        from openframetap.video.sample_capture import SelfTestError, run_rtmp_selftest

        try:
            payload = run_rtmp_selftest(
                url=args.url,
                private_output=args.private_output,
                sanitized_output=args.sanitized_output,
                ffmpeg_bin=args.ffmpeg_bin,
                ffprobe_bin=args.ffprobe_bin,
            )
        except (OSError, SelfTestError, subprocess.SubprocessError) as exc:
            print(f"RTMP_SELF_TEST_FAILED: {exc}")
            return 1
        print(json.dumps(payload, indent=2, ensure_ascii=False))
        return 0
    if args.command == "ble" and args.ble_command == "scan":
        if args.seconds <= 0:
            raise SystemExit("--seconds must be positive")
        if args.replay:
            records = load_replay(args.replay)
            seconds = None
        else:
            records = asyncio.run(scan(args.seconds))
            seconds = args.seconds
        payload = scan_payload(records, seconds=seconds)
        if args.json_path:
            _write_json(args.json_path, payload)
        print(render_scan(payload))
        return 0
    if args.command == "ble" and args.ble_command == "probe":
        payload, ok = asyncio.run(probe(args.address, timeout=args.timeout))
        if args.json_path:
            write_probe(args.json_path, payload)
        print(json.dumps(payload, indent=2, ensure_ascii=False))
        return 0 if ok else 1
    if args.command == "ble" and args.ble_command == "listen":
        output_dir = args.output_dir or Path("artifacts") / f"pocket3-listen-{_stamp()}"
        payload, ok = asyncio.run(
            listen_pocket3(args.address, seconds=args.seconds, output_dir=output_dir)
        )
        print(json.dumps({"output_dir": str(output_dir), "summary": payload}, indent=2))
        return 0 if ok else 1
    if args.command == "ble" and args.ble_command == "manual-write":
        if os.environ.get("OPENFRAMETAP_USER_INITIATED") != "1":
            print(
                "REFUSED: use scripts/remote.sh pocket3-send-frame so the local human "
                "must enter the full SHA-256."
            )
            return 4
        try:
            raw = bytes.fromhex("".join(args.hex_data.replace("0x", "").split()))
            required_incoming_raw = (
                bytes.fromhex("".join(args.require_incoming_hex.replace("0x", "").split()))
                if args.require_incoming_hex
                else None
            )
        except ValueError as exc:
            raise SystemExit(f"invalid hexadecimal input: {exc}") from exc
        payload, ok = asyncio.run(
            manual_send_pocket3_frame(
                args.address,
                raw=raw,
                command_name=args.frame_command,
                confirmed_sha256=args.confirmed_sha256,
                seconds=args.seconds,
                output_dir=args.output_dir,
                required_incoming_raw=required_incoming_raw,
            )
        )
        print(json.dumps({"output_dir": str(args.output_dir), "summary": payload}, indent=2))
        return 0 if ok else 1
    if args.command == "duml" and args.duml_command == "decode":
        if args.hex_data is not None:
            try:
                raw = bytes.fromhex("".join(args.hex_data.replace("0x", "").split()))
            except ValueError as exc:
                raise SystemExit(f"invalid hexadecimal input: {exc}") from exc
        else:
            raw = _decode_file(args.file)
        frames, errors = _decode_stream(raw)
        if not frames and not errors:
            try:
                frames = [decode_duml_frame(raw).to_dict()]
            except ValueError as exc:
                errors = [{"reason": str(exc), "raw_hex": raw.hex()}]
        print(json.dumps({"frames": frames, "errors": errors}, indent=2, ensure_ascii=False))
        return 0 if frames and not errors else 1
    if args.command == "analyze" and args.analyze_command == "telemetry":
        from openframetap.analysis.report import analyze_experiment

        try:
            windows_ms = tuple(float(item) for item in args.windows_ms.split(","))
        except ValueError as exc:
            raise SystemExit("--windows-ms must be a comma-separated numeric list") from exc
        if not windows_ms or any(value < 0 for value in windows_ms):
            raise SystemExit("--windows-ms values must be non-negative")
        if args.battery_window_ms < 0:
            raise SystemExit("--battery-window-ms must be non-negative")
        try:
            payload = analyze_experiment(
                args.experiment_directory,
                windows_ms=windows_ms,
                battery_window_ms=args.battery_window_ms,
                analysis_git_head=os.environ.get("OPENFRAMETAP_GIT_HEAD", "unknown"),
                analysis_location=args.analysis_location,
            )
        except (FileNotFoundError, ValueError, RuntimeError) as exc:
            print(f"ANALYSIS_FAILED: {exc}")
            return 1
        print(json.dumps(payload, indent=2, ensure_ascii=False))
        return 0
    if args.command == "pocket3" and args.pocket3_command == "pair":
        if args.pair_command == "status":
            output_dir = args.output_dir or Path("artifacts") / f"pocket3-pair-status-{_stamp()}"
            payload, ok = asyncio.run(
                listen_pocket3(
                    args.address,
                    seconds=args.seconds,
                    output_dir=output_dir,
                    operation="pair-status-passive-listen-no-query",
                )
            )
            payload["bluez_pairing"] = "not requested"
            payload["dji_application_pairing"] = "unknown unless explicit status frame captured"
            print(json.dumps({"output_dir": str(output_dir), "summary": payload}, indent=2))
            return 0 if ok else 1
        if args.pair_command == "propose-stage1":
            try:
                approval_raw = bytes.fromhex(
                    "".join(args.approval_hex.replace("0x", "").split())
                )
            except ValueError as exc:
                raise SystemExit(f"invalid approval hexadecimal input: {exc}") from exc
            payload = write_pairing_stage1_proposal(
                args.proposal_dir,
                required_approval_raw=approval_raw,
                source={
                    "file": args.source_file,
                    "sha256": args.source_sha256.lower(),
                    "note": args.source_note,
                },
            )
            print(json.dumps(payload, indent=2, ensure_ascii=False))
            print("PROPOSAL ONLY: no BLE connection or write was attempted.")
            return 0
        if args.pair_command == "manual-session":
            if os.environ.get("OPENFRAMETAP_USER_INITIATED") != "1" or not sys.stdin.isatty():
                print(
                    "REFUSED: manual-session requires the owner-only interactive SSH wrapper "
                    "and a real terminal."
                )
                return 4

            async def confirm_candidate(candidate: dict) -> bool:
                print("\nCANDIDATE_FRAME_REQUIRES_HUMAN_CONFIRMATION", flush=True)
                print(json.dumps(candidate, indent=2, ensure_ascii=False), flush=True)
                prompt = (
                    f"Type full SHA-256 for {candidate['command']} to write this one frame, "
                    "or press Enter to stop: "
                )
                typed = await asyncio.to_thread(input, prompt)
                return typed.strip().lower() == candidate["frame_sha256"]

            payload, ok = asyncio.run(
                run_manual_pairing_session(
                    args.address,
                    output_dir=args.output_dir,
                    confirmation_callback=confirm_candidate,
                    telemetry_seconds=args.telemetry_seconds,
                )
            )
            print(json.dumps({"output_dir": str(args.output_dir), "summary": payload}, indent=2))
            return 0 if ok else 1
        if args.address.upper() != (POCKET3_PROFILE.default_address or "").upper():
            address_note = f"proposal target selected by caller: {args.address}"
        else:
            address_note = "proposal target matches profile default"
        payload = write_pairing_proposal(args.proposal_dir, pin=args.pin)
        payload["address_note"] = address_note
        print(json.dumps(payload, indent=2, ensure_ascii=False))
        print("REFUSED: no FFF5 write is permitted before explicit user approval.")
        return 3
    if args.command == "pocket3" and args.pocket3_command == "telemetry":
        output_dir = args.output_dir or Path("artifacts") / f"pocket3-telemetry-{_stamp()}"
        payload, ok = asyncio.run(
            listen_pocket3(
                args.address,
                seconds=args.seconds,
                output_dir=output_dir,
                operation="telemetry-listen-no-active-query",
            )
        )
        print(json.dumps({"output_dir": str(output_dir), "summary": payload}, indent=2))
        return 0 if ok else 1
    if args.command == "pocket3" and args.pocket3_command == "experiment":
        if args.duration <= 0:
            raise SystemExit("--duration must be positive")
        if not sys.stdin.isatty():
            print(
                "REFUSED: pocket3 experiment requires an interactive TTY on the ROCK 4D; "
                "no BLE connection was attempted."
            )
            return 4
        payload, ok = asyncio.run(
            run_passive_experiment(
                args.address,
                duration=args.duration,
                output_dir=args.output,
            )
        )
        print(json.dumps({"output_dir": str(args.output), "session": payload}, indent=2))
        return 0 if ok else 1
    if args.command == "pocket3" and args.pocket3_command == "rtmp":
        from openframetap.devices.pocket3_livestream import (
            load_fixed_proposal,
            write_prepare_proposal,
        )
        from openframetap.network.secrets import require_private_directory
        from openframetap.workflows.pocket3_rtmp import (
            Pocket3RtmpWorkflow,
            workflow_plan,
        )

        if args.rtmp_command == "plan":
            print(json.dumps(workflow_plan(), indent=2, ensure_ascii=False))
            return 0
        if args.rtmp_command == "status":
            workflow = Pocket3RtmpWorkflow.load(args.state_file)
            print(json.dumps(workflow.to_dict(), indent=2, ensure_ascii=False))
            return 0
        if args.rtmp_command == "propose":
            evidence_sha = args.server_evidence_sha256.lower()
            if len(evidence_sha) != 64 or any(ch not in "0123456789abcdef" for ch in evidence_sha):
                raise SystemExit("--server-evidence-sha256 must be 64 lowercase hex characters")
            workflow = Pocket3RtmpWorkflow.load(args.state_file)
            if workflow.phase != "preflight":
                print(f"REFUSED: prepare proposal requires preflight state, found {workflow.phase}")
                return 4
            workflow.transition(
                "server_ready", evidence={"sanitized_selftest_sha256": evidence_sha}
            )
            workflow.transition(
                "pairing_confirmed", evidence={"prior_pairing_evidence": args.pairing_evidence}
            )
            payload = write_prepare_proposal(
                address=args.address,
                private_root=args.private_root,
                sanitized_root=args.sanitized_root,
            )
            workflow.transition(
                "prepare_proposed",
                evidence={
                    "proposal_sha256": payload["frame_sha256"],
                    "locally_sent": False,
                },
            )
            workflow.save(args.state_file)
            safe = {key: value for key, value in payload.items() if key != "private_proposal"}
            safe["private_proposal"] = "artifacts/private/<redacted-proposal-path>"
            print(json.dumps(safe, indent=2, ensure_ascii=False))
            print("PROPOSAL ONLY: no BLE connection or FFF5 write was attempted.")
            return 0
        if args.rtmp_command == "send-approved":
            if os.environ.get("OPENFRAMETAP_USER_INITIATED") != "1" or not sys.stdin.isatty():
                print("REFUSED: send-approved requires the owner-only interactive wrapper and TTY.")
                return 4
            require_private_directory(args.output_dir)
            proposal, raw = load_fixed_proposal(
                args.proposal, expected_address=args.address
            )
            digest = proposal["frame_sha256"].lower()
            typed = input(
                f"Type the full SHA-256 for {proposal['command']} to send once, or Enter to stop: "
            ).strip().lower()
            if typed != digest:
                print("REFUSED: confirmation mismatch; no BLE connection was attempted.")
                return 4
            workflow = Pocket3RtmpWorkflow.load(args.state_file)
            if workflow.phase != "prepare_proposed":
                print(f"REFUSED: workflow is {workflow.phase}, expected prepare_proposed")
                return 4
            payload, ok = asyncio.run(
                manual_send_pocket3_frame(
                    args.address,
                    raw=raw,
                    command_name=proposal["command"],
                    confirmed_sha256=digest,
                    seconds=args.seconds,
                    output_dir=args.output_dir,
                )
            )
            if ok and payload.get("writes_attempted") == 1:
                workflow.transition(
                    "prepare_sent", evidence={"proposal_sha256": digest, "send_count": 1}
                )
                workflow.save(args.state_file)
            print(
                json.dumps(
                    {
                        "ok": ok,
                        "writes_attempted": payload.get("writes_attempted"),
                        "automatic_follow_up_frames": 0,
                        "private_output": str(args.output_dir),
                    },
                    indent=2,
                )
            )
            return 0 if ok else 1
        if args.rtmp_command == "observe":
            require_private_directory(args.output_dir)
            payload, ok = asyncio.run(
                listen_pocket3(
                    args.address,
                    seconds=args.seconds,
                    output_dir=args.output_dir,
                    operation="pocket3-rtmp-passive-observe-no-query",
                )
            )
            print(
                json.dumps(
                    {
                        "ok": ok,
                        "notifications_received": payload.get("notifications_received"),
                        "fff5_write_count": 0,
                        "private_output": str(args.output_dir),
                    },
                    indent=2,
                )
            )
            return 0 if ok else 1
    return 2
