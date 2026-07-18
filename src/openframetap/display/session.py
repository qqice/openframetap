"""Discover the active local Wayland session without trusting the SSH shell."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import subprocess
import time


@dataclass(frozen=True, slots=True)
class DisplaySession:
    session_id: str
    active_user: str
    uid: int
    seat: str
    session_type: str
    active: bool
    remote: bool
    xdg_runtime_dir: str
    wayland_display: str
    display: str | None
    desktop: str | None

    def environment(self) -> dict[str, str]:
        env = dict(os.environ)
        env.update(
            {
                "XDG_RUNTIME_DIR": self.xdg_runtime_dir,
                "WAYLAND_DISPLAY": self.wayland_display,
                "DBUS_SESSION_BUS_ADDRESS": f"unix:path={self.xdg_runtime_dir}/bus",
                "XDG_SESSION_TYPE": "wayland",
            }
        )
        if self.display:
            env["DISPLAY"] = self.display
        return env

    def to_dict(self) -> dict:
        return asdict(self)


def parse_loginctl_session(session_id: str, text: str, runtime_root: Path = Path("/run/user")) -> DisplaySession:
    values = {}
    for line in text.splitlines():
        if "=" in line:
            key, value = line.split("=", 1)
            values[key] = value
    required = {"User", "Name", "Seat", "Type", "Active", "Remote"}
    if not required.issubset(values):
        raise RuntimeError("loginctl session output is incomplete")
    uid = int(values["User"])
    runtime = runtime_root / str(uid)
    sockets = sorted(path.name for path in runtime.glob("wayland-*") if not path.name.endswith(".lock"))
    if values["Type"] != "wayland" or values["Active"] != "yes" or values["Remote"] != "no":
        raise RuntimeError("session is not the active local Wayland session")
    if not sockets:
        raise RuntimeError("active Wayland session has no compositor socket")
    return DisplaySession(
        session_id=session_id,
        active_user=values["Name"],
        uid=uid,
        seat=values["Seat"],
        session_type=values["Type"],
        active=True,
        remote=False,
        xdg_runtime_dir=str(runtime),
        wayland_display=sockets[0],
        display=values.get("Display") or None,
        desktop=values.get("Desktop") or "GNOME",
    )


def discover_active_wayland_session() -> DisplaySession:
    listed = subprocess.run(
        ["loginctl", "list-sessions", "--no-legend"],
        text=True,
        capture_output=True,
        check=False,
        timeout=10,
    )
    if listed.returncode != 0:
        raise RuntimeError("loginctl list-sessions failed")
    for line in listed.stdout.splitlines():
        fields = line.split()
        if not fields:
            continue
        session_id = fields[0]
        shown = subprocess.run(
            [
                "loginctl",
                "show-session",
                session_id,
                "-p",
                "User",
                "-p",
                "Name",
                "-p",
                "Seat",
                "-p",
                "Type",
                "-p",
                "Active",
                "-p",
                "Remote",
                "-p",
                "Display",
                "-p",
                "Desktop",
            ],
            text=True,
            capture_output=True,
            check=False,
            timeout=10,
        )
        if shown.returncode != 0:
            continue
        try:
            return parse_loginctl_session(session_id, shown.stdout)
        except (RuntimeError, ValueError):
            continue
    raise RuntimeError("no active local Wayland session found")


def parse_overview_active(text: str) -> bool:
    normalized = text.strip().lower()
    if "<true>" in normalized:
        return True
    if "<false>" in normalized:
        return False
    raise ValueError("GNOME OverviewActive response is not boolean")


def gnome_overview_active(env: dict[str, str]) -> bool:
    result = subprocess.run(
        [
            "gdbus",
            "call",
            "--session",
            "--dest",
            "org.gnome.Shell",
            "--object-path",
            "/org/gnome/Shell",
            "--method",
            "org.freedesktop.DBus.Properties.Get",
            "org.gnome.Shell",
            "OverviewActive",
        ],
        env=env,
        text=True,
        capture_output=True,
        check=False,
        timeout=5,
    )
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or "failed to query GNOME overview")
    return parse_overview_active(result.stdout)


def set_gnome_overview_active(
    active: bool, env: dict[str, str], *, timeout: float = 2.0
) -> None:
    """Set transient shell state and wait for the asynchronous transition."""

    result = subprocess.run(
        [
            "gdbus",
            "call",
            "--session",
            "--dest",
            "org.gnome.Shell",
            "--object-path",
            "/org/gnome/Shell",
            "--method",
            "org.freedesktop.DBus.Properties.Set",
            "org.gnome.Shell",
            "OverviewActive",
            f"<{str(active).lower()}>",
        ],
        env=env,
        text=True,
        capture_output=True,
        check=False,
        timeout=5,
    )
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or "failed to set GNOME overview")
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if gnome_overview_active(env) is active:
            return
        time.sleep(0.05)
    raise RuntimeError("GNOME overview transition did not complete")


def run_display_doctor(output_dir: Path) -> dict:
    from openframetap.display.dsi import discover_dsi_state

    output_dir.mkdir(parents=True, exist_ok=True)
    session = discover_active_wayland_session()
    dsi = discover_dsi_state(session)
    payload = {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "active_session": session.to_dict(),
        "dsi": dsi.to_dict(),
        "persistent_configuration_changed": False,
    }
    path = output_dir / "display-doctor.json"
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    (output_dir / "checksums.sha256").write_text(
        f"{digest}  {path.name}\n", encoding="ascii"
    )
    return payload
