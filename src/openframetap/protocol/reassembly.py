"""Streaming DUML reassembly across arbitrary notification boundaries."""

from __future__ import annotations

from dataclasses import asdict, dataclass

from openframetap.protocol.crc import crc8_dji
from openframetap.protocol.duml import (
    MAGIC,
    MAX_FRAME_LENGTH,
    MIN_FRAME_LENGTH,
    DumlFrame,
    decode_duml_frame,
)


@dataclass(slots=True)
class ReassemblyStats:
    bytes_in: int = 0
    frames_ok: int = 0
    crc8_failures: int = 0
    crc16_failures: int = 0
    invalid_lengths: int = 0
    discarded_bytes: int = 0
    truncated_fragments: int = 0

    def to_dict(self) -> dict[str, int]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class ReassemblyEvent:
    kind: str
    raw: bytes
    frame: DumlFrame | None = None
    reason: str | None = None


class DumlStreamReassembler:
    def __init__(self) -> None:
        self._buffer = bytearray()
        self.stats = ReassemblyStats()

    @property
    def buffered_bytes(self) -> int:
        return len(self._buffer)

    def feed(self, data: bytes) -> list[ReassemblyEvent]:
        chunk = bytes(data)
        self.stats.bytes_in += len(chunk)
        self._buffer.extend(chunk)
        events: list[ReassemblyEvent] = []

        while self._buffer:
            marker = self._buffer.find(MAGIC)
            if marker < 0:
                discarded = bytes(self._buffer)
                self.stats.discarded_bytes += len(discarded)
                self._buffer.clear()
                events.append(ReassemblyEvent("error", discarded, reason="garbage_before_magic"))
                break
            if marker:
                discarded = bytes(self._buffer[:marker])
                del self._buffer[:marker]
                self.stats.discarded_bytes += len(discarded)
                events.append(ReassemblyEvent("error", discarded, reason="garbage_before_magic"))
            if len(self._buffer) < 4:
                break

            total_length = self._buffer[1] | ((self._buffer[2] & 0x03) << 8)
            if not MIN_FRAME_LENGTH <= total_length <= MAX_FRAME_LENGTH:
                raw = bytes(self._buffer[:4])
                del self._buffer[0]
                self.stats.invalid_lengths += 1
                events.append(ReassemblyEvent("error", raw, reason="invalid_length"))
                continue
            if crc8_dji(bytes(self._buffer[:3])) != self._buffer[3]:
                raw = bytes(self._buffer[:4])
                del self._buffer[0]
                self.stats.crc8_failures += 1
                events.append(ReassemblyEvent("error", raw, reason="crc8_mismatch"))
                continue
            if len(self._buffer) < total_length:
                break

            candidate = bytes(self._buffer[:total_length])
            frame = decode_duml_frame(candidate)
            if not frame.crc16_valid:
                del self._buffer[0]
                self.stats.crc16_failures += 1
                events.append(ReassemblyEvent("error", candidate, frame=frame, reason="crc16_mismatch"))
                continue
            del self._buffer[:total_length]
            self.stats.frames_ok += 1
            events.append(ReassemblyEvent("frame", candidate, frame=frame))
        return events

    def finish(self) -> list[ReassemblyEvent]:
        if not self._buffer:
            return []
        raw = bytes(self._buffer)
        self._buffer.clear()
        self.stats.truncated_fragments += 1
        return [ReassemblyEvent("error", raw, reason="truncated_at_end")]
