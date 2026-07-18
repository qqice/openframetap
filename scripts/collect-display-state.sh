#!/usr/bin/env bash
set -uo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"
mkdir -p artifacts
stamp="$(date +%Y%m%d-%H%M%S)"
stem="display-$stamp"
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
  section "DRI DEVICES" bash -lc 'ls -l /dev/dri 2>&1 || true'
  section "DRI DEVICE PATHS" bash -lc 'for path in /dev/dri/card* /dev/dri/renderD*; do [ -e "$path" ] && printf "%s\\n" "$path"; done'
  section "DRM CONNECTORS" bash -lc 'for path in /sys/class/drm/card*-*; do [ -f "$path/status" ] || continue; printf "CONNECTOR=%s\\n" "${path##*/}"; printf "STATUS="; cat "$path/status" 2>/dev/null || true; printf "ENABLED="; cat "$path/enabled" 2>/dev/null || true; while IFS= read -r mode; do [ -n "$mode" ] && printf "MODE=%s\\n" "$mode"; done <"$path/modes"; done'
  section "DRM STATUS" bash -lc 'cat /sys/class/drm/*/status 2>/dev/null || true'
  section "DRM MODES" bash -lc 'cat /sys/class/drm/*/modes 2>/dev/null || true'
  section "DRM ENABLED" bash -lc 'for f in /sys/class/drm/*/enabled; do echo "===== $f ====="; cat "$f"; done 2>/dev/null || true'
  section "MODETEST CONNECTORS" bash -lc 'modetest -c 2>/dev/null || true'
  section "MODETEST PLANES" bash -lc 'modetest -p 2>/dev/null || true'
  section FRAMEBUFFER bash -lc 'fbset -s 2>/dev/null || true'
  section "LOGIN SESSIONS" loginctl list-sessions --no-legend
  section "SESSION DETAILS" bash -lc 'loginctl list-sessions --no-legend 2>/dev/null | while read -r id rest; do [ -n "$id" ] || continue; printf "SESSION=%s\\n" "$id"; loginctl show-session "$id" -p Name -p Type -p Class -p State -p Display -p Remote -p Leader; done'
  section "DISPLAY PROCESSES" bash -lc "ps -ef | grep -Ei 'weston|wayfire|kwin|gnome-shell|Xorg|Xwayland|sway|labwc' | grep -v grep || true"
  section "SSH DISPLAY ENV" bash -lc 'printf "XDG_SESSION_TYPE=%s\\nWAYLAND_DISPLAY=%s\\nDISPLAY=%s\\n" "${XDG_SESSION_TYPE:-}" "${WAYLAND_DISPLAY:-}" "${DISPLAY:-}"'
  section LSINPUT bash -lc 'lsinput 2>/dev/null || true'
  section LIBINPUT bash -lc 'libinput list-devices 2>/dev/null || true'
  section "PROC INPUT DEVICES" cat /proc/bus/input/devices
  section "INPUT LINKS" bash -lc 'ls -l /dev/input/by-path /dev/input/by-id 2>/dev/null || true'
  section "INPUT IOCTL PROBE" bash -lc 'PYTHONPATH=src python3 -m openframetap.display_probe'
  section "DISPLAY AND INPUT CONFIG" bash -lc 'for f in "$HOME/.config/monitors.xml" "$HOME/.config/weston.ini" "$HOME/.config/labwc/rc.xml" /etc/xdg/weston/weston.ini; do if [ -f "$f" ]; then printf "%s\\n" "--- $f ---"; cat "$f"; fi; done'
  section "DISPLAY DMESG" bash -lc "dmesg 2>&1 | grep -Ei 'dsi|mipi|panel|drm|display|touch|goodix|gt9|waveshare' || true"
} >"$text_path" 2>&1

PYTHONPATH=src python3 -m openframetap.doctor display "$text_path" --json "$json_path"
status=$?
printf 'ARTIFACT_STEM=%s\n' "$stem"
printf 'DISPLAY_JSON=%s\n' "$json_path"
exit "$status"
