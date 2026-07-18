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
    rtmp_propose.add_argument("stage", choices=("prepare", "wifi", "prepare-recovery"))
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
    rtmp_propose.add_argument("--server-evidence-sha256")
    rtmp_propose.add_argument("--pairing-evidence")
    rtmp_propose.add_argument("--secret-file", type=Path)
    rtmp_propose.add_argument("--prepare-result", type=Path)
    rtmp_propose.add_argument("--wifi-result", type=Path)
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
    rtmp_recover_prepare = rtmp_commands.add_parser(
        "recover-prepare",
        help="run the fixed two-stage prepare recovery authorized by command invocation",
    )
    rtmp_recover_prepare.add_argument("proposal", type=Path)
    rtmp_recover_prepare.add_argument(
        "--address",
        default=os.environ.get("POCKET3_BLE_ADDRESS", POCKET3_PROFILE.default_address),
    )
    rtmp_recover_prepare.add_argument("--seconds", type=int, default=10)
    rtmp_recover_prepare.add_argument("--response-timeout", type=float, default=15.0)
    rtmp_recover_prepare.add_argument("--output-dir", type=Path, required=True)
    rtmp_recover_prepare.add_argument(
        "--state-file",
        type=Path,
        default=Path("artifacts/private/pocket3-rtmp-workflow.json"),
    )
    rtmp_analyze_prepare = rtmp_commands.add_parser(
        "analyze-prepare", help="offline-validate a captured prepare response"
    )
    rtmp_analyze_prepare.add_argument("capture_dir", type=Path)
    rtmp_analyze_prepare.add_argument("--sanitized-output", type=Path, required=True)
    rtmp_analyze_prepare.add_argument(
        "--state-file",
        type=Path,
        default=Path("artifacts/private/pocket3-rtmp-workflow.json"),
    )
    rtmp_analyze_wifi = rtmp_commands.add_parser(
        "analyze-wifi", help="offline-validate a captured Wi-Fi provisioning result"
    )
    rtmp_analyze_wifi.add_argument("capture_dir", type=Path)
    rtmp_analyze_wifi.add_argument("--sanitized-output", type=Path, required=True)
    rtmp_analyze_wifi.add_argument(
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
    secrets = subcommands.add_parser("secrets", help="owner-only private secret setup")
    secret_commands = secrets.add_subparsers(dest="secrets_command", required=True)
    configure_wifi = secret_commands.add_parser(
        "configure-wifi", help="prompt without echo and store a 0600 Wi-Fi secret file"
    )
    configure_wifi.add_argument(
        "--path", type=Path, default=Path("~/.config/openframetap/secrets.env")
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.command == "secrets" and args.secrets_command == "configure-wifi":
        if os.environ.get("OPENFRAMETAP_USER_INITIATED") != "1" or not sys.stdin.isatty():
            print("REFUSED: Wi-Fi secret setup requires the owner at an interactive TTY.")
            return 4
        import getpass

        from openframetap.network.secrets import (
            WifiProvisioningSecrets,
            load_wifi_provisioning_secrets,
            store_wifi_provisioning_secrets,
        )

        ssid = getpass.getpass("Test-network SSID (input hidden): ")
        psk = getpass.getpass("Test-network Wi-Fi PSK (input hidden): ")
        confirmation = getpass.getpass("Repeat Wi-Fi PSK (input hidden): ")
        if psk != confirmation:
            print("REFUSED: Wi-Fi PSK confirmation did not match; no file was changed.")
            return 4
        secret_values = load_wifi_provisioning_secrets(
            environ={
                "OPENFRAMETAP_WIFI_SSID": ssid,
                "OPENFRAMETAP_WIFI_PSK": psk,
            }
        )
        path = args.path.expanduser()
        backup = store_wifi_provisioning_secrets(
            path,
            WifiProvisioningSecrets(secret_values.ssid, secret_values.psk),
        )
        print(f"Wi-Fi secrets stored privately: {path} mode=0600")
        if backup is not None:
            print(f"Previous private file backed up: {backup} mode=0600")
        print("No Pocket connection or BLE write was attempted.")
        return 0
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
            load_fixed_wifi_proposal,
            write_prepare_recovery_proposal,
            write_prepare_proposal,
            write_wifi_proposal,
        )
        from openframetap.network.secrets import (
            load_wifi_provisioning_secrets,
            require_private_directory,
        )
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
        if args.rtmp_command == "analyze-prepare":
            from openframetap.workflows.prepare_analysis import (
                analyze_prepare_capture,
                write_sanitized_prepare_analysis,
            )

            workflow = Pocket3RtmpWorkflow.load(args.state_file)
            if workflow.phase not in {"prepare_sent", "prepare_acknowledged"}:
                print(
                    f"REFUSED: prepare analysis requires prepare_sent state, found {workflow.phase}"
                )
                return 4
            proposal_sha = next(
                (
                    entry.get("evidence", {}).get("proposal_sha256")
                    for entry in reversed(workflow.history)
                    if entry.get("evidence", {}).get("proposal_sha256")
                ),
                None,
            )
            if not proposal_sha:
                print("REFUSED: workflow does not contain the approved proposal SHA-256")
                return 4
            result = analyze_prepare_capture(
                args.capture_dir, expected_frame_sha256=proposal_sha
            )
            write_sanitized_prepare_analysis(result, args.sanitized_output)
            response_sha = result["response"]["frame_sha256"]
            if workflow.phase == "prepare_sent":
                workflow.transition(
                    "prepare_acknowledged",
                    evidence={
                        "capture_directory_name": result["capture_directory_name"],
                        "response_frame_sha256": response_sha,
                        "response_latency_ms": result["response_latency_ms"],
                        "writes_attempted": 1,
                        "automatic_follow_up_frames": 0,
                    },
                )
                workflow.save(args.state_file)
            print(json.dumps(result, indent=2, ensure_ascii=False))
            return 0
        if args.rtmp_command == "analyze-wifi":
            from openframetap.workflows.wifi_analysis import (
                analyze_wifi_capture,
                write_sanitized_wifi_analysis,
            )

            workflow = Pocket3RtmpWorkflow.load(args.state_file)
            if workflow.phase not in {"wifi_sent", "wifi_connected_or_unknown"}:
                print(
                    f"REFUSED: Wi-Fi analysis requires wifi_sent state, found {workflow.phase}"
                )
                return 4
            proposal_sha = next(
                (
                    entry.get("evidence", {}).get("proposal_sha256")
                    for entry in reversed(workflow.history)
                    if entry.get("to") in {"wifi_proposed", "wifi_sent"}
                    and entry.get("evidence", {}).get("proposal_sha256")
                ),
                None,
            )
            if not proposal_sha:
                print("REFUSED: workflow does not contain the approved Wi-Fi proposal SHA-256")
                return 4
            result = analyze_wifi_capture(
                args.capture_dir, expected_frame_sha256=proposal_sha
            )
            write_sanitized_wifi_analysis(result, args.sanitized_output)
            if workflow.phase == "wifi_sent":
                workflow.transition(
                    "wifi_connecting",
                    evidence={
                        "capture_directory_name": result["capture_directory_name"],
                        "application_frames_written": 1,
                        "att_write_command_chunks": result["att_write_command_chunks"],
                    },
                )
                workflow.transition(
                    "wifi_connected_or_unknown",
                    evidence={
                        "analysis_status": result["status"],
                        "matching_sequence_response_count": result[
                            "matching_sequence_response_count"
                        ],
                        "automatic_follow_up_frames": 0,
                    },
                )
                workflow.save(args.state_file)
            print(json.dumps(result, indent=2, ensure_ascii=False))
            return 0
        if args.rtmp_command == "propose":
            workflow = Pocket3RtmpWorkflow.load(args.state_file)
            if args.stage == "prepare":
                if not args.server_evidence_sha256 or not args.pairing_evidence:
                    raise SystemExit(
                        "prepare requires --server-evidence-sha256 and --pairing-evidence"
                    )
                evidence_sha = args.server_evidence_sha256.lower()
                if len(evidence_sha) != 64 or any(
                    ch not in "0123456789abcdef" for ch in evidence_sha
                ):
                    raise SystemExit(
                        "--server-evidence-sha256 must be 64 lowercase hex characters"
                    )
                pairing_digest = args.pairing_evidence.rsplit(":", 1)[-1].lower()
                if len(pairing_digest) != 64 or any(
                    ch not in "0123456789abcdef" for ch in pairing_digest
                ):
                    raise SystemExit(
                        "--pairing-evidence must end with a 64-character lowercase SHA-256"
                    )
                if workflow.phase != "preflight":
                    print(
                        "REFUSED: prepare proposal requires preflight state, "
                        f"found {workflow.phase}"
                    )
                    return 4
                workflow.transition(
                    "server_ready", evidence={"sanitized_selftest_sha256": evidence_sha}
                )
                workflow.transition(
                    "pairing_confirmed",
                    evidence={"prior_pairing_evidence": args.pairing_evidence},
                )
                payload = write_prepare_proposal(
                    address=args.address,
                    private_root=args.private_root,
                    sanitized_root=args.sanitized_root,
                    server_evidence_sha256=evidence_sha,
                    pairing_evidence=args.pairing_evidence,
                )
                workflow.transition(
                    "prepare_proposed",
                    evidence={
                        "proposal_sha256": payload["frame_sha256"],
                        "locally_sent": False,
                    },
                )
            elif args.stage == "wifi":
                if workflow.phase != "prepare_acknowledged":
                    print(
                        "REFUSED: Wi-Fi proposal requires prepare_acknowledged state, "
                        f"found {workflow.phase}"
                    )
                    return 4
                if args.secret_file is None or args.prepare_result is None:
                    raise SystemExit("wifi requires --secret-file and --prepare-result")
                prepare_result = json.loads(args.prepare_result.read_text(encoding="utf-8"))
                if (
                    prepare_result.get("status") != "prepare_acknowledged"
                    or prepare_result.get("response", {}).get("cmd_set") != "0x02"
                    or prepare_result.get("response", {}).get("cmd_id") != "0xE1"
                    or prepare_result.get("response", {}).get("payload_hex") != "00"
                ):
                    raise SystemExit("--prepare-result does not contain the validated ACK")
                import hashlib

                prepare_result_sha = hashlib.sha256(args.prepare_result.read_bytes()).hexdigest()
                secrets = load_wifi_provisioning_secrets(secret_file=args.secret_file)
                payload = write_wifi_proposal(
                    address=args.address,
                    secrets=secrets,
                    private_root=args.private_root,
                    sanitized_root=args.sanitized_root,
                    prepare_result_sha256=prepare_result_sha,
                )
                workflow.transition(
                    "wifi_proposed",
                    evidence={
                        "proposal_sha256": payload["frame_sha256"],
                        "prepare_result_sha256": prepare_result_sha,
                        "locally_sent": False,
                        "contains_sensitive_data": True,
                    },
                )
                workflow.save(args.state_file)
            else:
                if workflow.phase != "wifi_connected_or_unknown":
                    print(
                        "REFUSED: prepare recovery requires wifi_connected_or_unknown "
                        f"state, found {workflow.phase}"
                    )
                    return 4
                if args.wifi_result is None:
                    raise SystemExit("prepare-recovery requires --wifi-result")
                wifi_result = json.loads(args.wifi_result.read_text(encoding="utf-8"))
                if (
                    wifi_result.get("status") != "no_protocol_response_observed"
                    or wifi_result.get("matching_sequence_response_count") != 0
                    or wifi_result.get("application_frames_written") != 1
                ):
                    raise SystemExit(
                        "--wifi-result does not contain the validated no-response result"
                    )
                import hashlib

                wifi_result_sha = hashlib.sha256(args.wifi_result.read_bytes()).hexdigest()
                payload = write_prepare_recovery_proposal(
                    address=args.address,
                    private_root=args.private_root,
                    sanitized_root=args.sanitized_root,
                    wifi_result_sha256=wifi_result_sha,
                )
            if args.stage == "prepare":
                workflow.save(args.state_file)
            safe = {key: value for key, value in payload.items() if key != "private_proposal"}
            safe["private_proposal"] = "artifacts/private/<redacted-proposal-path>"
            print(json.dumps(safe, indent=2, ensure_ascii=False))
            print(f"PROPOSAL_STEM={Path(payload['sanitized_proposal']).parent.name}")
            print("PROPOSAL ONLY: no BLE connection or FFF5 write was attempted.")
            return 0
        if args.rtmp_command == "send-approved":
            if os.environ.get("OPENFRAMETAP_USER_INITIATED") != "1" or not sys.stdin.isatty():
                print("REFUSED: send-approved requires the owner-only interactive wrapper and TTY.")
                return 4
            require_private_directory(args.output_dir)
            proposal_header = json.loads(args.proposal.read_text(encoding="utf-8"))
            if proposal_header.get("stage") == "wifi":
                proposal, raw = load_fixed_wifi_proposal(
                    args.proposal, expected_address=args.address
                )
                expected_phase = "wifi_proposed"
                sent_phase = "wifi_sent"
            elif proposal_header.get("stage") == "prepare":
                proposal, raw = load_fixed_proposal(
                    args.proposal, expected_address=args.address
                )
                expected_phase = "prepare_proposed"
                sent_phase = "prepare_sent"
            else:
                print("REFUSED: proposal stage is not in the single-send allowlist")
                return 4
            digest = proposal["frame_sha256"].lower()
            workflow = Pocket3RtmpWorkflow.load(args.state_file)
            if workflow.phase != expected_phase:
                print(f"REFUSED: workflow is {workflow.phase}, expected {expected_phase}")
                return 4
            typed = input(
                f"Type the full SHA-256 for {proposal['command']} to send once, or Enter to stop: "
            ).strip().lower()
            if typed != digest:
                print("REFUSED: confirmation mismatch; no BLE connection was attempted.")
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
                    sent_phase,
                    evidence={
                        "proposal_sha256": digest,
                        "send_count": 1,
                        "automatic_follow_up_frames": 0,
                    },
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
        if args.rtmp_command == "recover-prepare":
            if os.environ.get("OPENFRAMETAP_USER_INITIATED") != "1" or not sys.stdin.isatty():
                print(
                    "REFUSED: recover-prepare requires the owner to invoke the fixed "
                    "interactive wrapper from a real TTY."
                )
                return 4
            from openframetap.workflows.prepare_recovery_session import (
                load_prepare_recovery_proposal,
                run_prepare_recovery_session,
            )

            require_private_directory(args.output_dir)
            workflow = Pocket3RtmpWorkflow.load(args.state_file)
            if workflow.phase != "wifi_connected_or_unknown":
                print(
                    "REFUSED: prepare recovery requires wifi_connected_or_unknown state, "
                    f"found {workflow.phase}"
                )
                return 4
            _proposal, stages = load_prepare_recovery_proposal(
                args.proposal, expected_address=args.address
            )
            print("OWNER-INVOKED FIXED PREPARE RECOVERY")
            print("Command invocation authorizes only the following two fixed frames:")
            for index, (stage, raw) in enumerate(stages, start=1):
                print(
                    f"  Stage {index}: {stage['command']} sha256={stage['frame_sha256']} "
                    f"hex={raw.hex()}"
                )
            print(
                "Stage 2 is attempted once only after the exact Stage 1 ACK in the same "
                "BLE connection. No Wi-Fi retry or other DJI command is included."
            )

            def invocation_authorizes_fixed_candidate(_candidate: dict) -> bool:
                return True

            payload, ok = asyncio.run(
                run_prepare_recovery_session(
                    args.address,
                    proposal_path=args.proposal,
                    output_dir=args.output_dir,
                    confirmation_callback=invocation_authorizes_fixed_candidate,
                    response_timeout=args.response_timeout,
                    passive_seconds=args.seconds,
                )
            )
            print(
                json.dumps(
                    {
                        "ok": ok,
                        "recovery_result": payload.get("recovery_result"),
                        "writes_attempted": payload.get("writes_attempted"),
                        "sent_frames": payload.get("sent_frames"),
                        "wifi_frames_sent": payload.get("wifi_frames_sent"),
                        "automatic_follow_up_frames": payload.get(
                            "automatic_follow_up_frames"
                        ),
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
