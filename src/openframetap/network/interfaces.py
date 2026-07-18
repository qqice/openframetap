"""Select the ROCK 4D LAN address without changing network state."""

from __future__ import annotations

from dataclasses import asdict, dataclass
import hashlib
import ipaddress
import socket
import subprocess
from typing import Callable


class NetworkPreflightError(RuntimeError):
    pass


RFC1918_NETWORKS = tuple(
    ipaddress.ip_network(value) for value in ("10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16")
)
TAILSCALE_CGNAT = ipaddress.ip_network("100.64.0.0/10")


@dataclass(frozen=True, slots=True)
class LanSnapshot:
    interface: str
    ipv4: str
    prefix_length: int
    gateway: str | None
    connection_masked: str | None
    connection_sha256: str | None

    def to_dict(self) -> dict:
        return asdict(self)


def is_rfc1918(value: str) -> bool:
    address = ipaddress.ip_address(value)
    return isinstance(address, ipaddress.IPv4Address) and any(
        address in network for network in RFC1918_NETWORKS
    )


def parse_brief_ipv4(output: str, *, interface: str = "wlan0") -> tuple[str, int]:
    for line in output.splitlines():
        fields = line.split()
        if len(fields) < 3 or fields[0] != interface:
            continue
        for field in fields[2:]:
            try:
                network = ipaddress.ip_interface(field)
            except ValueError:
                continue
            if not isinstance(network, ipaddress.IPv4Interface):
                continue
            address = network.ip
            if address in TAILSCALE_CGNAT or not is_rfc1918(str(address)):
                continue
            return str(address), network.network.prefixlen
    raise NetworkPreflightError(f"{interface} has no RFC1918 IPv4 address")


def build_rtmp_url(ipv4: str, stream_key: str, *, port: int = 1935) -> str:
    if not is_rfc1918(ipv4) or ipaddress.ip_address(ipv4) in TAILSCALE_CGNAT:
        raise NetworkPreflightError("RTMP target must be a wlan0 RFC1918 IPv4 address")
    if not stream_key or any(character in stream_key for character in "/?#\\\r\n"):
        raise ValueError("stream key must be one non-empty path segment")
    if not 1 <= port <= 65535:
        raise ValueError("port outside 1..65535")
    return f"rtmp://{ipv4}:{port}/live/{stream_key}"


def mask_identifier(value: str) -> str:
    if not value:
        return ""
    if len(value) == 1:
        return "*"
    return f"{value[0]}***{value[-1]}"


def hash_identifier(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def port_is_available(ipv4: str, port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        try:
            sock.bind((ipv4, port))
        except OSError:
            return False
    return True


def _run(command: list[str]) -> str:
    result = subprocess.run(command, text=True, capture_output=True, check=False)
    if result.returncode != 0:
        detail = result.stderr.strip() or f"exit {result.returncode}"
        raise NetworkPreflightError(f"read-only command failed: {command[0]}: {detail}")
    return result.stdout


def audit_wlan0(*, runner: Callable[[list[str]], str] = _run) -> LanSnapshot:
    brief = runner(["ip", "-4", "-br", "addr", "show", "wlan0"])
    ipv4, prefix = parse_brief_ipv4(brief)
    gateway_output = runner(["nmcli", "-g", "IP4.GATEWAY", "device", "show", "wlan0"])
    gateway = next((line.strip() for line in gateway_output.splitlines() if line.strip()), None)
    connection_output = runner(
        ["nmcli", "-g", "GENERAL.CONNECTION", "device", "show", "wlan0"]
    )
    connection = next(
        (line.strip() for line in connection_output.splitlines() if line.strip() and line.strip() != "--"),
        None,
    )
    return LanSnapshot(
        interface="wlan0",
        ipv4=ipv4,
        prefix_length=prefix,
        gateway=gateway,
        connection_masked=mask_identifier(connection) if connection else None,
        connection_sha256=hash_identifier(connection) if connection else None,
    )

