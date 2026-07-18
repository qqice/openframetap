from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path

from openframetap.ble_scan import load_replay, render_scan, scan, scan_payload
from openframetap.gatt_probe import probe, write_probe


def _write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


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
    return 2
