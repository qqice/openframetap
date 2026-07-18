from __future__ import annotations

import os
from pathlib import Path

import pytest

from openframetap.network.interfaces import NetworkPreflightError
from openframetap.video.rtmp_server import RtmpPaths, RtmpServer, media_mtx_config


def _fake_binary(path: Path) -> None:
    path.parent.mkdir(parents=True)
    if os.name == "nt":
        pytest.skip("process ownership fixture is POSIX-specific")
    path.write_text(
        "#!/bin/sh\n"
        "if [ \"${1:-}\" = --version ]; then echo vfixture; exit 0; fi\n"
        "trap 'exit 0' TERM INT\n"
        "while :; do sleep 1; done\n"
    )
    path.chmod(0o755)


def test_config_binds_only_requested_lan_address() -> None:
    rendered = media_mtx_config("192.168.1.229")
    assert "rtmpAddress: 192.168.1.229:1935" in rendered
    assert "0.0.0.0" not in rendered
    assert "100.125.223.67" not in rendered
    with pytest.raises(NetworkPreflightError):
        media_mtx_config("100.125.223.67")


def test_stale_pid_is_removed(tmp_path: Path) -> None:
    paths = RtmpPaths.under(tmp_path / "runtime")
    paths.root.mkdir(parents=True)
    paths.pid_file.write_text("999999999\n")
    status = RtmpServer(paths, "192.168.1.229").status()
    assert status.pid is None
    assert not paths.pid_file.exists()


def test_start_status_stop_and_pid_cleanup(tmp_path: Path, monkeypatch) -> None:
    paths = RtmpPaths.under(tmp_path / "runtime")
    _fake_binary(paths.binary)
    monkeypatch.setattr("openframetap.video.rtmp_server.port_is_available", lambda *_: True)
    monkeypatch.setattr("openframetap.video.rtmp_server.tcp_reachable", lambda *_args, **_kwargs: True)
    server = RtmpServer(paths, "192.168.1.229")
    started = server.start(timeout=2)
    assert started.state == "running"
    assert started.pid
    stopped = server.stop(timeout=3)
    assert stopped.state == "foreign_listener"  # mocked reachability remains true
    assert not paths.pid_file.exists()
