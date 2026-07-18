from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Any

from openframetap.models import json_safe, utc_timestamp


SECTION_RE = re.compile(r"^===== ([A-Z0-9 _./*:-]+) =====$", re.MULTILINE)


def split_sections(report: str) -> dict[str, str]:
    matches = list(SECTION_RE.finditer(report))
    sections: dict[str, str] = {}
    for index, match in enumerate(matches):
        end = matches[index + 1].start() if index + 1 < len(matches) else len(report)
        sections[match.group(1).strip()] = report[match.end() : end].strip()
    return sections


def _first_line(value: str) -> str | None:
    for line in value.splitlines():
        cleaned = line.strip().strip("\x00")
        if cleaned and not cleaned.startswith(("[", "$")):
            return cleaned
    return None


def _os_release(value: str) -> dict[str, str]:
    result: dict[str, str] = {}
    for line in value.splitlines():
        if "=" not in line:
            continue
        key, raw = line.split("=", 1)
        result[key] = raw.strip().strip('"')
    return result


def _wireless_modes(iw_list: str) -> list[str]:
    wanted = {"managed", "AP", "P2P-client", "P2P-GO", "P2P-device", "monitor"}
    found: set[str] = set()
    in_modes = False
    for line in iw_list.splitlines():
        if "Supported interface modes:" in line:
            in_modes = True
            continue
        if in_modes:
            match = re.match(r"\s*\*\s+(.+?)\s*$", line)
            if not match:
                if line.strip() and not line.startswith((" ", "\t")):
                    in_modes = False
                continue
            mode = match.group(1)
            if mode in wanted:
                found.add(mode)
    return [mode for mode in ("managed", "AP", "P2P-client", "P2P-GO", "P2P-device", "monitor") if mode in found]


def _wireless_interfaces(iw_dev: str) -> list[str]:
    return sorted(set(re.findall(r"^\s*Interface\s+(\S+)", iw_dev, re.MULTILINE)))


def parse_doctor_report(report: str) -> dict[str, Any]:
    sections = split_sections(report)
    uname = _first_line(sections.get("UNAME", "")) or ""
    kernel_match = re.search(r"Linux\s+\S+\s+(\S+)", uname)
    kernel_version = kernel_match.group(1) if kernel_match else uname
    os_release = _os_release(sections.get("OS RELEASE", ""))
    iw_dev = sections.get("IW DEV", "")
    iw_list = sections.get("IW LIST", "")
    interfaces = _wireless_interfaces(iw_dev)
    modes = _wireless_modes(iw_list)

    lsusb = "\n".join(
        [sections.get("LSUSB", ""), sections.get("LSUSB FALLBACK", ""), sections.get("USB SYSFS", "")]
    )
    usb_candidates = [
        line.strip()
        for line in lsusb.splitlines()
        if re.search(r"aic|8800|fcu|quectel|wireless|bluetooth", line, re.I)
    ]
    usb_id_match = re.search(r"\bID(?:=|\s+)([0-9a-f]{4}:[0-9a-f]{4})\b", "\n".join(usb_candidates), re.I)

    drivers: dict[str, str] = {}
    for line in sections.get("NETWORK DRIVERS", "").splitlines():
        match = re.search(r"INTERFACE=(\S+)\s+DRIVER=(\S+)", line)
        if match:
            drivers[match.group(1)] = match.group(2)
    wifi_driver = {name: drivers.get(name) for name in interfaces}
    wifi_module_match = re.search(
        r"^(aic\S*(?:fdrv|wlan|wifi)\S*)\s+", sections.get("LSMOD", ""), re.MULTILINE | re.I
    )
    if wifi_module_match:
        for name, driver in wifi_driver.items():
            if not driver or re.search(r"(?:^|_)(?:bt)?usb$", driver, re.I):
                wifi_driver[name] = wifi_module_match.group(1)

    discovered_firmware_paths = [
        line.strip()
        for line in sections.get("WIRELESS FIRMWARE", "").splitlines()
        if line.strip().startswith("/lib/firmware/")
    ]
    loaded_firmware_paths = sorted(
        set(re.findall(r"firmware path\s*=\s*(/lib/firmware/\S+)", sections.get("WIRELESS DMESG", ""), re.I))
    )
    firmware_paths = loaded_firmware_paths or discovered_firmware_paths
    bt_text = "\n".join(
        [
            sections.get("BLUETOOTHCTL LIST", ""),
            sections.get("BLUETOOTHCTL SHOW", ""),
            sections.get("BTMGMT INFO", ""),
        ]
    )
    controllers = sorted(
        set(
            re.findall(
                r"(?:Controller|hci\d+:)\s+([0-9A-F]{2}(?::[0-9A-F]{2}){5})",
                bt_text,
                re.I,
            )
        )
    )
    bluez_match = re.search(r"(\d+(?:\.\d+)+)", sections.get("BLUEZ VERSION", ""))
    route = sections.get("IP ROUTE", "")
    default_match = re.search(r"^default\s+.*?\bdev\s+(\S+)", route, re.MULTILINE)
    default_interface = default_match.group(1) if default_match else None
    ssh_route = sections.get("SSH ROUTE", "")
    ssh_dev_match = re.search(r"\bdev\s+(\S+)", ssh_route)
    ssh_route_interface = ssh_dev_match.group(1) if ssh_dev_match else None

    device_paths = sections.get("MEDIA DEVICE PATHS", "")
    drm_devices = sorted(set(re.findall(r"^/dev/dri/\S+", device_paths, re.MULTILINE)))
    video_devices = sorted(set(re.findall(r"^/dev/video\S+", device_paths, re.MULTILINE)))
    decoders = [
        line.strip()
        for name in ("GSTREAMER DECODERS", "FFMPEG DECODERS")
        for line in sections.get(name, "").splitlines()
        if line.strip() and not line.startswith(("[", "$"))
    ]

    warnings: list[str] = []
    if not usb_candidates:
        warnings.append("FCU760K/AIC8800 USB device not identified in lsusb")
    if not interfaces:
        warnings.append("wireless netdev not created")
    elif not any(wifi_driver.values()):
        warnings.append("wireless interface exists but its kernel driver was not resolved")
    if not firmware_paths:
        warnings.append("AIC8800/FCU760K firmware paths not found")
    rfkill = sections.get("RFKILL", "")
    if re.search(r"(?:Soft|Hard) blocked:\s+yes", rfkill, re.I):
        warnings.append("rfkill blocking is active")
    nmcli = sections.get("NMCLI DEVICE", "")
    for interface in interfaces:
        if re.search(rf"^{re.escape(interface)}\s+.*\bunmanaged\b", nmcli, re.MULTILINE):
            warnings.append(f"NetworkManager does not manage {interface}")
    if not controllers:
        warnings.append("Bluetooth HCI/controller not found")
    bluetooth_status = sections.get("BLUETOOTH SERVICE", "")
    if "Active: active (running)" not in bluetooth_status:
        warnings.append("BlueZ bluetooth.service is not confirmed active")
    dmesg = sections.get("WIRELESS DMESG", "")
    aic_failures = [
        line.strip()
        for line in dmesg.splitlines()
        if re.search(r"aic|8800|fcu", line, re.I)
        and re.search(r"probe.*fail|firmware.*(?:not found|failed to load)", line, re.I)
    ]
    if aic_failures:
        if interfaces and controllers:
            warnings.append("AIC boot log has an initial probe failure, but Wi-Fi and Bluetooth are currently operational")
        else:
            warnings.append("AIC driver or firmware load failure appears in dmesg")

    return {
        "captured_at": utc_timestamp(),
        "hostname": _first_line(sections.get("HOSTNAME", "")),
        "board_model": _first_line(sections.get("BOARD MODEL", "")),
        "distribution": os_release.get("ARMBIAN_PRETTY_NAME") or os_release.get("PRETTY_NAME"),
        "kernel_version": kernel_version,
        "kernel_is_expected_vendor_6_1_115": bool(
            re.match(r"^6\.1\.115.*vendor.*rk35xx", kernel_version, re.I)
        ),
        "wifi_usb_id": usb_id_match.group(1).lower() if usb_id_match else None,
        "wifi_usb_description": usb_candidates,
        "wifi_driver": wifi_driver,
        "wifi_firmware_paths": firmware_paths,
        "wifi_firmware_discovered_path_count": len(discovered_firmware_paths),
        "wifi_boot_log_failures": aic_failures,
        "wireless_interfaces": interfaces,
        "wireless_interface_modes": modes,
        "bluetooth_controllers": controllers,
        "bluez_version": bluez_match.group(1) if bluez_match else None,
        "default_route_interface": default_interface,
        "ssh_route_interface": ssh_route_interface,
        "ssh_route_risk": ssh_route_interface in interfaces if ssh_route_interface else None,
        "drm_devices": drm_devices,
        "video_devices": video_devices,
        "hardware_decoders": decoders,
        "warnings": warnings,
    }


def parse_display_report(report: str) -> dict[str, Any]:
    sections = split_sections(report)
    connector_text = sections.get("DRM CONNECTORS", "")
    connectors: list[dict[str, Any]] = []
    current: dict[str, Any] | None = None
    for line in connector_text.splitlines():
        if line.startswith(("$", "[")):
            continue
        if line.startswith("CONNECTOR="):
            if current:
                connectors.append(current)
            current = {"name": line.split("=", 1)[1], "modes": []}
        elif current is not None and "=" in line:
            key, value = line.split("=", 1)
            if key == "MODE":
                current["modes"].append(value)
            else:
                current[key.lower()] = value
    if current:
        connectors.append(current)
    active = [
        item
        for item in connectors
        if item.get("status") == "connected" and item.get("enabled") == "enabled"
    ]
    dsi_active = [item for item in active if "DSI" in str(item.get("name", "")).upper()]
    selected = dsi_active[0] if dsi_active else (active[0] if active else None)
    modes = selected.get("modes", []) if selected else []
    mode_720p = any(re.search(r"(?:1280x720|720x1280)", mode) for mode in modes)
    orientation = None
    if modes:
        match = re.search(r"(\d+)x(\d+)", modes[0])
        if match:
            width, height = int(match.group(1)), int(match.group(2))
            orientation = "portrait" if height > width else "landscape"

    touch_probe: list[dict[str, Any]] = []
    raw_touch = sections.get("INPUT IOCTL PROBE", "")
    try:
        start = raw_touch.find("[")
        end_marker = raw_touch.rfind("\n[exit-status=")
        end = end_marker if end_marker >= 0 else len(raw_touch)
        touch_probe = json.loads(raw_touch[start:end]) if start >= 0 else []
    except (ValueError, TypeError):
        pass
    touch_devices = [item for item in touch_probe if item.get("is_touchscreen")]
    processes = "\n".join(
        line
        for line in sections.get("DISPLAY PROCESSES", "").splitlines()
        if not line.startswith(("$", "["))
    )
    compositor_names = [
        name
        for name in ("weston", "wayfire", "kwin", "gnome-shell", "Xorg", "Xwayland", "sway", "labwc")
        if re.search(rf"\b{re.escape(name)}\b", processes, re.I)
    ]
    dri = sections.get("DRI DEVICE PATHS", "")
    session_lines = [
        line
        for line in sections.get("LOGIN SESSIONS", "").splitlines()
        if line and not line.startswith(("$", "["))
    ]
    session_details: list[dict[str, str]] = []
    current_session: dict[str, str] | None = None
    for line in sections.get("SESSION DETAILS", "").splitlines():
        if line.startswith("SESSION="):
            if current_session:
                session_details.append(current_session)
            current_session = {"id": line.split("=", 1)[1]}
        elif current_session is not None and "=" in line and not line.startswith(("$", "[")):
            key, value = line.split("=", 1)
            current_session[key.lower()] = value
    if current_session:
        session_details.append(current_session)
    config_lines = [
        line
        for line in sections.get("DISPLAY AND INPUT CONFIG", "").splitlines()
        if line and not line.startswith(("$", "["))
    ]
    rotation_match = re.search(
        r"<rotation>\s*([^<]+)\s*</rotation>", "\n".join(config_lines), re.I
    )
    return {
        "captured_at": utc_timestamp(),
        "connectors": connectors,
        "active_connector": selected,
        "mode_is_720p": mode_720p,
        "orientation_inferred_from_mode": orientation,
        "drm_cards": sorted(set(re.findall(r"/dev/dri/card\d+", dri))),
        "render_nodes": sorted(set(re.findall(r"/dev/dri/renderD\d+", dri))),
        "display_processes": compositor_names,
        "sessions": session_lines,
        "session_details": session_details,
        "touch_devices": touch_devices,
        "desktop_rotation": rotation_match.group(1) if rotation_match else None,
        "mapping_or_rotation_evidence": config_lines,
        "warnings": [] if selected else ["no active DRM connector identified"],
    }


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(json_safe(payload), indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    import sys

    arguments = list(sys.argv[1:] if argv is None else argv)
    if arguments and arguments[0] == "display":
        parser = argparse.ArgumentParser(prog="openframetap-doctor display")
        parser.add_argument("report", type=Path)
        parser.add_argument("--json", required=True, type=Path)
        args = parser.parse_args(arguments[1:])
        _write_json(
            args.json,
            parse_display_report(args.report.read_text(encoding="utf-8", errors="replace")),
        )
        return 0
    parser = argparse.ArgumentParser(prog="openframetap-doctor")
    parser.add_argument("report", type=Path)
    parser.add_argument("--json", dest="json_path", required=True, type=Path)
    args = parser.parse_args(arguments)
    _write_json(args.json_path, parse_doctor_report(args.report.read_text(encoding="utf-8", errors="replace")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
