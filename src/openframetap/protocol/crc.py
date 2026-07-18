"""CRC parameters used by DJI DUML frames.

The bit-reflection implementation intentionally follows the published parameter
sets rather than a lookup table.  It makes the initial value, polynomial and
reflection behavior independently testable.
"""

from __future__ import annotations


def _reflect(value: int, width: int) -> int:
    reflected = 0
    for bit in range(width):
        if value & (1 << bit):
            reflected |= 1 << (width - 1 - bit)
    return reflected


def _crc(
    data: bytes,
    *,
    width: int,
    polynomial: int,
    initial: int,
    reflect_input: bool,
    reflect_output: bool,
    xor_output: int = 0,
) -> int:
    mask = (1 << width) - 1
    top_bit = 1 << (width - 1)
    value = initial & mask
    for octet in data:
        if reflect_input:
            octet = _reflect(octet, 8)
        value ^= octet << (width - 8)
        for _ in range(8):
            value = ((value << 1) ^ polynomial) & mask if value & top_bit else (value << 1) & mask
    if reflect_output:
        value = _reflect(value, width)
    return (value ^ xor_output) & mask


def crc8_dji(data: bytes) -> int:
    """Return DJI DUML CRC-8 (poly 0x31, init 0xEE, refin/refout)."""

    return _crc(
        data,
        width=8,
        polynomial=0x31,
        initial=0xEE,
        reflect_input=True,
        reflect_output=True,
    )


def crc16_dji(data: bytes) -> int:
    """Return DJI DUML CRC-16 (poly 0x1021, init 0x496C, refin/refout)."""

    return _crc(
        data,
        width=16,
        polynomial=0x1021,
        initial=0x496C,
        reflect_input=True,
        reflect_output=True,
    )
