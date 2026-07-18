from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


# Company/manufacturer keys are intentionally empty until a primary source or
# device corpus verifies them. Observed keys remain in raw evidence.
DJI_COMPANY_IDS: set[int] = set()
DJI_NAME_TOKENS = ("dji", "osmo", "pocket")
DJI_DISCOVERY_UUIDS = {"fff0", "0000fff0-0000-1000-8000-00805f9b34fb"}


def normalize_uuid(value: str) -> str:
    return value.strip().lower()


@dataclass(slots=True)
class ProfileMatch:
    suspected: bool
    reasons: list[str] = field(default_factory=list)
    model_raw_hex: str | None = None


def classify_dji_osmo(record: dict[str, Any]) -> ProfileMatch:
    """Classify conservatively without assuming a decoded Pocket model."""
    reasons: list[str] = []
    name = str(record.get("display_name") or "").lower()
    if any(token in name for token in DJI_NAME_TOKENS):
        reasons.append("name-token")

    manufacturer = record.get("manufacturer_data") or {}
    raw_candidates: list[str] = []
    for raw_company_id, raw_value in manufacturer.items():
        try:
            company_id = int(str(raw_company_id), 0)
        except ValueError:
            company_id = -1
        if company_id in DJI_COMPANY_IDS:
            reasons.append(f"company-id-0x{company_id:04x}")
        value = str(raw_value)
        if value:
            raw_candidates.append(value)

    advertised_uuids = {
        normalize_uuid(str(item)) for item in record.get("service_uuids") or []
    }
    if advertised_uuids & DJI_DISCOVERY_UUIDS:
        reasons.append("fff0-service")

    model_raw_hex = raw_candidates[0] if raw_candidates else None
    return ProfileMatch(bool(reasons), reasons, model_raw_hex)
