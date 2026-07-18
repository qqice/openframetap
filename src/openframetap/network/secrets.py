"""Load Wi-Fi/RTMP secrets without serializing or logging their values."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import os
from pathlib import Path
import stat
from typing import Mapping


SECRET_NAMES = (
    "OPENFRAMETAP_WIFI_SSID",
    "OPENFRAMETAP_WIFI_PSK",
    "OPENFRAMETAP_RTMP_STREAM_KEY",
)


class SecretConfigurationError(RuntimeError):
    pass


def require_private_file(path: Path) -> None:
    if not path.is_file():
        raise SecretConfigurationError("secret file does not exist")
    if os.name != "nt":
        mode = stat.S_IMODE(path.stat().st_mode)
        if mode != 0o600:
            raise SecretConfigurationError(
                f"secret file permissions must be 0600, found {mode:04o}"
            )


def require_private_directory(path: Path) -> Path:
    normalized = path.resolve()
    parts = {part.lower() for part in normalized.parts}
    if not {"artifacts", "private"}.issubset(parts):
        raise SecretConfigurationError("sensitive output must be under artifacts/private")
    normalized.mkdir(parents=True, exist_ok=True)
    if os.name != "nt":
        normalized.chmod(0o700)
    return normalized


def _parse_env_file(path: Path) -> dict[str, str]:
    require_private_file(path)
    result: dict[str, str] = {}
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if "=" not in stripped:
            raise SecretConfigurationError(f"malformed secret entry at line {line_number}")
        name, value = stripped.split("=", 1)
        name = name.strip()
        if name not in SECRET_NAMES:
            continue
        result[name] = value.strip().strip('"').strip("'")
    return result


@dataclass(frozen=True, slots=True, repr=False)
class LivestreamSecrets:
    ssid: str
    psk: str
    stream_key: str

    def __repr__(self) -> str:
        return "LivestreamSecrets(<redacted>)"

    def __str__(self) -> str:
        return "LivestreamSecrets(<redacted>)"

    def sanitized(self) -> dict:
        return {
            "ssid_masked": (f"{self.ssid[0]}***{self.ssid[-1]}" if len(self.ssid) > 1 else "*"),
            "ssid_sha256": hashlib.sha256(self.ssid.encode()).hexdigest(),
            "psk_length": len(self.psk),
            "psk_sha256": hashlib.sha256(self.psk.encode()).hexdigest(),
            "stream_key_length": len(self.stream_key),
            "stream_key_sha256": hashlib.sha256(self.stream_key.encode()).hexdigest(),
        }

    def redact(self, text: str) -> str:
        result = str(text)
        for value in (self.ssid, self.psk, self.stream_key):
            if value:
                result = result.replace(value, "<redacted>")
        return result


def load_livestream_secrets(
    *, environ: Mapping[str, str] | None = None, secret_file: Path | None = None
) -> LivestreamSecrets:
    values = dict(_parse_env_file(secret_file)) if secret_file else {}
    source = os.environ if environ is None else environ
    for name in SECRET_NAMES:
        if source.get(name):
            values[name] = source[name]
    missing = [name for name in SECRET_NAMES if not values.get(name)]
    if missing:
        raise SecretConfigurationError("missing required secret variables: " + ", ".join(missing))
    ssid = values["OPENFRAMETAP_WIFI_SSID"]
    psk = values["OPENFRAMETAP_WIFI_PSK"]
    stream_key = values["OPENFRAMETAP_RTMP_STREAM_KEY"]
    if not 1 <= len(ssid.encode("utf-8")) <= 32:
        raise SecretConfigurationError("SSID encoded length must be 1..32 bytes")
    if not 8 <= len(psk) <= 63:
        raise SecretConfigurationError("Wi-Fi PSK length must be 8..63 characters")
    if not stream_key or any(character in stream_key for character in "/?#\\\r\n"):
        raise SecretConfigurationError("RTMP stream key must be one path segment")
    return LivestreamSecrets(ssid=ssid, psk=psk, stream_key=stream_key)

