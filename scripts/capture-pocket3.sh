#!/usr/bin/env bash
set -uo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"
operation="${1:-listen}"
address="${2:-}"
seconds="${3:-60}"
frame_hex="${4:-}"
command_name="${5:-}"
confirmed_sha256="${6:-}"
required_incoming_hex="${7:-}"
proposal_path="${4:-}"
workflow_state_path="${5:-}"
[[ "$operation" == "listen" || "$operation" == "telemetry" || "$operation" == "pair-status" || "$operation" == "manual-frame" || "$operation" == "manual-pair-session" || "$operation" == "experiment" || "$operation" == "rtmp-proposal" ]] || {
  echo 'unsupported capture operation' >&2
  exit 2
}
[[ -n "$address" ]] || { echo 'BLE address is required' >&2; exit 2; }
[[ "$seconds" =~ ^[1-9][0-9]*$ ]] || { echo 'seconds must be a positive integer' >&2; exit 2; }

stamp="$(date +%Y%m%d-%H%M%S)"
case "$operation" in
  listen) prefix="pocket3-listen" ;;
  telemetry) prefix="pocket3-telemetry" ;;
  pair-status) prefix="pocket3-pair-status" ;;
  manual-frame) prefix="pocket3-manual-frame" ;;
  manual-pair-session) prefix="pocket3-manual-pair-session" ;;
  experiment) prefix="pocket3-experiment" ;;
  rtmp-proposal) prefix="pocket3-rtmp-prepare" ;;
esac
stem="$prefix-$stamp"
if [[ "$operation" == "rtmp-proposal" ]]; then
  artifact_relative="private/$stem"
else
  artifact_relative="$stem"
fi
output_dir="artifacts/$artifact_relative"
mkdir -p "$output_dir"
[[ "$operation" == "rtmp-proposal" ]] && chmod 700 "$output_dir"
snoop_path="$output_dir/capture.btsnoop"
text_path="$output_dir/btmon.txt"
session_output="$output_dir/session-output.txt"
BTMON_BIN="${OPENFRAMETAP_BTMON_BIN:-btmon}"
PYTHON_BIN="${OPENFRAMETAP_PYTHON_BIN:-$ROOT_DIR/.venv/bin/python}"
BTMON_USE_SUDO="${OPENFRAMETAP_BTMON_USE_SUDO:-auto}"
btmon_pid=""
btmon_launcher_pid=""
btmon_pid_path="$output_dir/.btmon.pid"
artifact_marker_printed=0

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

finalize_checksums() {
  if [[ "$operation" != "experiment" && "$operation" != "rtmp-proposal" ]]; then
    return 0
  fi
  local names=(capture.btsnoop btmon.txt notifications.jsonl duml-frames.jsonl events.jsonl)
  if [[ "$operation" == "rtmp-proposal" ]]; then
    names+=(summary.json transmission.json session-output.txt)
  fi
  local name
  : >"$output_dir/checksums.sha256"
  for name in "${names[@]}"; do
    if [[ -f "$output_dir/$name" ]]; then
      (cd "$output_dir" && sha256sum "$name") >>"$output_dir/checksums.sha256"
    fi
  done
}

emit_artifact_marker() {
  if [[ "$artifact_marker_printed" == "0" ]]; then
    printf 'ARTIFACT_DIR=%s\n' "$artifact_relative"
    artifact_marker_printed=1
  fi
}

cleanup() {
  local status=$?
  trap - INT TERM EXIT
  stop_btmon
  finalize_checksums
  emit_artifact_marker
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
  manual-frame)
    [[ -n "$frame_hex" && -n "$command_name" && -n "$confirmed_sha256" ]] || {
      echo 'manual-frame requires hex, command name, and confirmed SHA-256' >&2
      exit 2
    }
    manual_args=(
      -m openframetap ble manual-write "$address"
      --hex "$frame_hex" --command "$command_name" --confirmed-sha256 "$confirmed_sha256"
      --seconds "$seconds" --output-dir "$output_dir"
    )
    if [[ -n "$required_incoming_hex" ]]; then
      manual_args+=(--require-incoming-hex "$required_incoming_hex")
    fi
    OPENFRAMETAP_USER_INITIATED=1 "$PYTHON_BIN" "${manual_args[@]}" \
      2>&1 | tee "$session_output"
    ;;
  manual-pair-session)
    OPENFRAMETAP_USER_INITIATED=1 "$PYTHON_BIN" -m openframetap pocket3 pair manual-session \
      "$address" --telemetry-seconds "$seconds" --output-dir "$output_dir" \
      2>&1 | tee "$session_output"
    ;;
  experiment)
    [[ -t 0 || "${OPENFRAMETAP_TEST_MODE:-0}" == "1" ]] || {
      echo 'experiment requires an interactive TTY' >&2
      exit 4
    }
    # The interactive Python child must receive Ctrl+C and finish its async
    # disconnect/finalize path.  The wrapper ignores SIGINT only while waiting
    # for that child; the subshell restores SIGINT before exec.  This prevents
    # the outer cleanup trap from exiting before session.json is flushed.
    trap '' INT
    (
      trap - INT
      exec "$PYTHON_BIN" -m openframetap pocket3 experiment \
        --address "$address" --duration "$seconds" --output "$output_dir"
    ) 2>&1 | tee "$session_output"
    experiment_status=${PIPESTATUS[0]}
    trap cleanup INT TERM EXIT
    exit "$experiment_status"
    ;;
  rtmp-proposal)
    [[ -t 0 || "${OPENFRAMETAP_TEST_MODE:-0}" == "1" ]] || {
      echo 'rtmp-proposal requires an interactive TTY' >&2
      exit 4
    }
    [[ -f "$proposal_path" && -f "$workflow_state_path" ]] || {
      echo 'fixed proposal or workflow state file is missing' >&2
      exit 2
    }
    OPENFRAMETAP_USER_INITIATED=1 "$PYTHON_BIN" -m openframetap pocket3 rtmp send-approved \
      "$proposal_path" --address "$address" --seconds "$seconds" \
      --output-dir "$output_dir" --state-file "$workflow_state_path" \
      2>&1 | tee "$session_output"
    ;;
esac
session_status=${PIPESTATUS[0]}
set -e

stop_btmon
finalize_checksums
trap - INT TERM EXIT
emit_artifact_marker
exit "$session_status"
