"""Local FFmpeg publisher/readback used to validate the ROCK RTMP ingest."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import time
from urllib.parse import urlsplit, urlunsplit

from openframetap.network.interfaces import is_rfc1918
from openframetap.network.secrets import require_private_directory
from openframetap.video.session import VideoSession
from openframetap.video.stream_probe import parse_ffprobe, redact_probe_metadata


class SelfTestError(RuntimeError):
    pass


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _terminate(process: subprocess.Popen, *, timeout: float = 3.0) -> None:
    if process.poll() is not None:
        return
    process.terminate()
    try:
        process.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=timeout)


def _tool(value: str | None, fallback: str) -> str:
    selected = value or shutil.which(fallback)
    if not selected:
        raise SelfTestError(f"required local tool is unavailable: {fallback}")
    return selected


def _sanitized_url(url: str) -> tuple[str, str]:
    parsed = urlsplit(url)
    if parsed.scheme != "rtmp" or not parsed.hostname or not is_rfc1918(parsed.hostname):
        raise SelfTestError("self-test URL must use the ROCK wlan0 RFC1918 address")
    parts = [part for part in parsed.path.split("/") if part]
    if len(parts) != 2 or parts[0] != "live":
        raise SelfTestError("self-test URL path must be /live/<key>")
    key = parts[1]
    sanitized = urlunsplit((parsed.scheme, parsed.netloc, "/live/<redacted>", "", ""))
    return sanitized, hashlib.sha256(key.encode()).hexdigest()


def _capture_existing_stream(
    *,
    url: str,
    private_dir: Path,
    sanitized_dir: Path,
    ffmpeg: str,
    ffprobe: str,
    duration_seconds: int,
    kind: str,
) -> dict:
    sanitized_url, key_hash = _sanitized_url(url)
    sample = private_dir / "sample.flv"
    frame_png = private_dir / "first-frame.png"
    probe_raw_path = private_dir / "ffprobe.json"
    probe_error_path = private_dir / "ffprobe.stderr.txt"
    capture_log = private_dir / "capture.stderr.txt"
    decode_log = private_dir / "decode.stderr.txt"
    session = VideoSession(kind=kind)
    parsed = urlsplit(url)
    session.listener_ipv4 = parsed.hostname
    session.listener_port = parsed.port or 1935

    probe = subprocess.run(
        [
            ffprobe,
            "-v",
            "error",
            "-rw_timeout",
            "5000000",
            "-show_format",
            "-show_streams",
            "-of",
            "json",
            url,
        ],
        text=True,
        capture_output=True,
        check=False,
        timeout=10,
    )
    probe_error_path.write_text(probe.stderr, encoding="utf-8")
    if probe.returncode != 0:
        raise SelfTestError(f"ffprobe failed with status {probe.returncode}")
    try:
        probe_payload = json.loads(probe.stdout)
    except json.JSONDecodeError as exc:
        raise SelfTestError("ffprobe returned invalid JSON") from exc
    probe_raw_path.write_text(json.dumps(probe_payload, indent=2) + "\n", encoding="utf-8")
    metadata = redact_probe_metadata(
        parse_ffprobe(probe_payload),
        {url: sanitized_url, urlsplit(url).path.split("/")[-1]: "<redacted>"},
    )
    if metadata["video"] is None:
        raise SelfTestError("ffprobe did not detect a video stream")
    session.publisher_connected = True
    session.media_detected = True

    capture = subprocess.run(
        [
            ffmpeg,
            "-hide_banner",
            "-loglevel",
            "error",
            "-rw_timeout",
            "5000000",
            "-i",
            url,
            "-t",
            str(duration_seconds),
            "-map",
            "0",
            "-c",
            "copy",
            "-y",
            str(sample),
        ],
        text=True,
        capture_output=True,
        check=False,
        timeout=duration_seconds + 7,
    )
    capture_log.write_text(capture.stderr, encoding="utf-8")
    if capture.returncode != 0 or not sample.is_file() or sample.stat().st_size == 0:
        raise SelfTestError(f"sample remux failed with status {capture.returncode}")
    session.sample_saved = True

    decode = subprocess.run(
        [
            ffmpeg,
            "-hide_banner",
            "-loglevel",
            "error",
            "-i",
            str(sample),
            "-frames:v",
            "100",
            "-f",
            "null",
            "-",
        ],
        text=True,
        capture_output=True,
        check=False,
        timeout=20,
    )
    decode_log.write_text(decode.stderr, encoding="utf-8")
    session.decode_ok = decode.returncode == 0
    session.decode_errors = len([line for line in decode.stderr.splitlines() if line.strip()])
    if not session.decode_ok:
        raise SelfTestError(f"software decode failed with status {decode.returncode}")

    png = subprocess.run(
        [
            ffmpeg,
            "-hide_banner",
            "-loglevel",
            "error",
            "-i",
            str(sample),
            "-frames:v",
            "1",
            "-y",
            str(frame_png),
        ],
        capture_output=True,
        check=False,
        timeout=15,
    )
    if png.returncode != 0 or not frame_png.is_file():
        raise SelfTestError(f"PNG extraction failed with status {png.returncode}")

    session.details = {
        "stream_key_sha256": key_hash,
        "sample_duration_seconds": duration_seconds,
        "sample_bytes": sample.stat().st_size,
        "sample_sha256": _sha256(sample),
        "first_frame_sha256": _sha256(frame_png),
        "metadata": metadata,
    }
    session.finish()
    private_session = session.to_dict() | {"receive_url": url}
    (private_dir / "session-private.json").write_text(
        json.dumps(private_session, indent=2) + "\n", encoding="utf-8"
    )
    summary = session.to_dict()
    summary["receive_url"] = sanitized_url
    (sanitized_dir / "session-summary.json").write_text(
        json.dumps(summary, indent=2) + "\n", encoding="utf-8"
    )
    (sanitized_dir / "stream-metadata.json").write_text(
        json.dumps(metadata, indent=2) + "\n", encoding="utf-8"
    )
    checksum_files = [probe_raw_path, sample, frame_png, private_dir / "session-private.json"]
    (private_dir / "checksums.sha256").write_text(
        "".join(f"{_sha256(path)}  {path.name}\n" for path in checksum_files),
        encoding="ascii",
    )
    return summary


def run_rtmp_live_capture(
    *,
    url: str,
    private_output: Path,
    sanitized_output: Path,
    duration_seconds: int = 8,
    ffmpeg_bin: str | None = None,
    ffprobe_bin: str | None = None,
) -> dict:
    """Read, remux, and decode an already-published private RTMP stream."""

    if not 1 <= duration_seconds <= 60:
        raise SelfTestError("live capture duration must be 1..60 seconds")
    ffmpeg = _tool(ffmpeg_bin, "ffmpeg")
    ffprobe = _tool(ffprobe_bin, "ffprobe")
    private_dir = require_private_directory(private_output)
    sanitized_dir = sanitized_output.resolve()
    if "sanitized" not in {part.lower() for part in sanitized_dir.parts}:
        raise SelfTestError("sanitized output must be under artifacts/sanitized")
    sanitized_dir.mkdir(parents=True, exist_ok=True)
    return _capture_existing_stream(
        url=url,
        private_dir=private_dir,
        sanitized_dir=sanitized_dir,
        ffmpeg=ffmpeg,
        ffprobe=ffprobe,
        duration_seconds=duration_seconds,
        kind="pocket3-live-rtmp-capture",
    )


def run_rtmp_selftest(
    *,
    url: str,
    private_output: Path,
    sanitized_output: Path,
    ffmpeg_bin: str | None = None,
    ffprobe_bin: str | None = None,
) -> dict:
    ffmpeg = _tool(ffmpeg_bin, "ffmpeg")
    ffprobe = _tool(ffprobe_bin, "ffprobe")
    _sanitized_url(url)
    private_dir = require_private_directory(private_output)
    sanitized_dir = sanitized_output.resolve()
    if "sanitized" not in {part.lower() for part in sanitized_dir.parts}:
        raise SelfTestError("sanitized output must be under artifacts/sanitized")
    sanitized_dir.mkdir(parents=True, exist_ok=True)
    producer_log = private_dir / "producer.stderr.txt"
    producer_command = [
        ffmpeg,
        "-hide_banner",
        "-loglevel",
        "warning",
        "-re",
        "-f",
        "lavfi",
        "-i",
        "testsrc2=size=640x360:rate=20",
        "-f",
        "lavfi",
        "-i",
        "sine=frequency=1000:sample_rate=48000",
        "-t",
        "18",
        "-c:v",
        "libx264",
        "-preset",
        "ultrafast",
        "-pix_fmt",
        "yuv420p",
        "-g",
        "40",
        "-b:v",
        "900k",
        "-c:a",
        "aac",
        "-b:a",
        "96k",
        "-f",
        "flv",
        url,
    ]
    with producer_log.open("wb") as producer_stderr:
        producer = subprocess.Popen(
            producer_command,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=producer_stderr,
        )
    try:
        # Windows FFmpeg can need more than two seconds to initialize libx264
        # and send the RTMP publish command. Readers fail immediately when a
        # MediaMTX path does not exist yet, so stay on the deterministic side
        # of that startup boundary instead of racing the publisher.
        time.sleep(4.0)
        if producer.poll() is not None:
            raise SelfTestError(f"RTMP producer exited early with status {producer.returncode}")
        return _capture_existing_stream(
            url=url,
            private_dir=private_dir,
            sanitized_dir=sanitized_dir,
            ffmpeg=ffmpeg,
            ffprobe=ffprobe,
            duration_seconds=7,
            kind="local-rtmp-self-test",
        )
    finally:
        _terminate(producer)
