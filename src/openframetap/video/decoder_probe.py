"""Read-only decoder and media-stack capability discovery."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
from typing import Callable


Runner = Callable[[list[str]], subprocess.CompletedProcess[str]]


@dataclass(slots=True)
class DecoderCapability:
    decoder_name: str
    framework: str
    hardware_or_software: str
    accepted_caps: list[str]
    output_caps: list[str]
    zero_copy_candidate: bool
    available: bool
    test_result: str = "not_tested"
    failure_reason: str | None = None
    plugin_filename: str | None = None
    plugin_version: str | None = None

    def to_dict(self) -> dict:
        return asdict(self)


GST_DECODERS = {
    "mppvideodec": "hardware",
    "v4l2h264dec": "hardware",
    "v4l2slh264dec": "hardware",
    "avdec_h264": "software",
}


def _default_runner(args: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(args, text=True, capture_output=True, check=False, timeout=20)


def parse_gst_inspect(
    name: str, text: str, *, returncode: int = 0, kind: str | None = None
) -> DecoderCapability:
    unavailable = returncode != 0 or "No such element or plugin" in text
    hardware_kind = kind or GST_DECODERS.get(name, "unknown")
    accepted: list[str] = []
    output: list[str] = []
    section = ""
    filename = None
    version = None
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if line.startswith("SINK template"):
            section = "sink"
        elif line.startswith("SRC template"):
            section = "src"
        elif line.startswith("Filename"):
            filename = line.split(None, 1)[1] if len(line.split(None, 1)) > 1 else None
        elif line.startswith("Version"):
            version = line.split(None, 1)[1] if len(line.split(None, 1)) > 1 else None
        elif line.startswith(("video/", "image/")):
            target = accepted if section == "sink" else output
            if line not in target:
                target.append(line)
    return DecoderCapability(
        decoder_name=name,
        framework="gstreamer",
        hardware_or_software=hardware_kind,
        accepted_caps=accepted,
        output_caps=output,
        zero_copy_candidate="memory:DMABuf" in text,
        available=not unavailable,
        failure_reason="plugin unavailable" if unavailable else None,
        plugin_filename=filename,
        plugin_version=version,
    )


def discover_decoders(runner: Runner = _default_runner) -> list[DecoderCapability]:
    capabilities: list[DecoderCapability] = []
    gst_inspect = shutil.which("gst-inspect-1.0")
    for name, kind in GST_DECODERS.items():
        if not gst_inspect:
            capabilities.append(
                DecoderCapability(
                    decoder_name=name,
                    framework="gstreamer",
                    hardware_or_software=kind,
                    accepted_caps=[],
                    output_caps=[],
                    zero_copy_candidate=False,
                    available=False,
                    failure_reason="gst-inspect-1.0 unavailable",
                )
            )
            continue
        result = runner([gst_inspect, name])
        capabilities.append(
            parse_gst_inspect(
                name, result.stdout + result.stderr, returncode=result.returncode, kind=kind
            )
        )
    ffmpeg = shutil.which("ffmpeg")
    if ffmpeg:
        result = runner([ffmpeg, "-hide_banner", "-decoders"])
        text = result.stdout + result.stderr
        for name, kind in (("h264", "software"), ("h264_rkmpp", "hardware"), ("h264_v4l2m2m", "hardware")):
            capabilities.append(
                DecoderCapability(
                    decoder_name=name,
                    framework="ffmpeg",
                    hardware_or_software=kind,
                    accepted_caps=["H.264"] if name in text else [],
                    output_caps=[],
                    zero_copy_candidate=False,
                    available=name in text,
                    failure_reason=None if name in text else "decoder unavailable",
                )
            )
    else:
        capabilities.append(
            DecoderCapability(
                decoder_name="h264",
                framework="ffmpeg",
                hardware_or_software="software",
                accepted_caps=[],
                output_caps=[],
                zero_copy_candidate=False,
                available=False,
                failure_reason="ffmpeg unavailable",
            )
        )
    return capabilities


def _capture(args: list[str]) -> dict:
    executable = shutil.which(args[0])
    if not executable:
        return {"command": args, "exit_code": 127, "stdout": "", "stderr": "unavailable"}
    result = subprocess.run(
        [executable, *args[1:]], text=True, capture_output=True, check=False, timeout=30
    )
    return {
        "command": args,
        "exit_code": result.returncode,
        "stdout": result.stdout,
        "stderr": result.stderr,
    }


def run_video_doctor(output_dir: Path) -> dict:
    output_dir.mkdir(parents=True, exist_ok=True)
    raw_dir = output_dir / "raw"
    raw_dir.mkdir(exist_ok=True)
    commands = [
        ["uname", "-a"],
        ["lsmod"],
        ["ffmpeg", "-hide_banner", "-hwaccels"],
        ["ffmpeg", "-hide_banner", "-decoders"],
        ["ffmpeg", "-hide_banner", "-filters"],
        ["gst-inspect-1.0"],
    ]
    captures = []
    for index, command in enumerate(commands):
        payload = _capture(command)
        captures.append(payload)
        (raw_dir / f"{index:02d}-{command[0]}.txt").write_text(
            payload["stdout"] + payload["stderr"], encoding="utf-8"
        )
    elements = {}
    for name in (
        "waylandsink",
        "kmssink",
        "glimagesink",
        "mppvideodec",
        "v4l2h264dec",
        "v4l2slh264dec",
        "avdec_h264",
        "rtmpsrc",
        "flvdemux",
        "h264parse",
        "rtspsrc",
        "rtph264depay",
        "fpsdisplaysink",
    ):
        capture = _capture(["gst-inspect-1.0", name])
        elements[name] = {
            "available": capture["exit_code"] == 0,
            "exit_code": capture["exit_code"],
        }
        (raw_dir / f"gst-{name}.txt").write_text(
            capture["stdout"] + capture["stderr"], encoding="utf-8"
        )
    payload = {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "decoders": [item.to_dict() for item in discover_decoders()],
        "elements": elements,
        "devices": {
            name: Path(name).exists()
            for name in ("/dev/mpp_service", "/dev/rga", "/dev/dri/renderD128")
        },
        "commands": [
            {"command": item["command"], "exit_code": item["exit_code"]}
            for item in captures
        ],
    }
    doctor_json = output_dir / "doctor.json"
    doctor_json.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    checksum_lines = []
    for path in sorted(p for p in output_dir.rglob("*") if p.is_file()):
        if path.name == "checksums.sha256":
            continue
        checksum_lines.append(
            f"{hashlib.sha256(path.read_bytes()).hexdigest()}  {path.relative_to(output_dir)}\n"
        )
    (output_dir / "checksums.sha256").write_text("".join(checksum_lines), encoding="ascii")
    return payload
