from __future__ import annotations

import os
from pathlib import Path

import pytest

from openframetap.network.interfaces import (
    NetworkPreflightError,
    build_rtmp_url,
    parse_brief_ipv4,
)
from openframetap.network.secrets import (
    LivestreamSecrets,
    SecretConfigurationError,
    WifiProvisioningSecrets,
    load_livestream_secrets,
    load_wifi_provisioning_secrets,
    require_private_directory,
    require_private_file,
    store_wifi_provisioning_secrets,
    store_rtmp_stream_key,
)


def test_wlan0_rfc1918_address_selected() -> None:
    assert parse_brief_ipv4("wlan0 UP 192.168.1.229/24\n") == ("192.168.1.229", 24)


@pytest.mark.parametrize("value", ["tailscale0 UNKNOWN 100.125.223.67/32", "wlan0 UP 100.125.1.2/32", "wlan0 UP"])
def test_tailscale_or_missing_ipv4_is_rejected(value: str) -> None:
    with pytest.raises(NetworkPreflightError):
        parse_brief_ipv4(value)


def test_rtmp_url_requires_wlan_rfc1918() -> None:
    assert build_rtmp_url("192.168.1.229", "pocket3") == "rtmp://192.168.1.229:1935/live/pocket3"
    for address in ("100.125.223.67", "127.0.0.1", "8.8.8.8"):
        with pytest.raises(NetworkPreflightError):
            build_rtmp_url(address, "pocket3")


def test_secrets_are_redacted_and_repr_is_safe() -> None:
    secrets = LivestreamSecrets("TestNetwork", "not-a-real-password", "secret-key")
    rendered = repr(secrets) + str(secrets) + secrets.redact(
        "TestNetwork not-a-real-password secret-key"
    )
    for value in (secrets.ssid, secrets.psk, secrets.stream_key):
        assert value not in rendered
    summary = secrets.sanitized()
    assert summary["ssid_masked"] == "T***k"
    assert summary["psk_length"] == len(secrets.psk)
    assert secrets.psk not in str(summary)


def test_load_secrets_reports_names_not_values() -> None:
    with pytest.raises(SecretConfigurationError, match="OPENFRAMETAP_WIFI_PSK"):
        load_livestream_secrets(
            environ={
                "OPENFRAMETAP_WIFI_SSID": "Fixture",
                "OPENFRAMETAP_RTMP_STREAM_KEY": "fixture",
            }
        )


def test_wifi_only_secrets_do_not_require_or_expose_stream_key() -> None:
    secrets = load_wifi_provisioning_secrets(
        environ={
            "OPENFRAMETAP_WIFI_SSID": "Fixture5G",
            "OPENFRAMETAP_WIFI_PSK": "fixture-password",
        }
    )
    assert isinstance(secrets, WifiProvisioningSecrets)
    rendered = repr(secrets) + str(secrets) + secrets.redact(
        "Fixture5G fixture-password"
    )
    assert "Fixture5G" not in rendered
    assert "fixture-password" not in rendered
    assert "stream" not in str(secrets.sanitized()).lower()


def test_secret_file_permissions_and_private_proposal_directory(tmp_path: Path) -> None:
    secret_file = tmp_path / "secrets.env"
    secret_file.write_text("OPENFRAMETAP_WIFI_SSID=Fixture\n", encoding="utf-8")
    if os.name != "nt":
        secret_file.chmod(0o644)
        with pytest.raises(SecretConfigurationError, match="0600"):
            require_private_file(secret_file)
        secret_file.chmod(0o600)
    require_private_file(secret_file)
    private = require_private_directory(tmp_path / "artifacts" / "private" / "proposal")
    assert private.is_dir()
    with pytest.raises(SecretConfigurationError, match="artifacts/private"):
        require_private_directory(tmp_path / "public")


def test_wifi_secret_file_is_private_and_backed_up_without_value_logging(
    tmp_path: Path,
) -> None:
    path = tmp_path / "secrets.env"
    first = WifiProvisioningSecrets("Fixture5G", "fixture-password")
    assert store_wifi_provisioning_secrets(path, first) is None
    if os.name != "nt":
        assert path.stat().st_mode & 0o777 == 0o600
    second = WifiProvisioningSecrets("FixtureGuest", "another-password")
    backup = store_wifi_provisioning_secrets(path, second)
    assert backup is not None and backup.is_file()
    assert "Fixture5G" in backup.read_text(encoding="utf-8")
    assert "FixtureGuest" in path.read_text(encoding="utf-8")


def test_stream_key_update_preserves_wifi_and_creates_private_backup(tmp_path: Path) -> None:
    path = tmp_path / "secrets.env"
    store_wifi_provisioning_secrets(
        path, WifiProvisioningSecrets("Fixture5G", "fixture-password")
    )
    backup = store_rtmp_stream_key(path, "private-stream-key")
    assert backup.is_file()
    current = load_livestream_secrets(secret_file=path, environ={})
    assert current.ssid == "Fixture5G"
    assert current.psk == "fixture-password"
    assert current.stream_key == "private-stream-key"
    assert "OPENFRAMETAP_RTMP_STREAM_KEY" not in backup.read_text(encoding="utf-8")
    if os.name != "nt":
        assert path.stat().st_mode & 0o777 == 0o600
        assert backup.stat().st_mode & 0o777 == 0o600


@pytest.mark.parametrize("value", ["", "bad/key", "bad?key", " bad", "bad\nkey"])
def test_stream_key_update_rejects_unsafe_values(tmp_path: Path, value: str) -> None:
    path = tmp_path / "secrets.env"
    store_wifi_provisioning_secrets(
        path, WifiProvisioningSecrets("Fixture5G", "fixture-password")
    )
    before = path.read_bytes()
    with pytest.raises(SecretConfigurationError):
        store_rtmp_stream_key(path, value)
    assert path.read_bytes() == before
