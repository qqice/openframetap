from __future__ import annotations

from dataclasses import asdict, dataclass


@dataclass(frozen=True, slots=True)
class ROI:
    x: int
    y: int
    width: int
    height: int

    def __post_init__(self) -> None:
        if min(self.x, self.y) < 0 or self.width <= 0 or self.height <= 0:
            raise ValueError("ROI must have non-negative origin and positive size")

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def parse(cls, text: str) -> "ROI":
        try:
            values = [int(item.strip()) for item in text.split(",")]
        except ValueError as exc:
            raise ValueError("ROI must be x,y,w,h integers") from exc
        if len(values) != 4:
            raise ValueError("ROI must contain exactly x,y,w,h")
        return cls(*values)


def parse_transform(text: str | None) -> tuple[tuple[float, float], ...] | None:
    if text is None:
        return None
    try:
        values = [float(item.strip()) for item in text.split(",")]
    except ValueError as exc:
        raise ValueError("transform must contain eight numeric coordinates") from exc
    if len(values) != 8:
        raise ValueError("transform must be x1,y1,x2,y2,x3,y3,x4,y4")
    return tuple((values[index], values[index + 1]) for index in range(0, 8, 2))


@dataclass(frozen=True, slots=True)
class ROIConfig:
    source: ROI
    dsi: ROI
    source_transform: tuple[tuple[float, float], ...] | None = None
    dsi_transform: tuple[tuple[float, float], ...] | None = None
    rectified_width: int = 960
    rectified_height: int = 540

    def to_dict(self) -> dict:
        payload = asdict(self)
        payload["source_transform"] = self.source_transform
        payload["dsi_transform"] = self.dsi_transform
        return payload
