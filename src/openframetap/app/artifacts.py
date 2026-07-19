from __future__ import annotations

import hashlib
import json
from pathlib import Path
import threading


class JsonlWriter:
    def __init__(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self.path = path
        self._stream = path.open("w", encoding="utf-8", newline="\n")
        self._lock = threading.Lock()

    def write(self, payload: dict) -> None:
        with self._lock:
            self._stream.write(json.dumps(payload, sort_keys=True, ensure_ascii=False) + "\n")
            self._stream.flush()

    def close(self) -> None:
        with self._lock:
            if not self._stream.closed:
                self._stream.close()


def write_manifest(directory: Path) -> None:
    paths = sorted(
        path for path in directory.rglob("*") if path.is_file() and path.name != "checksums.sha256"
    )
    with (directory / "checksums.sha256").open("w", encoding="ascii", newline="\n") as stream:
        for path in paths:
            digest = hashlib.sha256(path.read_bytes()).hexdigest()
            stream.write(f"{digest}  {path.relative_to(directory).as_posix()}\n")

