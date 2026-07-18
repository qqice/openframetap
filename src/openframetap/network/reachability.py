"""Small TCP reachability probe used by preflight and tests."""

from __future__ import annotations

import socket


def tcp_reachable(host: str, port: int, *, timeout: float = 1.0) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False

