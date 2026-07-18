"""Wrapping 16-bit DUML transaction sequence."""

from __future__ import annotations


class SequenceCounter:
    def __init__(self, initial: int = 0) -> None:
        if not 0 <= initial <= 0xFFFF:
            raise ValueError("initial sequence outside uint16")
        self._next = initial

    def take(self) -> int:
        value = self._next
        self._next = (self._next + 1) & 0xFFFF
        return value
