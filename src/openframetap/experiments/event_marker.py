"""Timestamped event records and a Linux TTY key reader."""

from __future__ import annotations

import asyncio
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import sys
import time
from typing import Any


@dataclass(frozen=True, slots=True)
class ExperimentEvent:
    wall_time_utc: str
    monotonic_ns: int
    event_code: str
    event_name: str
    phase: str
    optional_note: str | None = None
    observed_value: int | float | str | None = None

    @classmethod
    def now(
        cls,
        *,
        event_code: str,
        event_name: str,
        phase: str,
        optional_note: str | None = None,
        observed_value: int | float | str | None = None,
    ) -> "ExperimentEvent":
        return cls(
            wall_time_utc=datetime.now(timezone.utc).isoformat(),
            monotonic_ns=time.monotonic_ns(),
            event_code=event_code,
            event_name=event_name,
            phase=phase,
            optional_note=optional_note,
            observed_value=observed_value,
        )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class EventWriter:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text("", encoding="utf-8")
        self.events: list[ExperimentEvent] = []

    def record(self, event: ExperimentEvent) -> None:
        with self.path.open("a", encoding="utf-8", newline="\n") as stream:
            stream.write(json.dumps(event.to_dict(), ensure_ascii=False, sort_keys=True) + "\n")
            stream.flush()
        self.events.append(event)


class TTYKeyReader:
    """Read individual keys without requiring Enter, while restoring terminal state."""

    def __init__(self, stream=None) -> None:
        self.stream = stream or sys.stdin
        self.fd: int | None = None
        self._saved_attributes = None

    def __enter__(self) -> "TTYKeyReader":
        if not self.stream.isatty():
            raise RuntimeError("interactive experiment requires a real TTY")
        try:
            import termios
            import tty
        except ImportError as exc:
            raise RuntimeError("interactive experiment requires a POSIX TTY") from exc
        self.fd = self.stream.fileno()
        self._saved_attributes = termios.tcgetattr(self.fd)
        tty.setcbreak(self.fd)
        return self

    def __exit__(self, _exc_type, _exc, _traceback) -> None:
        if self.fd is None or self._saved_attributes is None:
            return
        import termios

        termios.tcsetattr(self.fd, termios.TCSADRAIN, self._saved_attributes)

    async def read_key(self, timeout: float | None = None) -> str | None:
        if self.fd is None:
            raise RuntimeError("TTY reader is not active")
        loop = asyncio.get_running_loop()
        future: asyncio.Future[str] = loop.create_future()

        def ready() -> None:
            if future.done():
                return
            data = os.read(self.fd, 1)
            future.set_result(data.decode("utf-8", errors="ignore"))

        loop.add_reader(self.fd, ready)
        try:
            if timeout is None:
                return await future
            try:
                return await asyncio.wait_for(future, timeout=timeout)
            except TimeoutError:
                return None
        finally:
            loop.remove_reader(self.fd)

    async def read_line(self, prompt: str, *, max_length: int = 200) -> str:
        print(prompt, end="", flush=True)
        characters: list[str] = []
        while True:
            key = await self.read_key()
            if key in {"\r", "\n"}:
                print(flush=True)
                return "".join(characters).strip()
            if key in {"\x7f", "\b"}:
                if characters:
                    characters.pop()
                    print("\b \b", end="", flush=True)
                continue
            if key and key.isprintable() and len(characters) < max_length:
                characters.append(key)
                print(key, end="", flush=True)
