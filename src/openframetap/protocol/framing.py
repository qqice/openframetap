"""Small framing helpers shared by BLE transports."""

from __future__ import annotations


def split_for_att(data: bytes, *, mtu: int = 23) -> list[bytes]:
    """Split a frame into ATT value chunks (MTU minus three-byte ATT header)."""

    if mtu < 4:
        raise ValueError("ATT MTU must be at least 4")
    chunk_size = mtu - 3
    return [bytes(data[index : index + chunk_size]) for index in range(0, len(data), chunk_size)]
