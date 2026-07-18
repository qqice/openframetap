#!/usr/bin/env bash
set -uo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"
seconds="${1:-60}"
[[ "$seconds" =~ ^[1-9][0-9]*$ ]] || { echo 'seconds must be a positive integer' >&2; exit 2; }

mkdir -p artifacts
stamp="$(date +%Y%m%d-%H%M%S)"
stem="ble-$stamp"
text_path="artifacts/$stem.txt"
json_path="artifacts/$stem.json"
snoop_path="artifacts/$stem.btsnoop"
BTMON_BIN="${OPENFRAMETAP_BTMON_BIN:-btmon}"
PYTHON_BIN="${OPENFRAMETAP_PYTHON_BIN:-$ROOT_DIR/.venv/bin/python}"
btmon_pid=""

: >"$text_path"
: >"$snoop_path"

stop_btmon() {
  if [[ -z "$btmon_pid" ]] || ! kill -0 "$btmon_pid" 2>/dev/null; then
    btmon_pid=""
    return 0
  fi
  kill -INT "$btmon_pid" 2>/dev/null || true
  for _ in $(seq 1 20); do
    kill -0 "$btmon_pid" 2>/dev/null || break
    sleep 0.1
  done
  if kill -0 "$btmon_pid" 2>/dev/null; then
    kill -TERM "$btmon_pid" 2>/dev/null || true
  fi
  wait "$btmon_pid" 2>/dev/null || true
  btmon_pid=""
}

cleanup() {
  local status=$?
  trap - INT TERM EXIT
  stop_btmon
  exit "$status"
}
trap cleanup INT TERM EXIT

printf 'btmon command: %q -w %q\n' "$BTMON_BIN" "$snoop_path" >>"$text_path"
"$BTMON_BIN" -w "$snoop_path" >>"$text_path" 2>&1 &
btmon_pid=$!

set +e
"$PYTHON_BIN" -m openframetap ble scan --seconds "$seconds" --json "$json_path" \
  2>&1 | tee -a "$text_path"
scanner_status=${PIPESTATUS[0]}
set -e

stop_btmon
trap - INT TERM EXIT
printf 'ARTIFACT_STEM=%s\n' "$stem"
exit "$scanner_status"
