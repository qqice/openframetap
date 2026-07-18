"""Shared confidence and provenance schemas for read-only state candidates."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any


CONFIDENCE_LEVELS = ("confirmed", "high", "medium", "low", "unknown", "rejected")


def validate_confidence(value: str) -> str:
    if value not in CONFIDENCE_LEVELS:
        raise ValueError(f"unsupported confidence {value!r}")
    return value


@dataclass(frozen=True, slots=True)
class ObservationProvenance:
    source_cmd_set: int
    source_cmd_id: int
    source_offset: int
    raw_value: str
    decoded_value: Any
    confidence: str
    last_updated: int
    evidence_session: str
    encoding: str
    scale: float | None = None

    def __post_init__(self) -> None:
        validate_confidence(self.confidence)
        if self.source_offset < 0:
            raise ValueError("source_offset must be non-negative")

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
