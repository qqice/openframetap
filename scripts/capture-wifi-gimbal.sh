#!/usr/bin/env bash
set -uo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"
mode="${1:-}"
address="${2:-}"
axis="${3:-}"
direction="${4:-}"
[[ "$mode" == "center-test" || "$mode" == "pulse" ]] || { echo 'mode must be center-test or pulse' >&2; exit 2; }
[[ -n "$address" ]] || { echo 'Pocket BLE address is required' >&2; exit 2; }
if [[ "$mode" == "pulse" ]]; then
  [[ "$axis" == "yaw" || "$axis" == "pitch" ]] || { echo 'pulse axis must be yaw or pitch' >&2; exit 2; }
  [[ "$direction" == "positive" || "$direction" == "negative" ]] || { echo 'pulse direction must be positive or negative' >&2; exit 2; }
fi
TCPDUMP_BIN="${OPENFRAMETAP_TCPDUMP_BIN:-tcpdump}"
BTMON_BIN="${OPENFRAMETAP_BTMON_BIN:-btmon}"
PYTHON_BIN="${OPENFRAMETAP_PYTHON_BIN:-$ROOT_DIR/.venv/bin/python}"
SUDO_BIN="${OPENFRAMETAP_SUDO_BIN:-sudo}"
command -v "$TCPDUMP_BIN" >/dev/null 2>&1 || {
  echo 'tcpdump is required for restricted UDP evidence capture; no control packet was sent' >&2
  exit 3
}
command -v "$BTMON_BIN" >/dev/null 2>&1 || {
  echo 'btmon is required for BLE evidence capture; no control packet was sent' >&2
  exit 3
}
"$SUDO_BIN" -n true >/dev/null 2>&1 || {
  echo 'passwordless sudo is required only for btmon/tcpdump capture; no control packet was sent' >&2
  exit 3
}

if [[ "${OPENFRAMETAP_TEST_MODE:-0}" == "1" && -n "${OPENFRAMETAP_TEST_TARGET_IP:-}" ]]; then
  target_ip="$OPENFRAMETAP_TEST_TARGET_IP"
else
target_ip="$($PYTHON_BIN - <<'PY'
from openframetap.transport.dji_wifi_udp import discover_rtmp_publisher_ip
print(discover_rtmp_publisher_ip())
PY
)" || exit $?
fi
[[ -n "$target_ip" ]] || { echo 'current Pocket RTMP publisher IP is unavailable' >&2; exit 3; }

stamp="$(date +%Y%m%d-%H%M%S)"
stem="wifi-gimbal-test-$stamp"
output_dir="artifacts/private/$stem"
mkdir -p "$output_dir"
chmod 700 "$output_dir"
: >"$output_dir/btmon.txt"
: >"$output_dir/session-output.txt"
: >"$output_dir/capture.btsnoop"
: >"$output_dir/udp-capture.pcap"
: >"$output_dir/tcpdump.txt"

btmon_pid=""
tcpdump_pid=""
btmon_pid_file="$output_dir/.btmon.pid"
tcpdump_pid_file="$output_dir/.tcpdump.pid"

stop_capture_pid() {
  local pid="$1"
  [[ -n "$pid" ]] || return 0
  if "$SUDO_BIN" -n kill -0 "$pid" 2>/dev/null; then
    "$SUDO_BIN" -n kill -INT "$pid" 2>/dev/null || true
    for _ in $(seq 1 30); do
      "$SUDO_BIN" -n kill -0 "$pid" 2>/dev/null || return 0
      sleep 0.1
    done
    "$SUDO_BIN" -n kill -TERM "$pid" 2>/dev/null || true
  fi
}

finalize() {
  local status=$?
  trap - INT TERM EXIT
  stop_capture_pid "$tcpdump_pid"
  stop_capture_pid "$btmon_pid"
  rm -f "$btmon_pid_file" "$tcpdump_pid_file"
  : >"$output_dir/checksums.sha256"
  local name
  for name in config.json udp-capture.pcap tcpdump.txt capture.btsnoop btmon.txt sent-datagrams.jsonl sent-duml.jsonl udp-received.jsonl notifications.jsonl telemetry.jsonl state-transitions.jsonl media-status.json summary.json session-output.txt; do
    [[ -f "$output_dir/$name" ]] && (cd "$output_dir" && sha256sum "$name") >>"$output_dir/checksums.sha256"
  done
  printf 'ARTIFACT_DIR=private/%s\n' "$stem"
  exit "$status"
}
trap finalize INT TERM EXIT
# The bounded workflow must finish its redundant center sequence even if the
# SSH parent disappears.  No control loop is unbounded, and output is written
# directly to the artifact rather than through a pipe that can break on logout.
trap '' HUP

printf 'btmon command: sudo -n btmon -w capture.btsnoop\n' >>"$output_dir/btmon.txt"
"$SUDO_BIN" -n sh -c 'printf "%s\n" "$$" >"$1"; exec "$2" -w "$3"' \
  sh "$btmon_pid_file" "$BTMON_BIN" "$output_dir/capture.btsnoop" >>"$output_dir/btmon.txt" 2>&1 &
for _ in $(seq 1 20); do [[ -s "$btmon_pid_file" ]] && break; sleep 0.1; done
btmon_pid="$(head -n 1 "$btmon_pid_file" 2>/dev/null || true)"

"$SUDO_BIN" -n sh -c 'printf "%s\n" "$$" >"$1"; exec "$2" -i any -U -n -w "$3" "udp and host $4 and port 9004"' \
  sh "$tcpdump_pid_file" "$TCPDUMP_BIN" "$output_dir/udp-capture.pcap" "$target_ip" \
  >>"$output_dir/tcpdump.txt" 2>&1 &
for _ in $(seq 1 20); do [[ -s "$tcpdump_pid_file" ]] && break; sleep 0.1; done
tcpdump_pid="$(head -n 1 "$tcpdump_pid_file" 2>/dev/null || true)"
sleep 0.2

args=(
  -m openframetap pocket3 wifi-gimbal "$mode"
  --address "$address" --local-port 54232 --mimo-closed
  --output-dir "$output_dir"
)
if [[ "$mode" == "pulse" ]]; then
  args+=(--axis "$axis" --direction "$direction" --offset 16 --frames 2 --rate-hz 10)
fi
set +e
OPENFRAMETAP_WIFI_GIMBAL_TEST=1 \
OPENFRAMETAP_GIT_HEAD="${OPENFRAMETAP_GIT_HEAD:-unknown}" \
  "$PYTHON_BIN" "${args[@]}" >"$output_dir/session-output.txt" 2>&1
status=$?
cat "$output_dir/session-output.txt"
set -e
exit "$status"
