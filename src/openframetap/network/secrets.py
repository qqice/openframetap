"""Load Wi-Fi/RTMP secrets without serializing or logging their values."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import os
from pathlib import Path
import shutil
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


@dataclass(frozen=True, slots=True, repr=False)
class WifiProvisioningSecrets:
    ssid: str
    psk: str

    def __repr__(self) -> str:
        return "WifiProvisioningSecrets(<redacted>)"

    def __str__(self) -> str:
        return "WifiProvisioningSecrets(<redacted>)"

    def sanitized(self) -> dict:
        return {
            "ssid_masked": (
                f"{self.ssid[0]}***{self.ssid[-1]}" if len(self.ssid) > 1 else "*"
            ),
            "ssid_sha256": hashlib.sha256(self.ssid.encode()).hexdigest(),
            "ssid_encoded_length": len(self.ssid.encode("utf-8")),
            "psk_length": len(self.psk.encode("utf-8")),
            "psk_sha256": hashlib.sha256(self.psk.encode()).hexdigest(),
        }

    def redact(self, text: str) -> str:
        result = str(text)
        for value in (self.ssid, self.psk):
            if value:
                result = result.replace(value, "<redacted>")
        return result


def load_wifi_provisioning_secrets(
    *, environ: Mapping[str, str] | None = None, secret_file: Path | None = None
) -> WifiProvisioningSecrets:
    values = dict(_parse_env_file(secret_file)) if secret_file else {}
    source = os.environ if environ is None else environ
    required = SECRET_NAMES[:2]
    for name in required:
        if source.get(name):
            values[name] = source[name]
    missing = [name for name in required if not values.get(name)]
    if missing:
        raise SecretConfigurationError(
            "missing required Wi-Fi secret variables: " + ", ".join(missing)
        )
    ssid = values["OPENFRAMETAP_WIFI_SSID"]
    psk = values["OPENFRAMETAP_WIFI_PSK"]
    if not 1 <= len(ssid.encode("utf-8")) <= 32:
        raise SecretConfigurationError("SSID encoded length must be 1..32 bytes")
    if not 8 <= len(psk.encode("utf-8")) <= 63:
        raise SecretConfigurationError("Wi-Fi PSK encoded length must be 8..63 bytes")
    return WifiProvisioningSecrets(ssid=ssid, psk=psk)


def store_wifi_provisioning_secrets(
    path: Path, secrets: WifiProvisioningSecrets
) -> Path | None:
    """Atomically store Wi-Fi values at 0600 without logging their contents."""

    path = path.expanduser().resolve()
    for name, value in (("SSID", secrets.ssid), ("Wi-Fi PSK", secrets.psk)):
        if any(character in value for character in "\r\n"):
            raise SecretConfigurationError(f"{name} must be one line")
        if value != value.strip() or value[:1] in {'"', "'"} or value[-1:] in {'"', "'"}:
            raise SecretConfigurationError(
                f"{name} cannot have outer whitespace or quote characters"
            )
    path.parent.mkdir(parents=True, exist_ok=True)
    if os.name != "nt":
        path.parent.chmod(0o700)
    backup: Path | None = None
    retained_stream_key_line: bytes | None = None
    if path.exists():
        if path.is_symlink():
            raise SecretConfigurationError("secret file cannot be a symbolic link")
        require_private_file(path)
        for line in path.read_bytes().splitlines():
            if line.startswith(b"OPENFRAMETAP_RTMP_STREAM_KEY="):
                retained_stream_key_line = line
                break
        backup = path.with_name(path.name + ".bak")
        shutil.copyfile(path, backup)
        if os.name != "nt":
            backup.chmod(0o600)
    content = (
        f"OPENFRAMETAP_WIFI_SSID={secrets.ssid}\n"
        f"OPENFRAMETAP_WIFI_PSK={secrets.psk}\n"
    ).encode("utf-8")
    if retained_stream_key_line is not None:
        content += retained_stream_key_line + b"\n"
    temporary = path.with_name(path.name + ".new")
    flags = os.O_WRONLY | os.O_CREAT | os.O_TRUNC
    descriptor = os.open(temporary, flags, 0o600)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise
    if os.name != "nt":
        temporary.chmod(0o600)
    temporary.replace(path)
    if os.name != "nt":
        path.chmod(0o600)
    return backup


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
