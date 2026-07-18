#!/usr/bin/env bash
set -uo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"
mkdir -p artifacts
stamp="$(date +%Y%m%d-%H%M%S)"
stem="doctor-$stamp"
text_path="artifacts/$stem.txt"
json_path="artifacts/$stem.json"

section() {
  local title="$1"
  shift
  printf '===== %s =====\n' "$title"
  printf '$'
  printf ' %q' "$@"
  printf '\n'
  "$@"
  local status=$?
  printf '[exit-status=%s]\n\n' "$status"
  return 0
}

{
  section HOSTNAME hostname
  section ID id
  section UNAME uname -a
  section "OS RELEASE" cat /etc/os-release
  section "BOARD MODEL" bash -lc "tr -d '\\000' </proc/device-tree/model; printf '\\n'"
  section CMDLINE cat /proc/cmdline
  section LSUSB lsusb -nn
  section "LSUSB FALLBACK" bash -lc 'lsusb 2>/dev/null || true'
  section "USB SYSFS" bash -lc 'for path in /sys/bus/usb/devices/*; do [ -r "$path/idVendor" ] || continue; vendor=$(cat "$path/idVendor"); product_id=$(cat "$path/idProduct"); product=$(tr "\\n" " " <"$path/product" 2>/dev/null || true); manufacturer=$(tr "\\n" " " <"$path/manufacturer" 2>/dev/null || true); printf "DEVICE=%s ID=%s:%s MANUFACTURER=%s PRODUCT=%s\\n" "${path##*/}" "$vendor" "$product_id" "$manufacturer" "$product"; done'
  section LSPCI bash -lc 'lspci -nn 2>/dev/null || true'
  section LSMOD lsmod
  section DKMS bash -lc 'dkms status 2>&1 || true'
  section RFKILL rfkill list
  section "IP LINK" ip -br link
  section "IP ADDRESS" ip -br address
  section "IP ROUTE" ip route
  section "SSH ROUTE" bash -lc 'printf "SSH_CONNECTION=%s\\n" "${SSH_CONNECTION:-}"; client="${SSH_CONNECTION%% *}"; if [ -n "$client" ]; then ip route get "$client"; fi'
  section "IW DEV" iw dev
  section "IW LIST" iw list
  section "NETWORK DRIVERS" bash -lc 'for path in /sys/class/net/*; do iface=${path##*/}; [ -e "$path/device" ] || continue; driver=""; fallback=""; for function in "$path/device"/*:*; do [ -L "$function/driver" ] || continue; candidate=$(basename "$(readlink -f "$function/driver")"); case "$candidate" in *fdrv*|*wlan*|*wifi*) driver="$candidate"; break ;; usb|usbfs) ;; *) fallback="$candidate" ;; esac; done; if [ -z "$driver" ]; then driver="${fallback:-$(basename "$(readlink -f "$path/device/driver" 2>/dev/null)" 2>/dev/null || true)}"; fi; printf "INTERFACE=%s DRIVER=%s\\n" "$iface" "${driver:-unknown}"; ethtool -i "$iface" 2>/dev/null || true; done'
  section "NMCLI GENERAL" nmcli general status
  section "NMCLI DEVICE" nmcli device status
  section "NETWORKMANAGER SERVICE" systemctl status NetworkManager --no-pager
  section "BLUETOOTH SERVICE" systemctl status bluetooth --no-pager
  section "BLUETOOTHCTL LIST" bluetoothctl list
  section "BLUETOOTHCTL SHOW" bluetoothctl show
  section "BTMGMT INFO" btmgmt info
  section "BLUEZ VERSION" bash -lc 'bluetoothctl --version 2>/dev/null || true'
  section "WIRELESS DMESG" bash -lc "dmesg 2>&1 | grep -Ei 'aic|8800|fcu|wifi|wlan|bluetooth|btusb|firmware' || true"
  section "WIRELESS FIRMWARE" bash -lc "find /lib/firmware -maxdepth 5 \\( -iname '*aic*' -o -iname '*8800*' -o -iname '*fcu*' \\) -print 2>/dev/null"
  section "MEDIA DEVICES" bash -lc 'ls -l /dev/dri /dev/video* /dev/mpp_service /dev/rga /dev/mali0 2>/dev/null || true'
  section "MEDIA DEVICE PATHS" bash -lc 'for path in /dev/dri/card* /dev/dri/renderD* /dev/video* /dev/mpp_service /dev/rga /dev/mali0; do [ -e "$path" ] && printf "%s\\n" "$path"; done'
  section "GSTREAMER DECODERS" bash -lc "gst-inspect-1.0 2>/dev/null | grep -Ei 'mpp|rkvdec|v4l2.*dec|h264|h265|hevc' || true"
  section "FFMPEG DECODERS" bash -lc "ffmpeg -hide_banner -decoders 2>/dev/null | grep -Ei 'h264|hevc|rkmpp|v4l2' || true"
  section "SUDO NONINTERACTIVE" sudo -n true
} >"$text_path" 2>&1

PYTHONPATH=src python3 -m openframetap.doctor "$text_path" --json "$json_path"
status=$?
printf 'ARTIFACT_STEM=%s\n' "$stem"
printf 'DOCTOR_JSON=%s\n' "$json_path"
exit "$status"
