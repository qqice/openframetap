from __future__ import annotations

import fcntl
import json
import os
import struct
from pathlib import Path
from typing import Any


EV_KEY = 0x01
EV_ABS = 0x03
BTN_TOUCH = 0x14A
ABS_MT_SLOT = 0x2F
ABS_MT_POSITION_X = 0x35
ABS_MT_POSITION_Y = 0x36

IOC_NRBITS = 8
IOC_TYPEBITS = 8
IOC_SIZEBITS = 14
IOC_NRSHIFT = 0
IOC_TYPESHIFT = IOC_NRSHIFT + IOC_NRBITS
IOC_SIZESHIFT = IOC_TYPESHIFT + IOC_TYPEBITS
IOC_DIRSHIFT = IOC_SIZESHIFT + IOC_SIZEBITS
IOC_READ = 2


def _ioc(direction: int, kind: int, number: int, size: int) -> int:
    return (
        (direction << IOC_DIRSHIFT)
        | (kind << IOC_TYPESHIFT)
        | (number << IOC_NRSHIFT)
        | (size << IOC_SIZESHIFT)
    )


def _eviocgname(length: int) -> int:
    return _ioc(IOC_READ, ord("E"), 0x06, length)


def _eviocgbit(event_type: int, length: int) -> int:
    return _ioc(IOC_READ, ord("E"), 0x20 + event_type, length)


def _eviocgabs(axis: int) -> int:
    return _ioc(IOC_READ, ord("E"), 0x40 + axis, struct.calcsize("iiiiii"))


def _bit_is_set(buffer: bytes, bit: int) -> bool:
    byte_index, bit_index = divmod(bit, 8)
    return byte_index < len(buffer) and bool(buffer[byte_index] & (1 << bit_index))


def _bits(fd: int, event_type: int, highest_bit: int) -> bytes:
    length = (highest_bit // 8) + 1
    buffer = bytearray(length)
    fcntl.ioctl(fd, _eviocgbit(event_type, length), buffer, True)
    return bytes(buffer)


def _abs_info(fd: int, axis: int) -> dict[str, int] | None:
    buffer = bytearray(struct.calcsize("iiiiii"))
    try:
        fcntl.ioctl(fd, _eviocgabs(axis), buffer, True)
    except OSError:
        return None
    value, minimum, maximum, fuzz, flat, resolution = struct.unpack("iiiiii", buffer)
    return {
        "value": value,
        "minimum": minimum,
        "maximum": maximum,
        "fuzz": fuzz,
        "flat": flat,
        "resolution": resolution,
    }


def probe_event(path: Path) -> dict[str, Any]:
    result: dict[str, Any] = {
        "event_node": str(path),
        "name": None,
        "is_touchscreen": False,
        "max_touch_points": None,
        "axes": {},
        "error": None,
    }
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NONBLOCK)
    except OSError as exc:
        result["error"] = f"{type(exc).__name__}: {exc}"
        return result
    try:
        name_buffer = bytearray(256)
        fcntl.ioctl(fd, _eviocgname(len(name_buffer)), name_buffer, True)
        result["name"] = bytes(name_buffer).split(b"\x00", 1)[0].decode(
            "utf-8", errors="replace"
        )
        event_bits = _bits(fd, 0, EV_ABS)
        key_bits = _bits(fd, EV_KEY, BTN_TOUCH) if _bit_is_set(event_bits, EV_KEY) else b""
        abs_bits = _bits(fd, EV_ABS, ABS_MT_POSITION_Y) if _bit_is_set(event_bits, EV_ABS) else b""
        has_touch_key = _bit_is_set(key_bits, BTN_TOUCH)
        has_mt_axes = _bit_is_set(abs_bits, ABS_MT_POSITION_X) and _bit_is_set(
            abs_bits, ABS_MT_POSITION_Y
        )
        result["is_touchscreen"] = has_touch_key or has_mt_axes
        for axis, label in (
            (ABS_MT_SLOT, "ABS_MT_SLOT"),
            (ABS_MT_POSITION_X, "ABS_MT_POSITION_X"),
            (ABS_MT_POSITION_Y, "ABS_MT_POSITION_Y"),
        ):
            info = _abs_info(fd, axis)
            if info is not None:
                result["axes"][label] = info
        slot = result["axes"].get("ABS_MT_SLOT")
        if slot:
            result["max_touch_points"] = slot["maximum"] - slot["minimum"] + 1
    except OSError as exc:
        result["error"] = f"{type(exc).__name__}: {exc}"
    finally:
        os.close(fd)
    return result


def main() -> int:
    records = [probe_event(path) for path in sorted(Path("/dev/input").glob("event*"))]
    print(json.dumps(records, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
