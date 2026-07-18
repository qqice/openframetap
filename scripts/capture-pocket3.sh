#!/usr/bin/env bash
set -uo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"
operation="${1:-listen}"
address="${2:-}"
seconds="${3:-60}"
[[ "$operation" == "listen" || "$operation" == "telemetry" || "$operation" == "pair-status" ]] || {
  echo 'operation must be listen, telemetry, or pair-status' >&2
  exit 2
}
[[ -n "$address" ]] || { echo 'BLE address is required' >&2; exit 2; }
[[ "$seconds" =~ ^[1-9][0-9]*$ ]] || { echo 'seconds must be a positive integer' >&2; exit 2; }

stamp="$(date +%Y%m%d-%H%M%S)"
case "$operation" in
  listen) prefix="pocket3-listen" ;;
  telemetry) prefix="pocket3-telemetry" ;;
  pair-status) prefix="pocket3-pair-status" ;;
esac
stem="$prefix-$stamp"
output_dir="artifacts/$stem"
mkdir -p "$output_dir"
snoop_path="$output_dir/capture.btsnoop"
text_path="$output_dir/btmon.txt"
session_output="$output_dir/session-output.txt"
BTMON_BIN="${OPENFRAMETAP_BTMON_BIN:-btmon}"
PYTHON_BIN="${OPENFRAMETAP_PYTHON_BIN:-$ROOT_DIR/.venv/bin/python}"
BTMON_USE_SUDO="${OPENFRAMETAP_BTMON_USE_SUDO:-auto}"
btmon_pid=""
btmon_launcher_pid=""
btmon_pid_path="$output_dir/.btmon.pid"

if [[ "$BTMON_USE_SUDO" == "auto" ]]; then
  if command -v sudo >/dev/null 2>&1 && sudo -n true >/dev/null 2>&1; then
    BTMON_USE_SUDO=1
  else
    BTMON_USE_SUDO=0
  fi
fi

: >"$text_path"
: >"$snoop_path"

stop_btmon() {
  if [[ -z "$btmon_pid" ]]; then
    return 0
  fi
  if [[ "$BTMON_USE_SUDO" == "1" ]]; then
    if sudo -n kill -0 "$btmon_pid" 2>/dev/null; then
      sudo -n kill -INT "$btmon_pid" 2>/dev/null || true
    fi
  elif kill -0 "$btmon_pid" 2>/dev/null; then
    kill -INT "$btmon_pid" 2>/dev/null || true
  fi
  for _ in $(seq 1 20); do
    if [[ "$BTMON_USE_SUDO" == "1" ]]; then
      sudo -n kill -0 "$btmon_pid" 2>/dev/null || break
    else
      kill -0 "$btmon_pid" 2>/dev/null || break
    fi
    sleep 0.1
  done
  if [[ "$BTMON_USE_SUDO" == "1" ]]; then
    sudo -n kill -0 "$btmon_pid" 2>/dev/null && sudo -n kill -TERM "$btmon_pid" 2>/dev/null || true
  elif kill -0 "$btmon_pid" 2>/dev/null; then
    kill -TERM "$btmon_pid" 2>/dev/null || true
  fi
  [[ -n "$btmon_launcher_pid" ]] && wait "$btmon_launcher_pid" 2>/dev/null || true
  rm -f "$btmon_pid_path"
  btmon_pid=""
  btmon_launcher_pid=""
}

cleanup() {
  local status=$?
  trap - INT TERM EXIT
  stop_btmon
  exit "$status"
}
trap cleanup INT TERM EXIT

if [[ "$BTMON_USE_SUDO" == "1" ]]; then
  printf 'btmon command: sudo -n %q -w %q\n' "$BTMON_BIN" "$snoop_path" >>"$text_path"
  : >"$btmon_pid_path"
  sudo -n sh -c 'printf "%s\n" "$$" >"$1"; exec "$2" -w "$3"' \
    sh "$btmon_pid_path" "$BTMON_BIN" "$snoop_path" >>"$text_path" 2>&1 &
  btmon_launcher_pid=$!
  for _ in $(seq 1 20); do
    [[ -s "$btmon_pid_path" ]] && break
    sleep 0.1
  done
  btmon_pid="$(head -n 1 "$btmon_pid_path" 2>/dev/null || true)"
else
  printf 'btmon command: %q -w %q\n' "$BTMON_BIN" "$snoop_path" >>"$text_path"
  "$BTMON_BIN" -w "$snoop_path" >>"$text_path" 2>&1 &
  btmon_launcher_pid=$!
  btmon_pid=$btmon_launcher_pid
fi

set +e
case "$operation" in
  listen)
    "$PYTHON_BIN" -m openframetap ble listen "$address" --seconds "$seconds" --output-dir "$output_dir" \
      2>&1 | tee "$session_output"
    ;;
  telemetry)
    "$PYTHON_BIN" -m openframetap pocket3 telemetry "$address" --seconds "$seconds" --output-dir "$output_dir" \
      2>&1 | tee "$session_output"
    ;;
  pair-status)
    "$PYTHON_BIN" -m openframetap pocket3 pair status "$address" --seconds "$seconds" --output-dir "$output_dir" \
      2>&1 | tee "$session_output"
    ;;
esac
session_status=${PIPESTATUS[0]}
set -e

stop_btmon
trap - INT TERM EXIT
printf 'ARTIFACT_DIR=%s\n' "$stem"
exit "$session_status"
