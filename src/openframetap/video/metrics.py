"""Process, CPU, RSS, and thermal metrics without external Python packages."""

from __future__ import annotations

from dataclasses import asdict, dataclass
import os
from pathlib import Path
import time


@dataclass(slots=True)
class MetricSample:
    monotonic_ns: int
    cpu_percent: float | None
    rss_bytes: int | None
    temperature_c: float | None
    load_1m: float

    def to_dict(self) -> dict:
        return asdict(self)


def read_temperature_c(root: Path = Path("/sys/class/thermal")) -> float | None:
    values = []
    for path in root.glob("thermal_zone*/temp"):
        try:
            raw = float(path.read_text().strip())
        except (OSError, ValueError):
            continue
        value = raw / 1000.0 if raw > 1000 else raw
        if -20 <= value <= 150:
            values.append(value)
    return max(values) if values else None


class ProcessMetrics:
    def __init__(self, pid: int) -> None:
        self.pid = pid
        self._previous: tuple[float, int] | None = None
        self.clock_ticks = os.sysconf("SC_CLK_TCK")

    def sample(self) -> MetricSample:
        now = time.monotonic()
        cpu_percent = None
        rss = None
        try:
            stat = Path(f"/proc/{self.pid}/stat").read_text().split()
            ticks = int(stat[13]) + int(stat[14])
            if self._previous is not None:
                elapsed = now - self._previous[0]
                if elapsed > 0:
                    cpu_percent = (ticks - self._previous[1]) / self.clock_ticks / elapsed * 100
            self._previous = (now, ticks)
            for line in Path(f"/proc/{self.pid}/status").read_text().splitlines():
                if line.startswith("VmRSS:"):
                    rss = int(line.split()[1]) * 1024
                    break
        except (FileNotFoundError, ProcessLookupError, ValueError, IndexError):
            pass
        return MetricSample(
            monotonic_ns=time.monotonic_ns(),
            cpu_percent=cpu_percent,
            rss_bytes=rss,
            temperature_c=read_temperature_c(),
            load_1m=os.getloadavg()[0],
        )


def summarize_metrics(samples: list[MetricSample]) -> dict:
    cpu = [item.cpu_percent for item in samples if item.cpu_percent is not None]
    rss = [item.rss_bytes for item in samples if item.rss_bytes is not None]
    temp = [item.temperature_c for item in samples if item.temperature_c is not None]
    return {
        "sample_count": len(samples),
        "average_cpu_percent": sum(cpu) / len(cpu) if cpu else None,
        "peak_cpu_percent": max(cpu) if cpu else None,
        "average_rss_bytes": sum(rss) / len(rss) if rss else None,
        "peak_rss_bytes": max(rss) if rss else None,
        "temperature_start_c": temp[0] if temp else None,
        "temperature_end_c": temp[-1] if temp else None,
        "peak_temperature_c": max(temp) if temp else None,
    }
