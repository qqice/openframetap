from __future__ import annotations

import argparse
import asyncio
from datetime import datetime, timezone
import json
from pathlib import Path

from openframetap.ble_scan import load_replay, render_scan, scan, scan_payload
from openframetap.devices.pocket3 import POCKET3_PROFILE
from openframetap.gatt_probe import probe, write_probe
from openframetap.pairing.pocket3 import write_pairing_proposal
from openframetap.protocol.duml import decode_duml_frame
from openframetap.protocol.reassembly import DumlStreamReassembler
from openframetap.session import listen_pocket3


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
    ble = subcommands.add_parser("ble", help="read-only BLE operations")
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

    duml = subcommands.add_parser("duml", help="offline DUML operations")
    duml_commands = duml.add_subparsers(dest="duml_command", required=True)
    decode_parser = duml_commands.add_parser("decode", help="decode one or more DUML frames")
    decode_source = decode_parser.add_mutually_exclusive_group(required=True)
    decode_source.add_argument("--hex", dest="hex_data")
    decode_source.add_argument("--file", type=Path)

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

    telemetry = pocket3_commands.add_parser(
        "telemetry", help="subscribe and record notifications without active queries"
    )
    telemetry.add_argument("address")
    telemetry.add_argument("--seconds", type=int, default=60)
    telemetry.add_argument("--output-dir", type=Path)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
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
    return 2
