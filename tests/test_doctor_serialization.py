import json

from openframetap.doctor import parse_doctor_report
from openframetap.models import json_safe


def report_section(name: str, body: str) -> str:
    return f"===== {name} =====\n$ fixture-command\n{body}\n[exit-status=0]\n\n"


def test_doctor_handles_no_wifi_and_no_bluetooth() -> None:
    report = "".join(
        [
            report_section("HOSTNAME", "rock-test"),
            report_section("UNAME", "Linux rock-test 6.1.115-vendor-rk35xx #1 aarch64 GNU/Linux"),
            report_section("OS RELEASE", 'ARMBIAN_PRETTY_NAME="Armbian test"'),
            report_section("BOARD MODEL", "ROCK 4D"),
            report_section("LSUSB", "Bus 001 Device 001: ID 1d6b:0002 Linux Foundation"),
            report_section("IW DEV", ""),
            report_section("IW LIST", ""),
            report_section("WIRELESS FIRMWARE", ""),
            report_section("BLUETOOTHCTL LIST", ""),
            report_section("BLUETOOTHCTL SHOW", ""),
            report_section("BTMGMT INFO", ""),
            report_section("BLUETOOTH SERVICE", "inactive (dead)"),
        ]
    )
    payload = parse_doctor_report(report)
    assert payload["hostname"] == "rock-test"
    assert payload["kernel_is_expected_vendor_6_1_115"] is True
    assert payload["wireless_interfaces"] == []
    assert payload["bluetooth_controllers"] == []
    assert "wireless netdev not created" in payload["warnings"]
    assert "Bluetooth HCI/controller not found" in payload["warnings"]


def test_json_serialization_preserves_bytes_as_hex() -> None:
    payload = json_safe({"raw": b"\x00\xff", "nested": [bytearray(b"\x12")]})
    assert payload == {"raw": "00ff", "nested": ["12"]}
    assert json.loads(json.dumps(payload)) == payload
