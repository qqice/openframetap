#!/usr/bin/env bash
set -uo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
LOCAL_ARTIFACTS="$ROOT_DIR/artifacts/local"
REMOTE_ARTIFACTS="$ROOT_DIR/artifacts/remote"
TARGET="${ROCK4D_SSH_HOST:-${OPENFRAMETAP_SSH_TARGET:-qqice@100.125.223.67}}"
REMOTE_DIR="${OPENFRAMETAP_REMOTE_DIR:-~/openframetap-runtime}"
POCKET3_ADDRESS="${POCKET3_BLE_ADDRESS:-E4:7A:2C:36:DC:FC}"
MEDIAMTX_VERSION="v1.18.2"
MEDIAMTX_SHA256="c78aa7a1bdab94b2b02be364661f17802143215dba37e1fa67c3e0849248b485"
MEDIAMTX_CACHE="${OPENFRAMETAP_MEDIAMTX_CACHE:-$ROOT_DIR/artifacts/private/tool-cache/mediamtx/$MEDIAMTX_VERSION}"
LOCAL_PYTHON_BIN="${OPENFRAMETAP_LOCAL_PYTHON_BIN:-$ROOT_DIR/.venv/Scripts/python.exe}"
LOCAL_FFMPEG_BIN="${OPENFRAMETAP_LOCAL_FFMPEG_BIN:-/c/ffmpeg/bin/ffmpeg.exe}"
LOCAL_FFPROBE_BIN="${OPENFRAMETAP_LOCAL_FFPROBE_BIN:-/c/ffmpeg/bin/ffprobe.exe}"
APPROVED_PREPARE_SHA256="e0286b3d9e63e248f0792c8ae4055587484aba8a05ba154c451c8ae987cc2ad6"
APPROVED_PREPARE_DIR="$ROOT_DIR/artifacts/private/proposals/prepare-20260718T163611Z"
APPROVED_WIFI_SHA256="8751117a3022d1a0057a4d6ea1ef38505314d1d75995fd4d46fc8d85e25ed72e"
APPROVED_WIFI_DIR="$ROOT_DIR/artifacts/private/proposals/wifi-20260718T173350Z"
RTMP_WORKFLOW_STATE="${OPENFRAMETAP_RTMP_WORKFLOW_STATE:-$ROOT_DIR/artifacts/private/pocket3-rtmp-workflow.json}"
PREPARE_RESULT="${OPENFRAMETAP_PREPARE_RESULT:-$ROOT_DIR/artifacts/sanitized/pocket3-rtmp-prepare-20260719-010705/prepare-result.json}"
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

run_remote_interactive() {
  local label="$1"
  local command_text="$2"
  local stamp log_dir stdout_file stderr_file status
  stamp="$(timestamp)"
  log_dir="$LOCAL_ARTIFACTS/${label}-${stamp}"
  stdout_file="$log_dir/stdout.txt"
  stderr_file="$log_dir/stderr.txt"
  mkdir -p "$log_dir"
  print_target
  printf '[openframetap] Interactive remote command: %s\n' "$command_text"
  printf '%s\n' "$command_text" >"$log_dir/command.txt"
  printf '%s\n' "$TARGET" >"$log_dir/target.txt"
  "$SSH_BIN" "${SSH_OPTIONS[@]}" -tt "$TARGET" "$command_text" \
    > >(tee "$stdout_file") \
    2> >(tee "$stderr_file" >&2)
  status=$?
  printf '%s\n' "$status" >"$log_dir/exit-status.txt"
  printf '[openframetap] Exit status: %s\n' "$status"
  printf '[openframetap] Local log: %s\n' "$log_dir"
  LAST_LOG_DIR="$log_dir"
  return "$status"
}

deploy() {
  local stamp log_dir status
  stamp="$(timestamp)"
  log_dir="$LOCAL_ARTIFACTS/deploy-$stamp"
  mkdir -p "$log_dir"
  print_target
  printf '[openframetap] Local deploy command: %s\n' "$ROOT_DIR/scripts/deploy.sh"
  ROCK4D_SSH_HOST="$TARGET" \
    OPENFRAMETAP_REMOTE_DIR="$REMOTE_DIR" \
    OPENFRAMETAP_SSH_BIN="$SSH_BIN" \
    "$ROOT_DIR/scripts/deploy.sh" \
      > >(tee "$log_dir/stdout.txt") \
      2> >(tee "$log_dir/stderr.txt" >&2)
  status=$?
  printf '%s\n' "$status" >"$log_dir/exit-status.txt"
  printf '[openframetap] Deploy wrapper exit status: %s\n' "$status"
  printf '[openframetap] Local deploy log: %s\n' "$log_dir"
  return "$status"
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

pull_dir() {
  local remote_relative="$1"
  local destination="$2"
  mkdir -p "$destination"
  printf '[openframetap] Pull directory: %s:%s/%s -> %s\n' \
    "$TARGET" "$REMOTE_DIR" "$remote_relative" "$destination"
  "$SCP_BIN" "${SSH_OPTIONS[@]}" -r \
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
  sed -n "s/.*${marker}=//p" "$LAST_LOG_DIR/stdout.txt" | tail -n 1
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
  ./scripts/remote.sh ble-listen [seconds]
  ./scripts/remote.sh pocket3-pair-status
  ./scripts/remote.sh pocket3-pair
  ./scripts/remote.sh pocket3-telemetry [seconds]
  ./scripts/remote.sh pocket3-experiment [seconds]
  ./scripts/remote.sh analyze-telemetry <pocket3-experiment-directory>
  ./scripts/remote.sh rtmp-install
  ./scripts/remote.sh rtmp-doctor
  ./scripts/remote.sh rtmp-start
  ./scripts/remote.sh rtmp-status
  ./scripts/remote.sh rtmp-stop
  ./scripts/remote.sh rtmp-self-test
  ./scripts/remote.sh pocket3-rtmp-send-approved-prepare
  ./scripts/remote.sh pocket3-rtmp-send-approved-wifi
  ./scripts/remote.sh pocket3-rtmp-configure-wifi-secrets
  ./scripts/remote.sh pocket3-rtmp-propose-wifi
  ./scripts/remote.sh pocket3-send-frame <frame.bin> <command-name> [listen-seconds] [required-incoming.bin]
  ./scripts/remote.sh pocket3-manual-pair-session [telemetry-seconds]
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
  rtmp-install)
    binary="$MEDIAMTX_CACHE/mediamtx"
    archive="$MEDIAMTX_CACHE/mediamtx_${MEDIAMTX_VERSION}_linux_arm64.tar.gz"
    [[ -f "$binary" && -f "$archive" ]] || {
      echo "MediaMTX cache missing under: $MEDIAMTX_CACHE" >&2
      exit 2
    }
    actual_sha256="$(sha256sum "$archive" | awk '{print tolower($1)}')"
    [[ "$actual_sha256" == "$MEDIAMTX_SHA256" ]] || {
      echo 'MediaMTX archive SHA-256 mismatch; refusing installation.' >&2
      exit 5
    }
    deploy || exit $?
    run_remote rtmp-install-prepare "set -eu
mkdir -p $REMOTE_DIR/runtime/rtmp/bin
chmod 700 $REMOTE_DIR/runtime $REMOTE_DIR/runtime/rtmp $REMOTE_DIR/runtime/rtmp/bin"
    status=$?
    [[ $status -eq 0 ]] || exit "$status"
    printf '[openframetap] Copy verified MediaMTX %s binary to user runtime\n' "$MEDIAMTX_VERSION"
    "$SCP_BIN" "${SSH_OPTIONS[@]}" "$binary" \
      "$TARGET:${REMOTE_DIR#\~/}/runtime/rtmp/bin/mediamtx.new" || exit $?
    run_remote rtmp-install-finalize "set -eu
cd $REMOTE_DIR
actual=\$(sha256sum runtime/rtmp/bin/mediamtx.new | awk '{print \$1}')
printf 'MEDIAMTX_BINARY_SHA256=%s\\n' \"\$actual\"
mv runtime/rtmp/bin/mediamtx.new runtime/rtmp/bin/mediamtx
chmod 700 runtime/rtmp/bin/mediamtx
runtime/rtmp/bin/mediamtx --version"
    ;;
  rtmp-doctor)
    deploy || exit $?
    run_remote rtmp-doctor "set -eu
cd $REMOTE_DIR
.venv/bin/python -m openframetap video server doctor"
    ;;
  rtmp-start)
    deploy || exit $?
    run_remote rtmp-start "set -eu
cd $REMOTE_DIR
.venv/bin/python -m openframetap video server start"
    ;;
  rtmp-status)
    deploy || exit $?
    run_remote rtmp-status "set -eu
cd $REMOTE_DIR
.venv/bin/python -m openframetap video server status"
    ;;
  rtmp-stop)
    deploy || exit $?
    run_remote rtmp-stop "set -eu
cd $REMOTE_DIR
    .venv/bin/python -m openframetap video server stop"
    ;;
  rtmp-self-test)
    [[ $# -eq 1 ]] || { usage >&2; exit 2; }
    [[ -x "$LOCAL_PYTHON_BIN" && -x "$LOCAL_FFMPEG_BIN" && -x "$LOCAL_FFPROBE_BIN" ]] || {
      echo 'Local Python/FFmpeg/FFprobe tools required for LAN self-test are unavailable.' >&2
      exit 2
    }
    deploy || exit $?
    run_remote rtmp-selftest-address "set -eu
cd $REMOTE_DIR
ip=\$(ip -4 -o addr show dev wlan0 scope global | awk 'NR==1 {split(\$4,a,\"/\"); print a[1]}')
case \"\$ip\" in 10.*|192.168.*|172.1[6-9].*|172.2[0-9].*|172.3[01].*) ;; *) exit 6 ;; esac
printf 'WLAN_IP=%s\\n' \"\$ip\""
    status=$?
    [[ $status -eq 0 ]] || exit "$status"
    wlan_ip="$(extract_stem WLAN_IP | tr -d '\r')"
    [[ -n "$wlan_ip" ]] || { echo 'Missing sanitized wlan0 IP marker' >&2; exit 3; }
    stamp="$(timestamp)"
    key="openframetap-selftest-${stamp}"
    private_dir="$ROOT_DIR/artifacts/private/rtmp-selftest-${stamp}"
    sanitized_dir="$ROOT_DIR/artifacts/sanitized/rtmp-selftest-${stamp}"
    mkdir -p "$private_dir" "$sanitized_dir"
    server_started=0
    selftest_cleanup() {
      if [[ "$server_started" -eq 1 ]]; then
        run_remote rtmp-selftest-stop "set -u
cd $REMOTE_DIR
.venv/bin/python -m openframetap video server stop" || true
        server_started=0
      fi
    }
    trap selftest_cleanup EXIT INT TERM
    run_remote rtmp-selftest-start "set -eu
cd $REMOTE_DIR
.venv/bin/python -m openframetap video server start"
    status=$?
    [[ $status -eq 0 ]] || exit "$status"
    server_started=1
    "$LOCAL_PYTHON_BIN" -m openframetap video self-test \
      --url "rtmp://$wlan_ip:1935/live/$key" \
      --private-output "$private_dir" \
      --sanitized-output "$sanitized_dir" \
      --ffmpeg-bin "$LOCAL_FFMPEG_BIN" \
      --ffprobe-bin "$LOCAL_FFPROBE_BIN"
    status=$?
    selftest_cleanup
    trap - EXIT INT TERM
    pull_file runtime/rtmp/server.log "$private_dir" || true
    if [[ -f "$private_dir/server.log" ]]; then
      (cd "$private_dir" && sha256sum server.log >>checksums.sha256)
    fi
    printf '[openframetap] PRIVATE_ARTIFACT_DIR=%s\n' "$private_dir"
    printf '[openframetap] SANITIZED_ARTIFACT_DIR=%s\n' "$sanitized_dir"
    exit "$status"
    ;;
  pocket3-rtmp-send-approved-prepare)
    [[ $# -eq 1 ]] || { usage >&2; exit 2; }
    [[ -t 0 ]] || {
      echo '[openframetap] Refused: approved prepare send requires the device owner at a real terminal.' >&2
      exit 4
    }
    proposal_json="$APPROVED_PREPARE_DIR/proposal-private.json"
    proposal_bin="$APPROVED_PREPARE_DIR/proposal.bin"
    [[ -f "$proposal_json" && -f "$proposal_bin" && -f "$RTMP_WORKFLOW_STATE" ]] || {
      echo '[openframetap] Fixed approved proposal or workflow state is missing.' >&2
      exit 2
    }
    grep -Eq '"phase"[[:space:]]*:[[:space:]]*"prepare_proposed"' "$RTMP_WORKFLOW_STATE" || {
      echo '[openframetap] Refused: the approved prepare proposal is already consumed or no longer pending.' >&2
      exit 4
    }
    actual_sha256="$(sha256sum "$proposal_bin" | awk '{print tolower($1)}')"
    [[ "$actual_sha256" == "$APPROVED_PREPARE_SHA256" ]] || {
      echo '[openframetap] Approved prepare proposal SHA-256 mismatch.' >&2
      exit 5
    }
    grep -Fq "\"frame_sha256\": \"$APPROVED_PREPARE_SHA256\"" "$proposal_json" || {
      echo '[openframetap] Private proposal JSON does not name the approved SHA-256.' >&2
      exit 5
    }
    printf '%s\n' '[openframetap] APPROVED SINGLE COMMAND: prepare_to_live_stream (02/E1)'
    printf '[openframetap] Fixed frame SHA-256: %s\n' "$APPROVED_PREPARE_SHA256"
    printf '%s\n' '[openframetap] No Wi-Fi, stream-config, 02/8E, stop, or follow-up command is authorized.'
    deploy || exit $?
    run_remote rtmp-prepare-stage "set -eu
cd $REMOTE_DIR
mkdir -p artifacts/private/approved-prepare
chmod 700 artifacts/private artifacts/private/approved-prepare"
    status=$?
    [[ $status -eq 0 ]] || exit "$status"
    "$SCP_BIN" "${SSH_OPTIONS[@]}" "$proposal_json" \
      "$TARGET:${REMOTE_DIR#\~/}/artifacts/private/approved-prepare/proposal-private.json.new" || exit $?
    "$SCP_BIN" "${SSH_OPTIONS[@]}" "$RTMP_WORKFLOW_STATE" \
      "$TARGET:${REMOTE_DIR#\~/}/artifacts/private/pocket3-rtmp-workflow.json.new" || exit $?
    run_remote rtmp-prepare-finalize "set -eu
cd $REMOTE_DIR
mv artifacts/private/approved-prepare/proposal-private.json.new artifacts/private/approved-prepare/proposal-private.json
mv artifacts/private/pocket3-rtmp-workflow.json.new artifacts/private/pocket3-rtmp-workflow.json
chmod 600 artifacts/private/approved-prepare/proposal-private.json artifacts/private/pocket3-rtmp-workflow.json"
    status=$?
    [[ $status -eq 0 ]] || exit "$status"
    run_remote_interactive rtmp-prepare-send \
      "cd $REMOTE_DIR && bash scripts/capture-pocket3.sh rtmp-proposal '$POCKET3_ADDRESS' 15 'artifacts/private/approved-prepare/proposal-private.json' 'artifacts/private/pocket3-rtmp-workflow.json'"
    status=$?
    stem="$(extract_stem ARTIFACT_DIR | tr -d '\r')"
    [[ -n "$stem" ]] || { echo '[openframetap] Missing private RTMP prepare artifact marker' >&2; exit 3; }
    pull_dir "artifacts/$stem" "$ROOT_DIR/artifacts/private" || exit $?
    pull_file artifacts/private/pocket3-rtmp-workflow.json "$ROOT_DIR/artifacts/private" || true
    exit "$status"
    ;;
  pocket3-rtmp-send-approved-wifi)
    [[ $# -eq 1 ]] || { usage >&2; exit 2; }
    [[ -t 0 ]] || {
      echo '[openframetap] Refused: approved Wi-Fi send requires the device owner at a real terminal.' >&2
      exit 4
    }
    proposal_json="$APPROVED_WIFI_DIR/proposal-private.json"
    proposal_bin="$APPROVED_WIFI_DIR/proposal.bin"
    [[ -f "$proposal_json" && -f "$proposal_bin" && -f "$RTMP_WORKFLOW_STATE" ]] || {
      echo '[openframetap] Fixed approved Wi-Fi proposal or workflow state is missing.' >&2
      exit 2
    }
    grep -Eq '"phase"[[:space:]]*:[[:space:]]*"wifi_proposed"' "$RTMP_WORKFLOW_STATE" || {
      echo '[openframetap] Refused: the approved Wi-Fi proposal is already consumed or no longer pending.' >&2
      exit 4
    }
    actual_sha256="$(sha256sum "$proposal_bin" | awk '{print tolower($1)}')"
    [[ "$actual_sha256" == "$APPROVED_WIFI_SHA256" ]] || {
      echo '[openframetap] Approved Wi-Fi proposal SHA-256 mismatch.' >&2
      exit 5
    }
    grep -Fq "\"frame_sha256\": \"$APPROVED_WIFI_SHA256\"" "$proposal_json" || {
      echo '[openframetap] Private Wi-Fi proposal JSON does not name the approved SHA-256.' >&2
      exit 5
    }
    grep -Fq '"command": "wifi_connect"' "$proposal_json" || {
      echo '[openframetap] Private proposal is not the approved wifi_connect command.' >&2
      exit 5
    }
    printf '%s\n' '[openframetap] APPROVED SINGLE COMMAND: wifi_connect (07/47)'
    printf '[openframetap] Fixed private frame SHA-256: %s\n' "$APPROVED_WIFI_SHA256"
    printf '%s\n' '[openframetap] No RTMP configuration, 02/8E, start, stop, retry, or follow-up command is authorized.'
    deploy || exit $?
    run_remote rtmp-wifi-send-stage "set -eu
cd $REMOTE_DIR
mkdir -p artifacts/private/approved-wifi
chmod 700 artifacts/private artifacts/private/approved-wifi"
    status=$?
    [[ $status -eq 0 ]] || exit "$status"
    "$SCP_BIN" "${SSH_OPTIONS[@]}" "$proposal_json" \
      "$TARGET:${REMOTE_DIR#\~/}/artifacts/private/approved-wifi/proposal-private.json.new" || exit $?
    "$SCP_BIN" "${SSH_OPTIONS[@]}" "$RTMP_WORKFLOW_STATE" \
      "$TARGET:${REMOTE_DIR#\~/}/artifacts/private/pocket3-rtmp-workflow.json.new" || exit $?
    run_remote rtmp-wifi-send-finalize "set -eu
cd $REMOTE_DIR
mv artifacts/private/approved-wifi/proposal-private.json.new artifacts/private/approved-wifi/proposal-private.json
mv artifacts/private/pocket3-rtmp-workflow.json.new artifacts/private/pocket3-rtmp-workflow.json
chmod 600 artifacts/private/approved-wifi/proposal-private.json artifacts/private/pocket3-rtmp-workflow.json"
    status=$?
    [[ $status -eq 0 ]] || exit "$status"
    run_remote_interactive rtmp-wifi-send \
      "cd $REMOTE_DIR && bash scripts/capture-pocket3.sh rtmp-wifi-proposal '$POCKET3_ADDRESS' 30 'artifacts/private/approved-wifi/proposal-private.json' 'artifacts/private/pocket3-rtmp-workflow.json'"
    status=$?
    stem="$(extract_stem ARTIFACT_DIR | tr -d '\r')"
    [[ "$stem" == private/pocket3-rtmp-wifi-* ]] || {
      echo '[openframetap] Missing private Wi-Fi send artifact marker' >&2
      exit 3
    }
    pull_dir "artifacts/$stem" "$ROOT_DIR/artifacts/private" || exit $?
    pull_file artifacts/private/pocket3-rtmp-workflow.json "$ROOT_DIR/artifacts/private" || true
    exit "$status"
    ;;
  pocket3-rtmp-configure-wifi-secrets)
    [[ $# -eq 1 ]] || { usage >&2; exit 2; }
    [[ -t 0 ]] || {
      echo '[openframetap] Refused: Wi-Fi secret setup requires the device owner at a real terminal.' >&2
      exit 4
    }
    deploy || exit $?
    run_remote_interactive rtmp-wifi-secret-setup \
      "cd $REMOTE_DIR && OPENFRAMETAP_USER_INITIATED=1 .venv/bin/python -m openframetap secrets configure-wifi"
    ;;
  pocket3-rtmp-propose-wifi)
    [[ $# -eq 1 ]] || { usage >&2; exit 2; }
    [[ -f "$RTMP_WORKFLOW_STATE" && -f "$PREPARE_RESULT" ]] || {
      echo '[openframetap] Acknowledged workflow or sanitized prepare evidence is missing.' >&2
      exit 2
    }
    grep -Eq '"phase"[[:space:]]*:[[:space:]]*"prepare_acknowledged"' "$RTMP_WORKFLOW_STATE" || {
      echo '[openframetap] Refused: Wi-Fi proposal requires prepare_acknowledged state.' >&2
      exit 4
    }
    deploy || exit $?
    run_remote rtmp-wifi-proposal-stage "set -eu
cd $REMOTE_DIR
test -f \"\$HOME/.config/openframetap/secrets.env\"
test ! -L \"\$HOME/.config/openframetap/secrets.env\"
test \"\$(stat -c %a \"\$HOME/.config/openframetap/secrets.env\")\" = 600
mkdir -p artifacts/private/proposal-input artifacts/private/proposals artifacts/sanitized/proposals
chmod 700 artifacts/private artifacts/private/proposal-input artifacts/private/proposals"
    status=$?
    [[ $status -eq 0 ]] || exit "$status"
    "$SCP_BIN" "${SSH_OPTIONS[@]}" "$RTMP_WORKFLOW_STATE" \
      "$TARGET:${REMOTE_DIR#\~/}/artifacts/private/proposal-input/workflow.json.new" || exit $?
    "$SCP_BIN" "${SSH_OPTIONS[@]}" "$PREPARE_RESULT" \
      "$TARGET:${REMOTE_DIR#\~/}/artifacts/private/proposal-input/prepare-result.json.new" || exit $?
    run_remote rtmp-wifi-proposal-input "set -eu
cd $REMOTE_DIR
mv artifacts/private/proposal-input/workflow.json.new artifacts/private/proposal-input/workflow.json
mv artifacts/private/proposal-input/prepare-result.json.new artifacts/private/proposal-input/prepare-result.json
chmod 600 artifacts/private/proposal-input/workflow.json artifacts/private/proposal-input/prepare-result.json
.venv/bin/python -m openframetap pocket3 rtmp propose wifi \\
  --secret-file \"\$HOME/.config/openframetap/secrets.env\" \\
  --prepare-result artifacts/private/proposal-input/prepare-result.json \\
  --state-file artifacts/private/proposal-input/workflow.json"
    status=$?
    [[ $status -eq 0 ]] || exit "$status"
    stem="$(extract_stem PROPOSAL_STEM | tr -d '\r')"
    [[ "$stem" == wifi-* ]] || {
      echo '[openframetap] Refused: missing sanitized Wi-Fi proposal marker.' >&2
      exit 3
    }
    pull_dir "artifacts/private/proposals/$stem" "$ROOT_DIR/artifacts/private/proposals" || exit $?
    pull_dir "artifacts/sanitized/proposals/$stem" "$ROOT_DIR/artifacts/sanitized/proposals" || exit $?
    pull_file artifacts/private/proposal-input/workflow.json "$ROOT_DIR/artifacts/private" || exit $?
    mv "$ROOT_DIR/artifacts/private/workflow.json" "$RTMP_WORKFLOW_STATE"
    printf '[openframetap] PRIVATE_PROPOSAL_DIR=%s\n' "$ROOT_DIR/artifacts/private/proposals/$stem"
    printf '[openframetap] SANITIZED_PROPOSAL_DIR=%s\n' "$ROOT_DIR/artifacts/sanitized/proposals/$stem"
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
  ble-listen)
    seconds="${2:-60}"
    [[ "$seconds" =~ ^[1-9][0-9]*$ ]] || { echo 'seconds must be a positive integer' >&2; exit 2; }
    deploy || exit $?
    run_remote ble-listen "set -u
cd $REMOTE_DIR
bash scripts/capture-pocket3.sh listen '$POCKET3_ADDRESS' '$seconds'"
    status=$?
    stem="$(extract_stem ARTIFACT_DIR)"
    [[ -n "$stem" ]] || { echo '[openframetap] Missing Pocket listen artifact marker' >&2; exit 3; }
    pull_dir "artifacts/$stem" "$REMOTE_ARTIFACTS" || exit $?
    exit "$status"
    ;;
  pocket3-pair-status)
    deploy || exit $?
    run_remote pocket3-pair-status "set -u
cd $REMOTE_DIR
bash scripts/capture-pocket3.sh pair-status '$POCKET3_ADDRESS' 10"
    status=$?
    stem="$(extract_stem ARTIFACT_DIR)"
    [[ -n "$stem" ]] || { echo '[openframetap] Missing pairing-status artifact marker' >&2; exit 3; }
    pull_dir "artifacts/$stem" "$REMOTE_ARTIFACTS" || exit $?
    exit "$status"
    ;;
  pocket3-pair)
    deploy || exit $?
    run_remote pocket3-pair "set -u
cd $REMOTE_DIR
.venv/bin/python -m openframetap pocket3 pair start '$POCKET3_ADDRESS' --proposal-dir artifacts/local"
    exit $?
    ;;
  pocket3-telemetry)
    seconds="${2:-60}"
    [[ "$seconds" =~ ^[1-9][0-9]*$ ]] || { echo 'seconds must be a positive integer' >&2; exit 2; }
    deploy || exit $?
    run_remote pocket3-telemetry "set -u
cd $REMOTE_DIR
bash scripts/capture-pocket3.sh telemetry '$POCKET3_ADDRESS' '$seconds'"
    status=$?
    stem="$(extract_stem ARTIFACT_DIR)"
    [[ -n "$stem" ]] || { echo '[openframetap] Missing telemetry artifact marker' >&2; exit 3; }
    pull_dir "artifacts/$stem" "$REMOTE_ARTIFACTS" || exit $?
    exit "$status"
    ;;
  pocket3-experiment)
    seconds="${2:-180}"
    [[ $# -le 2 ]] || { usage >&2; exit 2; }
    [[ "$seconds" =~ ^[1-9][0-9]*$ ]] || { echo 'seconds must be a positive integer' >&2; exit 2; }
    [[ -t 0 ]] || {
      echo '[openframetap] Refused: experiment requires the device owner at a real terminal.' >&2
      exit 4
    }
    git_head="$(git -C "$ROOT_DIR" rev-parse HEAD)" || exit $?
    print_target
    printf '%s\n' '[openframetap] Passive experiment: FFF4 CCCD only; FFF5 writes are prohibited.'
    printf '[openframetap] Duration limit: %s seconds\n' "$seconds"
    deploy || exit $?
    run_remote_interactive pocket3-experiment \
      "cd $REMOTE_DIR && OPENFRAMETAP_GIT_HEAD='$git_head' bash scripts/capture-pocket3.sh experiment '$POCKET3_ADDRESS' '$seconds'"
    status=$?
    stem="$(extract_stem ARTIFACT_DIR | tr -d '\r')"
    [[ -n "$stem" ]] || { echo '[openframetap] Missing experiment artifact marker' >&2; exit 3; }
    pull_dir "artifacts/$stem" "$REMOTE_ARTIFACTS" || exit $?
    exit "$status"
    ;;
  analyze-telemetry)
    [[ $# -eq 2 ]] || { usage >&2; exit 2; }
    stem="$(basename -- "$2")"
    [[ "$stem" =~ ^pocket3-experiment-[0-9]{8}-[0-9]{6}$ ]] || {
      echo 'experiment directory must be pocket3-experiment-YYYYMMDD-HHMMSS' >&2
      exit 2
    }
    git_head="$(git -C "$ROOT_DIR" rev-parse HEAD)" || exit $?
    deploy || exit $?
    run_remote analyze-telemetry "set -eu
cd $REMOTE_DIR
OPENFRAMETAP_GIT_HEAD='$git_head' .venv/bin/python -m openframetap analyze telemetry \
  'artifacts/$stem' --analysis-location rock4d"
    status=$?
    pull_dir "artifacts/$stem" "$REMOTE_ARTIFACTS" || exit $?
    exit "$status"
    ;;
  pocket3-send-frame)
    [[ $# -ge 3 && $# -le 5 ]] || { usage >&2; exit 2; }
    frame_path="$2"
    command_name="$3"
    seconds="${4:-20}"
    prerequisite_path="${5:-}"
    [[ -f "$frame_path" ]] || { echo "frame file not found: $frame_path" >&2; exit 2; }
    [[ "$seconds" =~ ^[1-9][0-9]*$ ]] || { echo 'seconds must be a positive integer' >&2; exit 2; }
    case "$command_name" in
      set_pairing_pin|pairing_stage1_ack|pairing_stage2) ;;
      *) echo "command is not in the pairing allowlist: $command_name" >&2; exit 2 ;;
    esac
    frame_hex="$(od -An -v -tx1 "$frame_path" | tr -d ' \n')"
    frame_sha256="$(sha256sum "$frame_path" | awk '{print tolower($1)}')"
    prerequisite_hex=""
    prerequisite_sha256=""
    if [[ -n "$prerequisite_path" ]]; then
      [[ -f "$prerequisite_path" ]] || {
        echo "required incoming frame file not found: $prerequisite_path" >&2
        exit 2
      }
      prerequisite_hex="$(od -An -v -tx1 "$prerequisite_path" | tr -d ' \n')"
      prerequisite_sha256="$(sha256sum "$prerequisite_path" | awk '{print tolower($1)}')"
    fi
    [[ -n "$frame_hex" ]] || { echo 'frame file is empty' >&2; exit 2; }
    print_target
    printf '[openframetap] MANUAL SINGLE-FRAME WRITE\n'
    printf '[openframetap] Pocket address: %s\n' "$POCKET3_ADDRESS"
    printf '[openframetap] Command: %s\n' "$command_name"
    printf '[openframetap] Frame hex: %s\n' "$frame_hex"
    printf '[openframetap] SHA-256: %s\n' "$frame_sha256"
    if [[ -n "$prerequisite_hex" ]]; then
      printf '[openframetap] Required incoming frame: %s\n' "$prerequisite_hex"
      printf '[openframetap] Required incoming SHA-256: %s\n' "$prerequisite_sha256"
      printf '[openframetap] No write occurs unless that exact incoming frame is observed.\n'
    fi
    printf '[openframetap] No automatic follow-up frame will be sent.\n'
    printf '[openframetap] Type the full SHA-256 to write this one frame: '
    IFS= read -r typed_sha256
    if [[ "${typed_sha256,,}" != "$frame_sha256" ]]; then
      echo '[openframetap] Confirmation mismatch; no deployment or BLE connection was attempted.' >&2
      exit 4
    fi
    deploy || exit $?
    run_remote manual-frame "set -u
cd $REMOTE_DIR
bash scripts/capture-pocket3.sh manual-frame '$POCKET3_ADDRESS' '$seconds' '$frame_hex' '$command_name' '$frame_sha256' '$prerequisite_hex'"
    status=$?
    stem="$(extract_stem ARTIFACT_DIR)"
    [[ -n "$stem" ]] || { echo '[openframetap] Missing manual-frame artifact marker' >&2; exit 3; }
    pull_dir "artifacts/$stem" "$REMOTE_ARTIFACTS" || exit $?
    exit "$status"
    ;;
  pocket3-manual-pair-session)
    seconds="${2:-60}"
    [[ $# -le 2 ]] || { usage >&2; exit 2; }
    [[ "$seconds" =~ ^[1-9][0-9]*$ ]] || { echo 'seconds must be a positive integer' >&2; exit 2; }
    [[ -t 0 ]] || {
      echo '[openframetap] Refused: this action requires the device owner at a real terminal.' >&2
      exit 4
    }
    print_target
    printf '%s\n' '[openframetap] This is the final allowed application-pairing attempt.'
    printf '%s\n' '[openframetap] Every candidate requires a separate full SHA-256 typed by the owner.'
    printf '[openframetap] Type RUN to open the interactive session, or anything else to stop: '
    IFS= read -r start_confirmation
    [[ "$start_confirmation" == "RUN" ]] || {
      echo '[openframetap] Cancelled before deployment or BLE connection.' >&2
      exit 4
    }
    deploy || exit $?
    run_remote_interactive manual-pair-session \
      "cd $REMOTE_DIR && bash scripts/capture-pocket3.sh manual-pair-session '$POCKET3_ADDRESS' '$seconds'"
    status=$?
    stem="$(extract_stem ARTIFACT_DIR | tr -d '\r')"
    [[ -n "$stem" ]] || { echo '[openframetap] Missing manual-pair-session artifact marker' >&2; exit 3; }
    pull_dir "artifacts/$stem" "$REMOTE_ARTIFACTS" || exit $?
    exit "$status"
    ;;
  *)
    usage >&2
    exit 2
    ;;
esac
