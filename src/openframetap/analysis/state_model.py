"""Read-only Pocket 3 candidate state with lossless provenance."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any

from openframetap.telemetry.schemas import ObservationProvenance, validate_confidence


@dataclass(slots=True)
class Pocket3State:
    battery_percent: int | None = None
    product_identifier: str | None = None
    gimbal_pitch_candidate: float | None = None
    gimbal_roll_candidate: float | None = None
    gimbal_yaw_candidate: float | None = None
    gimbal_mode_candidate: int | None = None
    camera_status_candidate: int | None = None
    last_update_monotonic_ns: int | None = None
    confidence: dict[str, str] = field(default_factory=dict)
    provenance: dict[str, ObservationProvenance] = field(default_factory=dict)

    def apply(
        self,
        field_name: str,
        value: Any,
        provenance: ObservationProvenance,
    ) -> None:
        if not hasattr(self, field_name) or field_name in {
            "confidence",
            "provenance",
            "last_update_monotonic_ns",
        }:
            raise ValueError(f"unsupported state field {field_name!r}")
        validate_confidence(provenance.confidence)
        setattr(self, field_name, value)
        self.confidence[field_name] = provenance.confidence
        self.provenance[field_name] = provenance
        if (
            self.last_update_monotonic_ns is None
            or provenance.last_updated >= self.last_update_monotonic_ns
        ):
            self.last_update_monotonic_ns = provenance.last_updated

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["provenance"] = {
            name: provenance.to_dict() for name, provenance in self.provenance.items()
        }
        return payload


def provenance_from_candidate(
    candidate: dict[str, Any],
    *,
    confidence: str,
    last_updated: int,
    evidence_session: str,
    scale: float | None = None,
) -> ObservationProvenance:
    cmd_set, cmd_id = (int(part, 16) for part in candidate["command"].split("/"))
    decoded = candidate.get("last_value")
    if scale is not None and isinstance(decoded, (int, float)):
        decoded = decoded * scale
    return ObservationProvenance(
        source_cmd_set=cmd_set,
        source_cmd_id=cmd_id,
        source_offset=int(candidate["offset"]),
        raw_value=str(candidate.get("last_raw_hex", candidate.get("last_value"))),
        decoded_value=decoded,
        confidence=confidence,
        last_updated=last_updated,
        evidence_session=evidence_session,
        encoding=candidate["encoding"],
        scale=scale,
    )
