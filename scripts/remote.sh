#!/usr/bin/env bash
set -uo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
LOCAL_ARTIFACTS="$ROOT_DIR/artifacts/local"
REMOTE_ARTIFACTS="$ROOT_DIR/artifacts/remote"
TARGET="${ROCK4D_SSH_HOST:-${OPENFRAMETAP_SSH_TARGET:-qqice@100.125.223.67}}"
REMOTE_DIR="${OPENFRAMETAP_REMOTE_DIR:-~/openframetap-runtime}"
SSH_BIN="${OPENFRAMETAP_SSH_BIN:-ssh}"
SCP_BIN="${OPENFRAMETAP_SCP_BIN:-scp}"
SSH_OPTIONS=(-o BatchMode=yes -o ConnectTimeout=15)

mkdir -p "$LOCAL_ARTIFACTS" "$REMOTE_ARTIFACTS"

timestamp() {
  date -u +%Y%m%dT%H%M%SZ
}

print_target() {
  printf '[openframetap] SSH target: %s\n' "$TARGET"
}

run_remote() {
  local label="$1"
  local command_text="$2"
  local stamp log_dir stdout_file stderr_file status
  stamp="$(timestamp)"
  log_dir="$LOCAL_ARTIFACTS/${label}-${stamp}"
  stdout_file="$log_dir/stdout.txt"
  stderr_file="$log_dir/stderr.txt"
  mkdir -p "$log_dir"

  print_target
  printf '[openframetap] Remote command: %s\n' "$command_text"
  printf '%s\n' "$command_text" >"$log_dir/command.txt"
  printf '%s\n' "$TARGET" >"$log_dir/target.txt"

  "$SSH_BIN" "${SSH_OPTIONS[@]}" "$TARGET" bash -s \
    > >(tee "$stdout_file") \
    2> >(tee "$stderr_file" >&2) <<<"$command_text"
  status=$?
  printf '%s\n' "$status" >"$log_dir/exit-status.txt"
  printf '[openframetap] Exit status: %s\n' "$status"
  printf '[openframetap] Local log: %s\n' "$log_dir"
  LAST_LOG_DIR="$log_dir"
  return "$status"
}

deploy() {
  print_target
  ROCK4D_SSH_HOST="$TARGET" \
    OPENFRAMETAP_REMOTE_DIR="$REMOTE_DIR" \
    OPENFRAMETAP_SSH_BIN="$SSH_BIN" \
    "$ROOT_DIR/scripts/deploy.sh"
}

pull_file() {
  local remote_relative="$1"
  local destination="$2"
  mkdir -p "$destination"
  printf '[openframetap] Pull: %s:%s/%s -> %s\n' \
    "$TARGET" "$REMOTE_DIR" "$remote_relative" "$destination"
  "$SCP_BIN" "${SSH_OPTIONS[@]}" \
    "$TARGET:${REMOTE_DIR#\~/}/$remote_relative" "$destination/"
}

pull_named_artifacts() {
  local stem="$1"
  shift
  local destination="$REMOTE_ARTIFACTS/$stem"
  local suffix
  mkdir -p "$destination"
  for suffix in "$@"; do
    pull_file "artifacts/${stem}.${suffix}" "$destination" || return $?
  done
  printf '[openframetap] Evidence archived: %s\n' "$destination"
}

extract_stem() {
  local marker="$1"
  sed -n "s/^${marker}=//p" "$LAST_LOG_DIR/stdout.txt" | tail -n 1
}

usage() {
  cat <<'EOF'
Usage:
  ./scripts/remote.sh command 'uname -a'
  ./scripts/remote.sh deploy
  ./scripts/remote.sh doctor
  ./scripts/remote.sh display-audit
  ./scripts/remote.sh ble-scan [seconds]
  ./scripts/remote.sh ble-probe <device-address>
  ./scripts/remote.sh setup-python
EOF
}

action="${1:-}"
case "$action" in
  command)
    [[ $# -eq 2 ]] || { usage >&2; exit 2; }
    run_remote command "$2"
    ;;
  deploy)
    deploy
    ;;
  doctor)
    deploy || exit $?
    run_remote doctor "set -eu
cd $REMOTE_DIR
bash scripts/audit-rock4d.sh"
    status=$?
    [[ $status -eq 0 ]] || exit "$status"
    stem="$(extract_stem ARTIFACT_STEM)"
    [[ -n "$stem" ]] || { echo '[openframetap] Missing doctor artifact marker' >&2; exit 3; }
    pull_named_artifacts "$stem" txt json
    ;;
  display-audit)
    deploy || exit $?
    run_remote display "set -eu
cd $REMOTE_DIR
bash scripts/collect-display-state.sh"
    status=$?
    [[ $status -eq 0 ]] || exit "$status"
    stem="$(extract_stem ARTIFACT_STEM)"
    [[ -n "$stem" ]] || { echo '[openframetap] Missing display artifact marker' >&2; exit 3; }
    pull_named_artifacts "$stem" txt json
    ;;
  setup-python)
    deploy || exit $?
    run_remote setup-python "set -eu
cd $REMOTE_DIR
python3 -m venv .venv
.venv/bin/python -m pip install -e ."
    ;;
  ble-scan)
    seconds="${2:-60}"
    [[ "$seconds" =~ ^[1-9][0-9]*$ ]] || { echo 'seconds must be a positive integer' >&2; exit 2; }
    deploy || exit $?
    run_remote ble-scan "set -eu
cd $REMOTE_DIR
bash scripts/capture-ble.sh $seconds"
    status=$?
    [[ $status -eq 0 ]] || exit "$status"
    stem="$(extract_stem ARTIFACT_STEM)"
    [[ -n "$stem" ]] || { echo '[openframetap] Missing BLE artifact marker' >&2; exit 3; }
    pull_named_artifacts "$stem" txt json btsnoop
    ;;
  ble-probe)
    [[ $# -eq 2 ]] || { usage >&2; exit 2; }
    address="$2"
    deploy || exit $?
    run_remote ble-probe "set -u
cd $REMOTE_DIR
.venv/bin/python -m openframetap ble probe '$address' --json artifacts/gatt-probe.json"
    status=$?
    stamp="$(timestamp)"
    destination="$REMOTE_ARTIFACTS/gatt-${stamp}"
    pull_file artifacts/gatt-probe.json "$destination" || true
    exit "$status"
    ;;
  *)
    usage >&2
    exit 2
    ;;
esac
