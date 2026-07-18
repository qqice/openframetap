from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]


def bash_path() -> str:
    if os.name == "nt":
        candidate = Path(r"C:\Program Files\Git\bin\bash.exe")
        if candidate.exists():
            return str(candidate)
    found = shutil.which("bash")
    if not found:
        pytest.skip("bash unavailable")
    return found


def shell_path(path: Path) -> str:
    path = path.resolve()
    if os.name == "nt":
        return f"/{path.drive[0].lower()}{path.as_posix()[2:]}"
    return str(path)


def run_bash(script: str, *, env: dict[str, str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [bash_path(), script],
        cwd=ROOT,
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )


def test_ssh_failure_status_propagates() -> None:
    env = os.environ.copy()
    env["OPENFRAMETAP_SSH_BIN"] = shell_path(ROOT / "tests/fixtures/fail-ssh.sh")
    env["ROCK4D_SSH_HOST"] = "fixture@example.invalid"
    result = subprocess.run(
        [bash_path(), "scripts/remote.sh", "command", "true"],
        cwd=ROOT,
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )
    assert result.returncode == 37
    assert "Exit status: 37" in result.stdout


def test_rsync_deploy_has_required_exclusions(tmp_path: Path) -> None:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    fake_rsync = bin_dir / "rsync"
    fake_rsync.write_text(
        '#!/usr/bin/env bash\nprintf "%s\\n" "$@" >"${RSYNC_ARGS_FILE:?}"\n',
        encoding="utf-8",
    )
    fake_rsync.chmod(0o755)
    args_file = tmp_path / "args.txt"
    env = os.environ.copy()
    env["PATH"] = f"{shell_path(bin_dir)}:/usr/bin:/bin"
    env["RSYNC_ARGS_FILE"] = shell_path(args_file)
    env["ROCK4D_SSH_HOST"] = "fixture@example.invalid"
    result = run_bash("scripts/deploy.sh", env=env)
    assert result.returncode == 0, result.stderr
    args = args_file.read_text(encoding="utf-8").splitlines()
    assert "--delete" in args
    for exclusion in (
        ".git/",
        ".venv/",
        "artifacts/",
        "__pycache__/",
        "*.pcap",
        "*.btsnoop",
        "*.log",
        ".pytest_cache/",
        "*.egg-info/",
    ):
        assert f"--exclude={exclusion}" in args


def test_scanner_failure_cleans_up_btmon(tmp_path: Path) -> None:
    start_file = tmp_path / "started"
    stop_file = tmp_path / "stopped"
    env = os.environ.copy()
    env["OPENFRAMETAP_BTMON_BIN"] = shell_path(ROOT / "tests/fixtures/fake-btmon.sh")
    env["OPENFRAMETAP_PYTHON_BIN"] = shell_path(ROOT / "tests/fixtures/fail-python.sh")
    env["OPENFRAMETAP_BTMON_USE_SUDO"] = "0"
    env["BTMON_START_FILE"] = shell_path(start_file)
    env["BTMON_STOP_FILE"] = shell_path(stop_file)
    env["FAKE_PYTHON_STATUS"] = "23"
    result = subprocess.run(
        [bash_path(), "scripts/capture-ble.sh", "1"],
        cwd=ROOT,
        env=env,
        text=True,
        capture_output=True,
        check=False,
        timeout=15,
    )
    assert result.returncode == 23
    assert start_file.exists()
    assert stop_file.read_text(encoding="utf-8") == "stopped"
    assert "ARTIFACT_STEM=ble-" in result.stdout


def test_ctrl_c_path_cleans_up_btmon(tmp_path: Path) -> None:
    start_file = tmp_path / "started"
    stop_file = tmp_path / "stopped"
    env = os.environ.copy()
    env["OPENFRAMETAP_BTMON_BIN"] = shell_path(ROOT / "tests/fixtures/fake-btmon.sh")
    env["OPENFRAMETAP_PYTHON_BIN"] = shell_path(ROOT / "tests/fixtures/interrupt-python.sh")
    env["OPENFRAMETAP_BTMON_USE_SUDO"] = "0"
    env["BTMON_START_FILE"] = shell_path(start_file)
    env["BTMON_STOP_FILE"] = shell_path(stop_file)
    result = subprocess.run(
        [bash_path(), "scripts/capture-ble.sh", "30"],
        cwd=ROOT,
        env=env,
        text=True,
        capture_output=True,
        check=False,
        timeout=15,
    )
    assert result.returncode != 0
    assert start_file.exists()
    assert stop_file.read_text(encoding="utf-8") == "stopped"


def test_pocket_listener_failure_cleans_up_btmon(tmp_path: Path) -> None:
    start_file = tmp_path / "started"
    stop_file = tmp_path / "stopped"
    env = os.environ.copy()
    env["OPENFRAMETAP_BTMON_BIN"] = shell_path(ROOT / "tests/fixtures/fake-btmon.sh")
    env["OPENFRAMETAP_PYTHON_BIN"] = shell_path(ROOT / "tests/fixtures/fail-python.sh")
    env["OPENFRAMETAP_BTMON_USE_SUDO"] = "0"
    env["BTMON_START_FILE"] = shell_path(start_file)
    env["BTMON_STOP_FILE"] = shell_path(stop_file)
    env["FAKE_PYTHON_STATUS"] = "29"
    result = subprocess.run(
        [
            bash_path(),
            "scripts/capture-pocket3.sh",
            "listen",
            "00:11:22:33:44:55",
            "1",
        ],
        cwd=ROOT,
        env=env,
        text=True,
        capture_output=True,
        check=False,
        timeout=15,
    )
    assert result.returncode == 29
    assert start_file.exists()
    assert stop_file.read_text(encoding="utf-8") == "stopped"
    assert "ARTIFACT_DIR=pocket3-listen-" in result.stdout
