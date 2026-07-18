from __future__ import annotations

import json
from pathlib import Path
import subprocess

import pytest

from openframetap.video.sample_capture import SelfTestError, run_rtmp_live_capture


URL = "rtmp://192.168.1.229:1935/live/fixture-private-key"


def test_live_capture_saves_private_media_and_redacts_summary(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    probe_payload = {
        "streams": [
            {
                "codec_type": "video",
                "codec_name": "h264",
                "profile": "High",
                "width": 1280,
                "height": 720,
                "pix_fmt": "yuv420p",
                "avg_frame_rate": "30000/1001",
                "r_frame_rate": "30000/1001",
            },
            {
                "codec_type": "audio",
                "codec_name": "aac",
                "sample_rate": "48000",
                "channels": 2,
            },
        ],
        "format": {"filename": URL, "format_name": "flv"},
    }

    def fake_run(args: list[str], **_kwargs) -> subprocess.CompletedProcess:
        if args[0] == "ffprobe-fixture":
            return subprocess.CompletedProcess(
                args, 0, stdout=json.dumps(probe_payload), stderr=""
            )
        if "-c" in args and "copy" in args:
            Path(args[-1]).write_bytes(b"fixture-flv")
        elif "-frames:v" in args and args[args.index("-frames:v") + 1] == "1":
            Path(args[-1]).write_bytes(b"fixture-png")
        return subprocess.CompletedProcess(args, 0, stdout="", stderr="")

    monkeypatch.setattr("openframetap.video.sample_capture.subprocess.run", fake_run)
    private_dir = tmp_path / "artifacts" / "private" / "capture"
    sanitized_dir = tmp_path / "artifacts" / "sanitized" / "capture"
    summary = run_rtmp_live_capture(
        url=URL,
        private_output=private_dir,
        sanitized_output=sanitized_dir,
        duration_seconds=8,
        ffmpeg_bin="ffmpeg-fixture",
        ffprobe_bin="ffprobe-fixture",
    )

    assert summary["kind"] == "pocket3-live-rtmp-capture"
    assert summary["publisher_connected"] is True
    assert summary["media_detected"] is True
    assert summary["sample_saved"] is True
    assert summary["decode_ok"] is True
    assert summary["details"]["metadata"]["video"]["width"] == 1280
    assert (private_dir / "sample.flv").read_bytes() == b"fixture-flv"
    assert (private_dir / "first-frame.png").read_bytes() == b"fixture-png"
    private_text = (private_dir / "session-private.json").read_text(encoding="utf-8")
    sanitized_text = (sanitized_dir / "session-summary.json").read_text(encoding="utf-8")
    assert URL in private_text
    assert "fixture-private-key" not in sanitized_text
    assert "/live/<redacted>" in sanitized_text
    assert len((private_dir / "checksums.sha256").read_text().splitlines()) == 4


def test_live_capture_rejects_unbounded_duration(tmp_path: Path) -> None:
    with pytest.raises(SelfTestError, match="1..60"):
        run_rtmp_live_capture(
            url=URL,
            private_output=tmp_path / "artifacts" / "private" / "capture",
            sanitized_output=tmp_path / "artifacts" / "sanitized" / "capture",
            duration_seconds=61,
            ffmpeg_bin="unused",
            ffprobe_bin="unused",
        )
