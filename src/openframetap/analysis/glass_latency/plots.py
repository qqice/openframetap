from __future__ import annotations

from pathlib import Path


WIDTH = 1200
HEIGHT = 700
MARGIN = 80


def _canvas(title: str):
    from PIL import Image, ImageDraw

    image = Image.new("RGB", (WIDTH, HEIGHT), "white")
    draw = ImageDraw.Draw(image)
    draw.text((MARGIN, 25), title, fill="black")
    draw.line((MARGIN, HEIGHT - MARGIN, WIDTH - 30, HEIGHT - MARGIN), fill="black", width=2)
    draw.line((MARGIN, 55, MARGIN, HEIGHT - MARGIN), fill="black", width=2)
    return image, draw


def _line(path: Path, values: list[float], title: str, *, color: str = "#0066cc") -> None:
    image, draw = _canvas(title)
    if values:
        minimum, maximum = min(values), max(values)
        span = max(maximum - minimum, 1e-9)
        points = []
        for index, value in enumerate(values):
            x = MARGIN + index * (WIDTH - MARGIN - 40) / max(len(values) - 1, 1)
            y = HEIGHT - MARGIN - (value - minimum) * (HEIGHT - 2 * MARGIN) / span
            points.append((x, y))
        if len(points) > 1:
            draw.line(points, fill=color, width=2)
        else:
            x, y = points[0]
            draw.ellipse((x - 3, y - 3, x + 3, y + 3), fill=color)
        draw.text((5, 55), f"max {maximum:.3f}", fill="black")
        draw.text((5, HEIGHT - MARGIN - 15), f"min {minimum:.3f}", fill="black")
    else:
        draw.text((WIDTH // 2 - 60, HEIGHT // 2), "no valid data", fill="red")
    image.save(path)


def write_latency_plots(
    directory: Path,
    *,
    latencies: list[float],
    source_ids: list[int],
    dsi_ids: list[int],
    run_lengths: list[int],
    confidences: list[float],
) -> list[Path]:
    from PIL import ImageDraw

    directory.mkdir(parents=True, exist_ok=True)
    paths = []
    over_time = directory / "latency-over-time.png"
    _line(over_time, latencies, "Glass-to-glass latency over phone-video time (ms)")
    paths.append(over_time)

    histogram = directory / "latency-histogram.png"
    image, draw = _canvas("Glass-to-glass latency histogram")
    if latencies:
        bins = min(50, max(10, round(len(latencies) ** 0.5)))
        low, high = min(latencies), max(latencies)
        width = max(high - low, 1e-9)
        counts = [0] * bins
        for value in latencies:
            counts[min(bins - 1, int((value - low) / width * bins))] += 1
        maximum = max(counts)
        for index, count in enumerate(counts):
            x0 = MARGIN + index * (WIDTH - MARGIN - 40) / bins
            x1 = MARGIN + (index + 1) * (WIDTH - MARGIN - 40) / bins
            y = HEIGHT - MARGIN - count * (HEIGHT - 2 * MARGIN) / max(maximum, 1)
            draw.rectangle((x0, y, x1 - 1, HEIGHT - MARGIN), fill="#4477aa")
    image.save(histogram)
    paths.append(histogram)

    cdf = directory / "latency-cdf.png"
    _line(cdf, sorted(latencies), "Glass-to-glass latency empirical CDF (ordered ms)")
    paths.append(cdf)

    pair = directory / "source-vs-dsi-frame-id.png"
    image, draw = _canvas("Decoded SOURCE (blue) and DSI (orange) frame IDs")
    combined = source_ids + dsi_ids
    if combined:
        low, high = min(combined), max(combined)
        span = max(high - low, 1)
        for values, color in ((source_ids, "#0066cc"), (dsi_ids, "#ee7700")):
            points = []
            for index, value in enumerate(values):
                x = MARGIN + index * (WIDTH - MARGIN - 40) / max(len(values) - 1, 1)
                y = HEIGHT - MARGIN - (value - low) * (HEIGHT - 2 * MARGIN) / span
                points.append((x, y))
            if len(points) > 1:
                draw.line(points, fill=color, width=2)
    image.save(pair)
    paths.append(pair)

    runs = directory / "dsi-frame-run-lengths.png"
    _line(runs, [float(value) for value in run_lengths], "DSI frame run lengths in phone samples")
    paths.append(runs)

    confidence = directory / "decode-confidence.png"
    _line(confidence, confidences, "Gray Code decode confidence", color="#228833")
    paths.append(confidence)
    return paths
