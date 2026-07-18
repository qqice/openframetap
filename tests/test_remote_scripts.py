from __future__ import annotations

import json
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


def test_manual_frame_confirmation_mismatch_never_calls_ssh(tmp_path: Path) -> None:
    frame = tmp_path / "frame.bin"
    frame.write_bytes(bytes.fromhex("550e046604026b1300041c48e5e2"))
    env = os.environ.copy()
    env["OPENFRAMETAP_SSH_BIN"] = shell_path(ROOT / "tests/fixtures/fail-ssh.sh")
    env["ROCK4D_SSH_HOST"] = "fixture@example.invalid"
    result = subprocess.run(
        [
            bash_path(),
            "scripts/remote.sh",
            "pocket3-send-frame",
            shell_path(frame),
            "set_pairing_pin",
            "1",
        ],
        cwd=ROOT,
        env=env,
        input="wrong-confirmation\n",
        text=True,
        capture_output=True,
        check=False,
        timeout=15,
    )
    assert result.returncode == 4
    assert "no deployment or BLE connection was attempted" in result.stderr
    assert "Exit status: 37" not in result.stdout


def test_manual_pair_session_requires_a_real_terminal() -> None:
    env = os.environ.copy()
    env["OPENFRAMETAP_SSH_BIN"] = shell_path(ROOT / "tests/fixtures/fail-ssh.sh")
    env["ROCK4D_SSH_HOST"] = "fixture@example.invalid"
    result = subprocess.run(
        [bash_path(), "scripts/remote.sh", "pocket3-manual-pair-session", "1"],
        cwd=ROOT,
        env=env,
        input="RUN\n",
        text=True,
        capture_output=True,
        check=False,
        timeout=15,
    )
    assert result.returncode == 4
    assert "requires the device owner at a real terminal" in result.stderr
    assert "Exit status: 37" not in result.stdout


def test_pocket_experiment_requires_a_real_terminal() -> None:
    env = os.environ.copy()
    env["OPENFRAMETAP_SSH_BIN"] = shell_path(ROOT / "tests/fixtures/fail-ssh.sh")
    env["ROCK4D_SSH_HOST"] = "fixture@example.invalid"
    result = subprocess.run(
        [bash_path(), "scripts/remote.sh", "pocket3-experiment", "1"],
        cwd=ROOT,
        env=env,
        text=True,
        capture_output=True,
        check=False,
        timeout=15,
    )
    assert result.returncode == 4
    assert "requires the device owner at a real terminal" in result.stderr
    assert "Exit status: 37" not in result.stdout


def test_experiment_tty_refusal_still_cleans_up_btmon(tmp_path: Path) -> None:
    start_file = tmp_path / "started"
    stop_file = tmp_path / "stopped"
    env = os.environ.copy()
    env["OPENFRAMETAP_BTMON_BIN"] = shell_path(ROOT / "tests/fixtures/fake-btmon.sh")
    env["OPENFRAMETAP_BTMON_USE_SUDO"] = "0"
    env["BTMON_START_FILE"] = shell_path(start_file)
    env["BTMON_STOP_FILE"] = shell_path(stop_file)
    result = subprocess.run(
        [
            bash_path(),
            "scripts/capture-pocket3.sh",
            "experiment",
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
    assert result.returncode == 4
    assert start_file.exists()
    assert stop_file.read_text(encoding="utf-8") == "stopped"
    assert "ARTIFACT_DIR=pocket3-experiment-" in result.stdout


def test_experiment_ctrl_c_waits_for_session_save_and_cleans_btmon(tmp_path: Path) -> None:
    start_file = tmp_path / "started"
    stop_file = tmp_path / "stopped"
    env = os.environ.copy()
    env["OPENFRAMETAP_BTMON_BIN"] = shell_path(ROOT / "tests/fixtures/fake-btmon.sh")
    env["OPENFRAMETAP_PYTHON_BIN"] = shell_path(
        ROOT / "tests/fixtures/interrupt-save-python.sh"
    )
    env["OPENFRAMETAP_BTMON_USE_SUDO"] = "0"
    env["OPENFRAMETAP_TEST_MODE"] = "1"
    env["BTMON_START_FILE"] = shell_path(start_file)
    env["BTMON_STOP_FILE"] = shell_path(stop_file)
    before = set((ROOT / "artifacts").glob("pocket3-experiment-*"))
    result = subprocess.run(
        [
            bash_path(),
            "scripts/capture-pocket3.sh",
            "experiment",
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
    after = set((ROOT / "artifacts").glob("pocket3-experiment-*"))
    created = after - before
    assert result.returncode == 130
    assert len(created) == 1
    output_dir = created.pop()
    session = json.loads((output_dir / "session.json").read_text(encoding="utf-8"))
    assert session["saved_after_interrupt"] is True
    assert session["fff5_write_count"] == 0
    assert (output_dir / "message-counts.json").exists()
    assert (output_dir / "checksums.sha256").exists()
    assert start_file.exists()
    assert stop_file.read_text(encoding="utf-8") == "stopped"


def test_analyze_wrapper_rejects_unsafe_directory_before_ssh() -> None:
    env = os.environ.copy()
    env["OPENFRAMETAP_SSH_BIN"] = shell_path(ROOT / "tests/fixtures/fail-ssh.sh")
    env["ROCK4D_SSH_HOST"] = "fixture@example.invalid"
    result = subprocess.run(
        [bash_path(), "scripts/remote.sh", "analyze-telemetry", "../not-an-experiment"],
        cwd=ROOT,
        env=env,
        text=True,
        capture_output=True,
        check=False,
        timeout=15,
    )
    assert result.returncode == 2
    assert "must be pocket3-experiment" in result.stderr
    assert "Exit status: 37" not in result.stdout
