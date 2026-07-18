"""High-contrast frame counter for an external glass-to-glass recording."""

from __future__ import annotations

from dataclasses import asdict, dataclass
import time


@dataclass(frozen=True, slots=True)
class PatternState:
    frame_id: int
    monotonic_ns: int
    milliseconds: int
    white: bool

    def to_dict(self) -> dict:
        return asdict(self)


def pattern_state(frame_id: int, monotonic_ns: int) -> PatternState:
    return PatternState(
        frame_id=frame_id,
        monotonic_ns=monotonic_ns,
        milliseconds=monotonic_ns // 1_000_000,
        white=(frame_id // 15) % 2 == 0,
    )


def run_latency_pattern(*, fullscreen: bool = True, duration_seconds: int = 60) -> None:
    import tkinter as tk

    root = tk.Tk()
    root.title("OpenFrameTap latency pattern")
    root.configure(background="black")
    if fullscreen:
        root.attributes("-fullscreen", True)
    root.bind("<Escape>", lambda _event: root.destroy())
    root.bind("q", lambda _event: root.destroy())
    canvas = tk.Canvas(root, highlightthickness=0, background="black")
    canvas.pack(fill="both", expand=True)
    started = time.monotonic()
    frame_id = 0

    def update() -> None:
        nonlocal frame_id
        if not root.winfo_exists() or time.monotonic() - started >= duration_seconds:
            root.destroy()
            return
        state = pattern_state(frame_id, time.perf_counter_ns())
        canvas.delete("all")
        width = max(canvas.winfo_width(), 640)
        height = max(canvas.winfo_height(), 360)
        color = "white" if state.white else "black"
        opposite = "black" if state.white else "white"
        canvas.create_rectangle(0, 0, width // 3, height, fill=color, outline=color)
        canvas.create_text(
            width * 2 // 3,
            height // 2,
            text=f"{state.milliseconds}\nFRAME {state.frame_id}",
            fill=opposite,
            font=("Consolas", max(28, height // 10), "bold"),
            justify="center",
        )
        frame_id += 1
        root.after(16, update)

    root.after(0, update)
    root.mainloop()
