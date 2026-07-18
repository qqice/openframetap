"""Persistent Pocket 3 RTMP workflow state with explicit legal transitions."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
import json
import os
from pathlib import Path
from typing import Any

from openframetap.network.secrets import require_private_directory


PHASES = (
    "preflight",
    "server_ready",
    "pairing_confirmed",
    "prepare_proposed",
    "prepare_sent",
    "prepare_acknowledged",
    "wifi_proposed",
    "wifi_sent",
    "wifi_connecting",
    "wifi_connected_or_unknown",
    "stream_proposed",
    "stream_sent",
    "waiting_for_rtmp",
    "rtmp_connected",
    "media_detected",
    "sample_saved",
    "stability_tested",
    "completed",
)
ALLOWED_TRANSITIONS = {
    phase: {PHASES[index + 1], "failed"}
    for index, phase in enumerate(PHASES[:-1])
}
ALLOWED_TRANSITIONS[PHASES[-1]] = set()
ALLOWED_TRANSITIONS["failed"] = set()


@dataclass(slots=True)
class Pocket3RtmpWorkflow:
    phase: str = "preflight"
    updated_at_utc: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    history: list[dict[str, Any]] = field(default_factory=list)

    def transition(self, target: str, *, evidence: dict[str, Any] | None = None) -> None:
        if target not in ALLOWED_TRANSITIONS.get(self.phase, set()):
            raise ValueError(f"illegal RTMP workflow transition: {self.phase} -> {target}")
        previous = self.phase
        self.phase = target
        self.updated_at_utc = datetime.now(timezone.utc).isoformat()
        self.history.append(
            {
                "from": previous,
                "to": target,
                "at_utc": self.updated_at_utc,
                "evidence": evidence or {},
            }
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "phase": self.phase,
            "updated_at_utc": self.updated_at_utc,
            "history": self.history,
        }

    def save(self, path: Path) -> None:
        require_private_directory(path.parent)
        path.write_text(json.dumps(self.to_dict(), indent=2) + "\n", encoding="utf-8")
        if os.name != "nt":
            path.chmod(0o600)

    @classmethod
    def load(cls, path: Path) -> "Pocket3RtmpWorkflow":
        if not path.exists():
            return cls()
        payload = json.loads(path.read_text(encoding="utf-8"))
        if payload.get("phase") not in ALLOWED_TRANSITIONS:
            raise ValueError("unknown persisted RTMP workflow phase")
        return cls(
            phase=payload["phase"],
            updated_at_utc=payload.get("updated_at_utc", "unknown"),
            history=list(payload.get("history") or []),
        )


def workflow_plan() -> dict[str, Any]:
    return {
        "phases": list(PHASES) + ["failed"],
        "automatic_retries": 0,
        "wifi_provision_max_send_count": 1,
        "new_command_types_require_independent_authorization": True,
        "stop_on_unexpected_response": True,
    }

