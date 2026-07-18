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
    per_core_cpu_percent: dict[str, float] | None = None
    network_rx_bytes: int | None = None

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
        self._previous_cores: dict[str, tuple[int, int]] = {}

    def _core_usage(self) -> dict[str, float] | None:
        current = {}
        try:
            for line in Path("/proc/stat").read_text().splitlines():
                fields = line.split()
                if not fields or not fields[0].startswith("cpu") or fields[0] == "cpu":
                    continue
                values = [int(value) for value in fields[1:]]
                total = sum(values)
                idle = values[3] + (values[4] if len(values) > 4 else 0)
                current[fields[0]] = (total, idle)
        except (OSError, ValueError):
            return None
        result = {}
        for name, (total, idle) in current.items():
            previous = self._previous_cores.get(name)
            if previous and total > previous[0]:
                delta_total = total - previous[0]
                result[name] = (delta_total - (idle - previous[1])) / delta_total * 100
        self._previous_cores = current
        return result or None

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
        try:
            network_rx = int(
                Path("/sys/class/net/wlan0/statistics/rx_bytes").read_text().strip()
            )
        except (OSError, ValueError):
            network_rx = None
        return MetricSample(
            monotonic_ns=time.monotonic_ns(),
            cpu_percent=cpu_percent,
            rss_bytes=rss,
            temperature_c=read_temperature_c(),
            load_1m=os.getloadavg()[0],
            per_core_cpu_percent=self._core_usage(),
            network_rx_bytes=network_rx,
        )


def summarize_metrics(samples: list[MetricSample]) -> dict:
    cpu = [item.cpu_percent for item in samples if item.cpu_percent is not None]
    rss = [item.rss_bytes for item in samples if item.rss_bytes is not None]
    temp = [item.temperature_c for item in samples if item.temperature_c is not None]
    network = [
        (item.monotonic_ns, item.network_rx_bytes)
        for item in samples
        if item.network_rx_bytes is not None
    ]
    core_names = sorted(
        {
            name
            for item in samples
            for name in (item.per_core_cpu_percent or {})
        }
    )
    per_core_average = {
        name: sum(values) / len(values)
        for name in core_names
        if (
            values := [
                item.per_core_cpu_percent[name]
                for item in samples
                if item.per_core_cpu_percent and name in item.per_core_cpu_percent
            ]
        )
    }
    network_bps = None
    if len(network) >= 2 and network[-1][0] > network[0][0]:
        network_bps = (network[-1][1] - network[0][1]) * 8e9 / (
            network[-1][0] - network[0][0]
        )
    return {
        "sample_count": len(samples),
        "average_cpu_percent": sum(cpu) / len(cpu) if cpu else None,
        "peak_cpu_percent": max(cpu) if cpu else None,
        "average_rss_bytes": sum(rss) / len(rss) if rss else None,
        "peak_rss_bytes": max(rss) if rss else None,
        "rss_change_bytes": rss[-1] - rss[0] if len(rss) >= 2 else None,
        "temperature_start_c": temp[0] if temp else None,
        "temperature_end_c": temp[-1] if temp else None,
        "peak_temperature_c": max(temp) if temp else None,
        "per_core_average_percent": per_core_average,
        "network_receive_bps": network_bps,
    }
