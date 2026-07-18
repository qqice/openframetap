from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys
import time

from openframetap.video.player_process import ProcessRegistry, redact_argv


def test_owned_process_start_stop_and_stale_pid_cleanup(tmp_path: Path) -> None:
    registry = ProcessRegistry(tmp_path / "runtime" / "media-processes.json")
    process = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])
    try:
        registry.register("preview", process, [sys.executable, "fixture"])
        assert registry.load()["preview"].pid == process.pid
        assert registry.stop("preview", timeout=0.1)
        process.wait(timeout=5)
        assert not registry.path.exists()
    finally:
        if process.poll() is None:
            process.kill()

    registry.path.parent.mkdir(parents=True, exist_ok=True)
    registry.path.write_text(
        '{"stale":{"name":"stale","pid":999999999,"argv":[],"started_at_monotonic_ns":1,"owner_uid":'
        + str(os.getuid() if hasattr(os, "getuid") else 0)
        + "}}"
    )
    assert registry.load() == {}
    assert not registry.path.exists()


def test_stop_all_does_not_touch_unregistered_process(tmp_path: Path) -> None:
    registry = ProcessRegistry(tmp_path / "runtime" / "media-processes.json")
    unrelated = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])
    try:
        assert registry.stop_all() == []
        time.sleep(0.05)
        assert unrelated.poll() is None
    finally:
        unrelated.terminate()
        unrelated.wait(timeout=5)


def test_process_registry_never_persists_or_reports_private_rtmp_key(tmp_path: Path) -> None:
    url = "rtmp://192.168.1.229:1935/live/private-key"
    assert redact_argv(["rtmpsrc", f"location={url}"])[1].endswith(
        "/live/<redacted>"
    )
    registry = ProcessRegistry(tmp_path / "runtime" / "media-processes.json")
    process = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])
    try:
        owned = registry.register("preview", process, ["rtmpsrc", f"location={url}"])
        assert "private-key" not in registry.path.read_text(encoding="utf-8")
        assert "private-key" not in str(owned.to_public_dict())
        registry.stop("preview", timeout=0.1)
        process.wait(timeout=5)
    finally:
        if process.poll() is None:
            process.kill()
