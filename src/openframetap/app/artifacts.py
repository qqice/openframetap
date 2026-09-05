from __future__ import annotations

import hashlib
import json
from pathlib import Path
import threading


class JsonlWriter:
    def __init__(self, path: Path, *, max_bytes: int = 0) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self.path = path
        self._stream = path.open("w", encoding="utf-8", newline="\n")
        self._lock = threading.Lock()
        self.max_bytes=max_bytes

    def write(self, payload: dict) -> None:
        with self._lock:
            if self.max_bytes and self._stream.tell()>=self.max_bytes:
                self._stream.close()
                previous=self.path.with_name(self.path.name+'.1')
                previous.replace(self.path.with_name(self.path.name+'.2')) if previous.exists() else None
                self.path.replace(previous)
                self._stream=self.path.open('w',encoding='utf-8',newline='\n')
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
