"""Refresh-aware Gray Code source for external glass-to-glass recordings."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import ctypes
from ctypes import wintypes
import hashlib
import json
from pathlib import Path
import signal
import sys
import time
from typing import TextIO


def binary_to_gray(value: int) -> int:
    if value < 0:
        raise ValueError("Gray Code input must be non-negative")
    return value ^ (value >> 1)


def gray_to_binary(value: int) -> int:
    if value < 0:
        raise ValueError("Gray Code input must be non-negative")
    result = value
    shift = value >> 1
    while shift:
        result ^= shift
        shift >>= 1
    return result


def gray_bit_values(frame_id: int, bits: int) -> tuple[int, ...]:
    if not 1 <= bits <= 24:
        raise ValueError("bits must be 1..24")
    gray = binary_to_gray(frame_id % (1 << bits))
    return tuple((gray >> bit) & 1 for bit in range(bits - 1, -1, -1))


@dataclass(frozen=True, slots=True)
class NormalizedRect:
    x0: float
    y0: float
    x1: float
    y1: float

    def pixels(self, width: int, height: int) -> tuple[int, int, int, int]:
        return (
            round(self.x0 * width),
            round(self.y0 * height),
            round(self.x1 * width),
            round(self.y1 * height),
        )


@dataclass(frozen=True, slots=True)
class PatternLayout:
    bits: int
    top_bits: tuple[NormalizedRect, ...]
    bottom_bits: tuple[NormalizedRect, ...]
    top_black_reference: NormalizedRect
    top_white_reference: NormalizedRect
    bottom_black_reference: NormalizedRect
    bottom_white_reference: NormalizedRect
    flash: NormalizedRect
    direction: NormalizedRect


def pattern_layout(bits: int) -> PatternLayout:
    if not 12 <= bits <= 16:
        raise ValueError("latency pattern supports 12..16 bits")
    left, right = 0.045, 0.815
    gap = 0.006
    cell = (right - left - gap * (bits - 1)) / bits

    def bank(y0: float, y1: float) -> tuple[NormalizedRect, ...]:
        return tuple(
            NormalizedRect(
                left + index * (cell + gap),
                y0,
                left + index * (cell + gap) + cell,
                y1,
            )
            for index in range(bits)
        )

    return PatternLayout(
        bits=bits,
        top_bits=bank(0.18, 0.445),
        bottom_bits=bank(0.505, 0.77),
        top_black_reference=NormalizedRect(0.845, 0.18, 0.895, 0.445),
        top_white_reference=NormalizedRect(0.915, 0.18, 0.965, 0.445),
        bottom_black_reference=NormalizedRect(0.845, 0.505, 0.895, 0.77),
        bottom_white_reference=NormalizedRect(0.915, 0.505, 0.965, 0.77),
        flash=NormalizedRect(0.045, 0.825, 0.51, 0.965),
        direction=NormalizedRect(0.55, 0.825, 0.965, 0.965),
    )


@dataclass(frozen=True, slots=True)
class DisplayInfo:
    index: int
    device_name: str
    x: int
    y: int
    width: int
    height: int
    refresh_hz: float
    primary: bool

    def to_dict(self) -> dict:
        return asdict(self)


class _MonitorInfo(ctypes.Structure):
    _fields_ = [
        ("cbSize", wintypes.DWORD),
        ("rcMonitor", wintypes.RECT),
        ("rcWork", wintypes.RECT),
        ("dwFlags", wintypes.DWORD),
        ("szDevice", wintypes.WCHAR * 32),
    ]


class _PointL(ctypes.Structure):
    _fields_ = [("x", wintypes.LONG), ("y", wintypes.LONG)]


class _PrinterFields(ctypes.Structure):
    _fields_ = [
        ("dmOrientation", wintypes.SHORT),
        ("dmPaperSize", wintypes.SHORT),
        ("dmPaperLength", wintypes.SHORT),
        ("dmPaperWidth", wintypes.SHORT),
        ("dmScale", wintypes.SHORT),
        ("dmCopies", wintypes.SHORT),
        ("dmDefaultSource", wintypes.SHORT),
        ("dmPrintQuality", wintypes.SHORT),
    ]


class _DisplayFields(ctypes.Structure):
    _fields_ = [
        ("dmPosition", _PointL),
        ("dmDisplayOrientation", wintypes.DWORD),
        ("dmDisplayFixedOutput", wintypes.DWORD),
    ]


class _ModeUnion(ctypes.Union):
    _fields_ = [("printer", _PrinterFields), ("display", _DisplayFields)]


class _DevMode(ctypes.Structure):
    _anonymous_ = ("mode",)
    _fields_ = [
        ("dmDeviceName", wintypes.WCHAR * 32),
        ("dmSpecVersion", wintypes.WORD),
        ("dmDriverVersion", wintypes.WORD),
        ("dmSize", wintypes.WORD),
        ("dmDriverExtra", wintypes.WORD),
        ("dmFields", wintypes.DWORD),
        ("mode", _ModeUnion),
        ("dmColor", wintypes.SHORT),
        ("dmDuplex", wintypes.SHORT),
        ("dmYResolution", wintypes.SHORT),
        ("dmTTOption", wintypes.SHORT),
        ("dmCollate", wintypes.SHORT),
        ("dmFormName", wintypes.WCHAR * 32),
        ("dmLogPixels", wintypes.WORD),
        ("dmBitsPerPel", wintypes.DWORD),
        ("dmPelsWidth", wintypes.DWORD),
        ("dmPelsHeight", wintypes.DWORD),
        ("dmDisplayFlags", wintypes.DWORD),
        ("dmDisplayFrequency", wintypes.DWORD),
        ("dmICMMethod", wintypes.DWORD),
        ("dmICMIntent", wintypes.DWORD),
        ("dmMediaType", wintypes.DWORD),
        ("dmDitherType", wintypes.DWORD),
        ("dmReserved1", wintypes.DWORD),
        ("dmReserved2", wintypes.DWORD),
        ("dmPanningWidth", wintypes.DWORD),
        ("dmPanningHeight", wintypes.DWORD),
    ]


def discover_displays() -> list[DisplayInfo]:
    if sys.platform != "win32":
        return [DisplayInfo(0, "default", 0, 0, 1280, 720, 60.0, True)]
    user32 = ctypes.windll.user32
    monitors: list[DisplayInfo] = []
    callback_type = ctypes.WINFUNCTYPE(
        wintypes.BOOL,
        wintypes.HMONITOR,
        wintypes.HDC,
        ctypes.POINTER(wintypes.RECT),
        wintypes.LPARAM,
    )

    def callback(handle, _dc, _rect, _data):
        info = _MonitorInfo()
        info.cbSize = ctypes.sizeof(info)
        if not user32.GetMonitorInfoW(handle, ctypes.byref(info)):
            return True
        mode = _DevMode()
        mode.dmSize = ctypes.sizeof(mode)
        refresh = 60.0
        if user32.EnumDisplaySettingsW(info.szDevice, -1, ctypes.byref(mode)):
            if 1 <= mode.dmDisplayFrequency <= 1000:
                refresh = float(mode.dmDisplayFrequency)
        rect = info.rcMonitor
        monitors.append(
            DisplayInfo(
                index=len(monitors),
                device_name=info.szDevice,
                x=rect.left,
                y=rect.top,
                width=rect.right - rect.left,
                height=rect.bottom - rect.top,
                refresh_hz=refresh,
                primary=bool(info.dwFlags & 1),
            )
        )
        return True

    callback_ref = callback_type(callback)
    if not user32.EnumDisplayMonitors(None, None, callback_ref, 0):
        raise RuntimeError("EnumDisplayMonitors failed")
    if not monitors:
        raise RuntimeError("Windows reported no active displays")
    return monitors


@dataclass(frozen=True, slots=True)
class PatternState:
    frame_id: int
    monotonic_ns: int
    milliseconds: int
    white: bool
    gray_code: int = 0

    def to_dict(self) -> dict:
        return asdict(self)


def pattern_state(frame_id: int, monotonic_ns: int, *, bits: int = 16) -> PatternState:
    bounded = frame_id % (1 << bits)
    return PatternState(
        frame_id=bounded,
        monotonic_ns=monotonic_ns,
        milliseconds=monotonic_ns // 1_000_000,
        white=(frame_id // 15) % 2 == 0,
        gray_code=binary_to_gray(bounded),
    )


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _write_json(path: Path, payload: dict) -> None:
    with path.open("w", encoding="utf-8", newline="\n") as stream:
        json.dump(payload, stream, indent=2, ensure_ascii=False)
        stream.write("\n")


def _write_checksums(directory: Path, files: list[Path]) -> None:
    with (directory / "checksums.sha256").open(
        "w", encoding="ascii", newline="\n"
    ) as stream:
        for path in files:
            stream.write(f"{_sha256(path)}  {path.relative_to(directory).as_posix()}\n")


def finalize_pattern_evidence(
    output: Path,
    *,
    timing_path: Path,
    config_path: Path,
    runtime_path: Path,
    config: dict,
    records: int,
    stopped_by: str,
    screenshot_path: Path,
    screenshot_error: str | None,
) -> dict:
    final = {
        **config,
        "ended_at_utc": datetime.now(timezone.utc).isoformat(),
        "records_written": records,
        "stopped_by": stopped_by,
        "completed": stopped_by == "duration",
        "screenshot_saved": screenshot_path.is_file(),
        "screenshot_error": screenshot_error,
    }
    _write_json(config_path, final)
    files = [timing_path, config_path, runtime_path]
    if screenshot_path.is_file():
        files.append(screenshot_path)
    _write_checksums(output, files)
    return final


def _configure_canvas(canvas, layout: PatternLayout, width: int, height: int) -> dict:
    canvas.delete("all")
    items: dict[str, object] = {"top": [], "bottom": []}
    canvas.create_rectangle(0, 0, width, height, fill="black", outline="black")
    canvas.create_rectangle(2, 2, width - 2, height - 2, outline="#00ff66", width=6)
    items["header"] = canvas.create_text(
        width // 2,
        round(height * 0.075),
        text="SOURCE",
        fill="white",
        font=("Consolas", max(24, height // 24), "bold"),
        justify="center",
    )
    for name, rects in (("top", layout.top_bits), ("bottom", layout.bottom_bits)):
        target = items[name]
        for rect in rects:
            target.append(
                canvas.create_rectangle(
                    *rect.pixels(width, height), fill="black", outline="#404040", width=2
                )
            )
    references = (
        (layout.top_black_reference, "black"),
        (layout.top_white_reference, "white"),
        (layout.bottom_black_reference, "black"),
        (layout.bottom_white_reference, "white"),
    )
    items["references"] = []
    for rect, color in references:
        items["references"].append(
            canvas.create_rectangle(
                *rect.pixels(width, height), fill=color, outline=color
            )
        )
    items["flash"] = canvas.create_rectangle(
        *layout.flash.pixels(width, height), fill="black", outline="white", width=4
    )
    x0, y0, x1, y1 = layout.direction.pixels(width, height)
    canvas.create_polygon(
        x0,
        (y0 + y1) // 2,
        x1,
        y0,
        x1,
        y1,
        fill="#00ff66",
        outline="white",
        width=3,
    )
    canvas.create_text(
        (x0 + x1) // 2,
        (y0 + y1) // 2,
        text="SOURCE  →",
        fill="black",
        font=("Consolas", max(18, height // 32), "bold"),
    )
    return items


def _update_canvas(canvas, items: dict, state: PatternState, bits: int, invert: bool) -> None:
    values = gray_bit_values(state.frame_id, bits)
    previous_values = items.get("_last_values")
    for bank in ("top", "bottom"):
        for index, (item, bit) in enumerate(zip(items[bank], values, strict=True)):
            if previous_values is not None and previous_values[index] == bit:
                continue
            on = bool(bit) ^ invert
            canvas.itemconfigure(item, fill="white" if on else "black")
    items["_last_values"] = values
    flash_on = state.frame_id % 2 == 0
    canvas.itemconfigure(
        items["flash"], fill="white" if flash_on ^ invert else "black"
    )
    reference_colors = ("white", "black", "white", "black") if invert else (
        "black",
        "white",
        "black",
        "white",
    )
    if items.get("_last_invert") != invert:
        for item, color in zip(items["references"], reference_colors, strict=True):
            canvas.itemconfigure(item, fill=color, outline=color)
        items["_last_invert"] = invert
    canvas.itemconfigure(
        items["header"],
        text=(
            f"SOURCE   FRAME {state.frame_id:05d}   "
            f"GRAY 0x{state.gray_code:04X}   {state.milliseconds} ms"
        ),
    )


def render_pattern_image(
    frame_id: int,
    *,
    width: int = 1600,
    height: int = 900,
    bits: int = 16,
    invert: bool = False,
):
    """Render a deterministic fixture/reference image with the live layout."""

    from PIL import Image, ImageDraw

    layout = pattern_layout(bits)
    state = pattern_state(frame_id, time.perf_counter_ns(), bits=bits)
    image = Image.new("RGB", (width, height), "black")
    draw = ImageDraw.Draw(image)
    values = gray_bit_values(state.frame_id, bits)
    for bank in (layout.top_bits, layout.bottom_bits):
        for rect, bit in zip(bank, values, strict=True):
            on = bool(bit) ^ invert
            draw.rectangle(rect.pixels(width, height), fill="white" if on else "black")
    references = (
        layout.top_black_reference,
        layout.top_white_reference,
        layout.bottom_black_reference,
        layout.bottom_white_reference,
    )
    colors = ("white", "black", "white", "black") if invert else (
        "black",
        "white",
        "black",
        "white",
    )
    for rect, color in zip(references, colors, strict=True):
        draw.rectangle(rect.pixels(width, height), fill=color)
    flash_on = state.frame_id % 2 == 0
    draw.rectangle(
        layout.flash.pixels(width, height),
        fill="white" if flash_on ^ invert else "black",
    )
    draw.rectangle((2, 2, width - 3, height - 3), outline="#00ff66", width=6)
    return image


def _capture_screen(path: Path, display: DisplayInfo) -> str | None:
    try:
        from PIL import ImageGrab

        image = ImageGrab.grab(
            bbox=(
                display.x,
                display.y,
                display.x + display.width,
                display.y + display.height,
            ),
            all_screens=True,
        )
        image.save(path)
        return None
    except Exception as exc:  # screenshot is evidence, not pattern correctness
        return str(exc)


def default_pattern_output() -> Path:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return Path("artifacts/private") / f"latency-pattern-{stamp}"


def _begin_high_resolution_timer() -> bool:
    if sys.platform != "win32":
        return False
    return ctypes.windll.winmm.timeBeginPeriod(1) == 0


def _end_high_resolution_timer(enabled: bool) -> None:
    if enabled and sys.platform == "win32":
        ctypes.windll.winmm.timeEndPeriod(1)


def run_latency_pattern(
    *,
    fullscreen: bool = True,
    duration_seconds: int = 60,
    display_index: int = 0,
    refresh_hz: float | None = None,
    bits: int = 16,
    invert: bool = False,
    warmup_seconds: float = 5.0,
    log_path: Path | None = None,
) -> Path:
    import tkinter as tk

    if not 1 <= duration_seconds <= 600:
        raise ValueError("duration must be 1..600 seconds")
    if not 0 <= warmup_seconds <= 30:
        raise ValueError("warmup must be 0..30 seconds")
    layout = pattern_layout(bits)
    displays = discover_displays()
    if not 0 <= display_index < len(displays):
        raise ValueError(f"display index must be 0..{len(displays) - 1}")
    display = displays[display_index]
    selected_hz = refresh_hz or display.refresh_hz
    if not 20 <= selected_hz <= 500:
        raise ValueError("refresh rate must be 20..500 Hz")
    output = log_path.parent if log_path else default_pattern_output()
    output.mkdir(parents=True, exist_ok=False)
    timing_path = log_path or output / "pattern-timing.jsonl"
    if timing_path.parent.resolve() != output.resolve():
        raise ValueError("pattern log must be inside its evidence directory")
    runtime_path = output / "runtime.log"
    config_path = output / "pattern-config.json"
    screenshot_path = output / "screenshot.png"
    config = {
        "schema_version": 1,
        "display": display.to_dict(),
        "display_index": display_index,
        "refresh_hz": selected_hz,
        "refresh_source": "windows_current_mode" if refresh_hz is None else "cli_override",
        "bits": bits,
        "gray_code": True,
        "invert": invert,
        "fullscreen": fullscreen,
        "warmup_seconds": warmup_seconds,
        "duration_seconds": duration_seconds,
        "clock": "time.perf_counter_ns",
        "actual_present_timestamp_available": False,
        "timestamp_limitation": (
            "actual_present_time_ns is unavailable through Tk; monotonic_ns records "
            "application submission after canvas update, not display scanout"
        ),
        "started_at_utc": datetime.now(timezone.utc).isoformat(),
        "completed": False,
    }
    _write_json(config_path, config)
    runtime: TextIO = runtime_path.open("w", encoding="utf-8", newline="\n")
    timing: TextIO = timing_path.open("w", encoding="utf-8", newline="\n", buffering=1)
    root = tk.Tk()
    root.title("OpenFrameTap Gray Code latency source")
    root.configure(background="black")
    if fullscreen:
        root.overrideredirect(True)
        root.geometry(f"{display.width}x{display.height}+{display.x}+{display.y}")
    else:
        width, height = max(960, display.width // 2), max(540, display.height // 2)
        root.geometry(f"{width}x{height}+{display.x + 40}+{display.y + 40}")
    root.update_idletasks()
    root_width = max(root.winfo_width(), 960)
    root_height = max(root.winfo_height(), 540)
    width = min(root_width, round(root_height * 16 / 9))
    height = min(root_height, round(root_width * 9 / 16))
    canvas = tk.Canvas(
        root,
        width=width,
        height=height,
        highlightthickness=0,
        background="black",
    )
    canvas.place(relx=0.5, rely=0.5, anchor="center", width=width, height=height)
    root.update_idletasks()
    items = _configure_canvas(canvas, layout, width, height)
    root.focus_force()
    stopping = False
    stopped_by = "duration"
    records = 0
    screenshot_error = None
    screenshot_attempted = False
    recording_start_ns = time.perf_counter_ns() + round(warmup_seconds * 1e9)
    frame_interval_ns = round(1e9 / selected_hz)
    frame_sequence = 0
    skipped_submission_slots = 0
    last_submission_ns = 0
    first_submission_ns = 0
    timer_period_enabled = _begin_high_resolution_timer()

    def request_stop(reason: str) -> None:
        nonlocal stopping, stopped_by
        if not stopping:
            stopped_by = reason
            stopping = True

    def capture_evidence_screenshot() -> None:
        nonlocal screenshot_attempted, screenshot_error
        if not screenshot_attempted:
            screenshot_attempted = True
            root.update_idletasks()
            screenshot_error = _capture_screen(screenshot_path, display)

    root.bind("<Escape>", lambda _event: request_stop("escape"))
    root.bind("q", lambda _event: request_stop("q"))
    root.protocol("WM_DELETE_WINDOW", lambda: request_stop("window-close"))
    previous_sigint = signal.getsignal(signal.SIGINT)
    signal.signal(signal.SIGINT, lambda _signum, _frame: request_stop("ctrl-c"))

    def update() -> None:
        nonlocal records, screenshot_error
        nonlocal frame_sequence, skipped_submission_slots, last_submission_ns
        nonlocal first_submission_ns
        if stopping:
            capture_evidence_screenshot()
            root.quit()
            return
        now = time.perf_counter_ns()
        if now < recording_start_ns:
            canvas.itemconfigure(
                items["header"],
                text=f"SOURCE   WARMUP {max(0.0, (recording_start_ns - now) / 1e9):.1f}s",
            )
            root.after(max(1, round((recording_start_ns - now) / 1e6)), update)
            return
        target_ns = recording_start_ns + frame_sequence * frame_interval_ns
        not_before_ns = max(
            target_ns,
            last_submission_ns + frame_interval_ns if last_submission_ns else target_ns,
        )
        if now < not_before_ns:
            root.after(max(1, round((not_before_ns - now) / 1e6)), update)
            return
        if now - recording_start_ns >= round(duration_seconds * 1e9):
            request_stop("duration")
            capture_evidence_screenshot()
            root.quit()
            return
        if now - target_ns >= frame_interval_ns:
            missed = (now - target_ns) // frame_interval_ns
            frame_sequence += int(missed)
            skipped_submission_slots += int(missed)
            target_ns = recording_start_ns + frame_sequence * frame_interval_ns
        state = pattern_state(frame_sequence, now, bits=bits)
        _update_canvas(canvas, items, state, bits, invert)
        submitted_ns = time.perf_counter_ns()
        last_submission_ns = submitted_ns
        if not first_submission_ns:
            first_submission_ns = submitted_ns
        payload = {
            "frame_id": state.frame_id,
            "gray_code": state.gray_code,
            "wall_time_utc": datetime.now(timezone.utc).isoformat(),
            "monotonic_ns": submitted_ns,
            "target_present_time_ns": target_ns,
            "actual_present_time_ns": None,
            "display_index": display_index,
            "refresh_hz": selected_hz,
        }
        timing.write(json.dumps(payload, sort_keys=True) + "\n")
        records += 1
        frame_sequence += 1
        root.after(1, update)

    runtime.write(
        f"display={display.device_name} geometry={display.width}x{display.height} "
        f"refresh_hz={selected_hz} bits={bits}\n"
    )
    runtime.flush()
    try:
        root.after(0, update)
        root.mainloop()
    except KeyboardInterrupt:
        request_stop("ctrl-c")
    finally:
        signal.signal(signal.SIGINT, previous_sigint)
        _end_high_resolution_timer(timer_period_enabled)
        try:
            root.destroy()
        except tk.TclError:
            pass
        timing.flush()
        timing.close()
        runtime.write(
            f"records_written={records} stopped_by={stopped_by} "
            f"skipped_submission_slots={skipped_submission_slots} "
            f"screenshot_saved={screenshot_path.is_file()}\n"
        )
        runtime.flush()
        runtime.close()
        finalize_pattern_evidence(
            output,
            timing_path=timing_path,
            config_path=config_path,
            runtime_path=runtime_path,
            config={
                **config,
                "pattern_viewport_width": width,
                "pattern_viewport_height": height,
                "skipped_submission_slots": skipped_submission_slots,
                "windows_timer_period_1ms_enabled": timer_period_enabled,
                "effective_submission_hz": (
                    (records - 1) * 1e9 / (last_submission_ns - first_submission_ns)
                    if records > 1 and last_submission_ns > first_submission_ns
                    else None
                ),
            },
            records=records,
            stopped_by=stopped_by,
            screenshot_path=screenshot_path,
            screenshot_error=screenshot_error,
        )
    return output
