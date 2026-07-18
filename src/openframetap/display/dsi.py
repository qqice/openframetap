"""Read-only DSI connector and Mutter logical-layout discovery."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from pathlib import Path
import re
import subprocess

from openframetap.display.session import DisplaySession


@dataclass(frozen=True, slots=True)
class DsiState:
    connector: str
    connected: bool
    enabled: bool
    physical_width: int | None
    physical_height: int | None
    logical_width: int | None
    logical_height: int | None
    transform: int | None
    rotation: str

    def to_dict(self) -> dict:
        return asdict(self)


def parse_mutter_state(text: str, *, physical_width: int, physical_height: int) -> tuple[int | None, int | None, int | None]:
    match = re.search(r"\[\(([-\d]+), ([-\d]+), [\d.]+, uint32 (\d+), true, \[\('DSI-1'", text)
    if not match:
        return None, None, None
    transform = int(match.group(3))
    if transform in {1, 3, 5, 7}:
        return physical_height, physical_width, transform
    return physical_width, physical_height, transform


def discover_dsi_state(session: DisplaySession, drm_root: Path = Path("/sys/class/drm")) -> DsiState:
    candidates = sorted(drm_root.glob("card*-DSI-*"))
    if not candidates:
        raise RuntimeError("no DSI connector exists")
    connector = candidates[0]
    status = (connector / "status").read_text().strip()
    enabled = (connector / "enabled").read_text().strip() == "enabled"
    modes = (connector / "modes").read_text().splitlines()
    width = height = None
    if modes and "x" in modes[0]:
        width, height = (int(value) for value in modes[0].split("x", 1))
    call = subprocess.run(
        [
            "gdbus",
            "call",
            "--session",
            "--dest",
            "org.gnome.Mutter.DisplayConfig",
            "--object-path",
            "/org/gnome/Mutter/DisplayConfig",
            "--method",
            "org.gnome.Mutter.DisplayConfig.GetCurrentState",
        ],
        env=session.environment(),
        text=True,
        capture_output=True,
        check=False,
        timeout=10,
    )
    logical_width = logical_height = transform = None
    if call.returncode == 0 and width and height:
        logical_width, logical_height, transform = parse_mutter_state(
            call.stdout, physical_width=width, physical_height=height
        )
    rotation = {0: "normal", 1: "90-clockwise", 2: "180", 3: "90-counter-clockwise"}.get(
        transform, "unknown"
    )
    return DsiState(
        connector=connector.name,
        connected=status == "connected",
        enabled=enabled,
        physical_width=width,
        physical_height=height,
        logical_width=logical_width,
        logical_height=logical_height,
        transform=transform,
        rotation=rotation,
    )
