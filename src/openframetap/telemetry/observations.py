"""Lossless field-level numeric observations."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any


@dataclass(frozen=True, slots=True)
class NumericObservation:
    monotonic_ns: int
    cmd_set: int
    cmd_id: int
    payload_length: int
    offset: int
    width: int
    encoding: str
    raw_hex: str
    value: int | float

    @property
    def command(self) -> str:
        return f"{self.cmd_set:02X}/{self.cmd_id:02X}"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self) | {"command": self.command}
