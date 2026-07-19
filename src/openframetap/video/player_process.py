"""PID-owned media process lifecycle; never broad pkill/killall."""

from __future__ import annotations

from dataclasses import asdict, dataclass
import json
import os
from pathlib import Path
import signal
import subprocess
import time
import re
from urllib.parse import urlsplit, urlunsplit


def _current_uid() -> int:
    return os.getuid() if hasattr(os, "getuid") else 0


def redact_argv(argv: list[str]) -> list[str]:
    rendered = []
    for item in argv:
        prefix = "location=" if item.startswith("location=") else ""
        value = item[len(prefix) :]
        if value.startswith(("rtmp://", "rtsp://")):
            parsed = urlsplit(value)
            parts = [part for part in parsed.path.split("/") if part]
            path = f"/{parts[0]}/<redacted>" if parts else "/<redacted>"
            value = urlunsplit((parsed.scheme, parsed.netloc, path, "", ""))
        value = re.sub(
            r"(?i)(?<![0-9a-f])(?:[0-9a-f]{2}:){5}[0-9a-f]{2}(?![0-9a-f])",
            "<device-address-redacted>",
            value,
        )
        rendered.append(prefix + value)
    return rendered


@dataclass(slots=True)
class OwnedProcess:
    name: str
    pid: int
    argv: list[str]
    started_at_monotonic_ns: int
    owner_uid: int

    def to_dict(self) -> dict:
        return asdict(self)

    def to_public_dict(self) -> dict:
        payload = self.to_dict()
        payload["argv"] = redact_argv(self.argv)
        return payload


def _pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except (ProcessLookupError, OSError):
        return False
    return True


class ProcessRegistry:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def load(self) -> dict[str, OwnedProcess]:
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
        except (FileNotFoundError, json.JSONDecodeError):
            return {}
        result = {}
        for name, item in payload.items():
            process = OwnedProcess(**item)
            if _pid_alive(process.pid):
                result[name] = process
        if not result:
            self.path.unlink(missing_ok=True)
        return result

    def save(self, processes: dict[str, OwnedProcess]) -> None:
        self.path.write_text(
            json.dumps({name: item.to_dict() for name, item in processes.items()}, indent=2)
            + "\n",
            encoding="utf-8",
        )
        if os.name != "nt":
            self.path.chmod(0o600)

    def register(self, name: str, process: subprocess.Popen, argv: list[str]) -> OwnedProcess:
        return self.register_pid(name, process.pid, argv)

    def register_pid(self, name: str, pid: int, argv: list[str]) -> OwnedProcess:
        processes = self.load()
        if name in processes:
            raise RuntimeError(f"owned process already running: {name}")
        owned = OwnedProcess(
            name, pid, redact_argv(argv), time.monotonic_ns(), _current_uid()
        )
        processes[name] = owned
        self.save(processes)
        return owned

    def stop(self, name: str, *, timeout: float = 5.0) -> bool:
        processes = self.load()
        owned = processes.get(name)
        if not owned:
            return False
        proc_status = Path(f"/proc/{owned.pid}/status")
        try:
            uid_line = next(line for line in proc_status.read_text().splitlines() if line.startswith("Uid:"))
            real_uid = int(uid_line.split()[1])
        except (FileNotFoundError, StopIteration, ValueError):
            real_uid = -1
        if os.name != "nt" and (real_uid != owned.owner_uid or real_uid != _current_uid()):
            raise PermissionError("refusing to stop a PID not owned by the current user")
        os.kill(owned.pid, signal.SIGTERM)
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline and _pid_alive(owned.pid):
            time.sleep(0.05)
        if _pid_alive(owned.pid):
            try:
                os.kill(owned.pid, getattr(signal, "SIGKILL", signal.SIGTERM))
            except ProcessLookupError:
                pass
            except PermissionError:
                if os.name != "nt":
                    raise
        processes.pop(name, None)
        if processes:
            self.save(processes)
        else:
            self.path.unlink(missing_ok=True)
        return True

    def stop_all(self) -> list[str]:
        stopped = []
        for name in list(self.load()):
            if self.stop(name):
                stopped.append(name)
        return stopped

    def unregister(self, name: str) -> None:
        processes = self.load()
        processes.pop(name, None)
        if processes:
            self.save(processes)
        else:
            self.path.unlink(missing_ok=True)
