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
APPROVED_RECOVERY_STAGE1_SHA256="a9c619ff04901974b5c30d255730e9a807d4d1919ab05ae89504777df11a2ee5"
APPROVED_RECOVERY_STAGE2_SHA256="624c92dc2ce9364346e1b5e548f8be260b9f21e35fca20503b38315c90999286"
APPROVED_RECOVERY_DIR="$ROOT_DIR/artifacts/private/proposals/prepare-recovery-20260718T181349Z"
APPROVED_WIFI_RECOVERY_SHA256="8c4de55a03533eba4ef59d038acada497aedcf33a449c67bd433a411d1fd5a3f"
APPROVED_WIFI_RECOVERY_DIR="$ROOT_DIR/artifacts/private/proposals/wifi-20260718T184319Z"
PREPARE_RECOVERY_RESULT="$ROOT_DIR/artifacts/private/pocket3-rtmp-prepare-recovery-20260719-023518/summary.json"
PREPARE_RECOVERY_RESULT_SHA256="34e0dfd95a52336e620edfa2206a05bd14fad533140fa793b85b89356285e9a1"
APPROVED_STREAM_SHA256="0765f4462b530b05071172f0175bed14f70720b4479b414ce89dea44481a7fc4"
APPROVED_STREAM_DIR="$ROOT_DIR/artifacts/private/proposals/stream-20260718T192544Z"
APPROVED_STREAM_START_SHA256="a5ea033f25d80ddd6b7ffe2f09b9693abede7c95458fab1da88b3bc140c6d150"
APPROVED_STREAM_START_DIR="$ROOT_DIR/artifacts/private/proposals/stream-start-20260718T195251Z"
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
  ./scripts/remote.sh pocket3-rtmp-send-approved-prepare-recovery
  ./scripts/remote.sh pocket3-rtmp-send-approved-prepare-wifi-recovery
  ./scripts/remote.sh pocket3-rtmp-send-approved-stream
  ./scripts/remote.sh pocket3-rtmp-send-approved-stream-start
  ./scripts/remote.sh pocket3-rtmp-run-full-stream-session
  ./scripts/remote.sh pocket3-rtmp-capture-live-sample [seconds]
  ./scripts/remote.sh pocket3-rtmp-configure-wifi-secrets
  ./scripts/remote.sh pocket3-rtmp-configure-stream-key
  ./scripts/remote.sh pocket3-rtmp-propose-wifi
  ./scripts/remote.sh pocket3-rtmp-propose-stream
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
  pocket3-rtmp-send-approved-prepare-recovery)
    [[ $# -eq 1 ]] || { usage >&2; exit 2; }
    [[ -t 0 ]] || {
      echo '[openframetap] Refused: prepare recovery requires the device owner at a real terminal.' >&2
      exit 4
    }
    proposal_json="$APPROVED_RECOVERY_DIR/proposal-private.json"
    stage1_bin="$APPROVED_RECOVERY_DIR/stage1.bin"
    stage2_bin="$APPROVED_RECOVERY_DIR/stage2.bin"
    approval_consumed="$APPROVED_RECOVERY_DIR/owner-command-invocation.consumed"
    [[ -f "$proposal_json" && -f "$stage1_bin" && -f "$stage2_bin" && -f "$RTMP_WORKFLOW_STATE" ]] || {
      echo '[openframetap] Fixed approved recovery proposal, frames, or workflow state is missing.' >&2
      exit 2
    }
    [[ ! -e "$approval_consumed" ]] || {
      echo '[openframetap] Refused: this fixed one-time prepare-recovery authorization is already consumed.' >&2
      exit 4
    }
    grep -Eq '"phase"[[:space:]]*:[[:space:]]*"wifi_connected_or_unknown"' "$RTMP_WORKFLOW_STATE" || {
      echo '[openframetap] Refused: prepare recovery requires wifi_connected_or_unknown state.' >&2
      exit 4
    }
    actual_stage1_sha256="$(sha256sum "$stage1_bin" | awk '{print tolower($1)}')"
    actual_stage2_sha256="$(sha256sum "$stage2_bin" | awk '{print tolower($1)}')"
    [[ "$actual_stage1_sha256" == "$APPROVED_RECOVERY_STAGE1_SHA256" ]] || {
      echo '[openframetap] Approved recovery Stage 1 SHA-256 mismatch.' >&2
      exit 5
    }
    [[ "$actual_stage2_sha256" == "$APPROVED_RECOVERY_STAGE2_SHA256" ]] || {
      echo '[openframetap] Approved recovery Stage 2 SHA-256 mismatch.' >&2
      exit 5
    }
    grep -Fq "\"frame_sha256\": \"$APPROVED_RECOVERY_STAGE1_SHA256\"" "$proposal_json" || {
      echo '[openframetap] Recovery proposal does not name the approved Stage 1 SHA-256.' >&2
      exit 5
    }
    grep -Fq "\"frame_sha256\": \"$APPROVED_RECOVERY_STAGE2_SHA256\"" "$proposal_json" || {
      echo '[openframetap] Recovery proposal does not name the approved Stage 2 SHA-256.' >&2
      exit 5
    }
    printf '%s\n' '[openframetap] COMMAND INVOCATION AUTHORIZES TWO FIXED PREPARE FRAMES:'
    printf '[openframetap] Stage 1 02/E1 SHA-256: %s\n' "$APPROVED_RECOVERY_STAGE1_SHA256"
    printf '[openframetap] Stage 2 02/8E SHA-256: %s\n' "$APPROVED_RECOVERY_STAGE2_SHA256"
    printf '%s\n' '[openframetap] Stage 2 is sent once only after the exact Stage 1 ACK in the same connection.'
    printf '%s\n' '[openframetap] No Wi-Fi retry, RTMP configuration, stream start/stop, or other command is included.'
    printf 'consumed_at_utc=%s\nstage1_sha256=%s\nstage2_sha256=%s\n' \
      "$(timestamp)" "$APPROVED_RECOVERY_STAGE1_SHA256" "$APPROVED_RECOVERY_STAGE2_SHA256" \
      >"$approval_consumed"
    deploy || exit $?
    run_remote rtmp-prepare-recovery-stage "set -eu
cd $REMOTE_DIR
mkdir -p artifacts/private/approved-prepare-recovery
chmod 700 artifacts/private artifacts/private/approved-prepare-recovery"
    status=$?
    [[ $status -eq 0 ]] || exit "$status"
    "$SCP_BIN" "${SSH_OPTIONS[@]}" "$proposal_json" \
      "$TARGET:${REMOTE_DIR#\~/}/artifacts/private/approved-prepare-recovery/proposal-private.json.new" || exit $?
    "$SCP_BIN" "${SSH_OPTIONS[@]}" "$RTMP_WORKFLOW_STATE" \
      "$TARGET:${REMOTE_DIR#\~/}/artifacts/private/pocket3-rtmp-workflow.json.new" || exit $?
    run_remote rtmp-prepare-recovery-finalize "set -eu
cd $REMOTE_DIR
mv artifacts/private/approved-prepare-recovery/proposal-private.json.new artifacts/private/approved-prepare-recovery/proposal-private.json
mv artifacts/private/pocket3-rtmp-workflow.json.new artifacts/private/pocket3-rtmp-workflow.json
chmod 600 artifacts/private/approved-prepare-recovery/proposal-private.json artifacts/private/pocket3-rtmp-workflow.json"
    status=$?
    [[ $status -eq 0 ]] || exit "$status"
    run_remote_interactive rtmp-prepare-recovery-send \
      "cd $REMOTE_DIR && bash scripts/capture-pocket3.sh rtmp-prepare-recovery '$POCKET3_ADDRESS' 10 'artifacts/private/approved-prepare-recovery/proposal-private.json' 'artifacts/private/pocket3-rtmp-workflow.json'"
    status=$?
    stem="$(extract_stem ARTIFACT_DIR | tr -d '\r')"
    [[ "$stem" == private/pocket3-rtmp-prepare-recovery-* ]] || {
      echo '[openframetap] Missing private prepare recovery artifact marker' >&2
      exit 3
    }
    pull_dir "artifacts/$stem" "$ROOT_DIR/artifacts/private" || exit $?
    exit "$status"
    ;;
  pocket3-rtmp-send-approved-prepare-wifi-recovery)
    [[ $# -eq 1 ]] || { usage >&2; exit 2; }
    [[ -t 0 ]] || {
      echo '[openframetap] Refused: prepare/Wi-Fi recovery requires the device owner at a real terminal.' >&2
      exit 4
    }
    prepare_json="$APPROVED_RECOVERY_DIR/proposal-private.json"
    stage1_bin="$APPROVED_RECOVERY_DIR/stage1.bin"
    stage2_bin="$APPROVED_RECOVERY_DIR/stage2.bin"
    wifi_json="$APPROVED_WIFI_RECOVERY_DIR/proposal-private.json"
    wifi_bin="$APPROVED_WIFI_RECOVERY_DIR/proposal.bin"
    approval_consumed="$APPROVED_WIFI_RECOVERY_DIR/owner-command-invocation.consumed"
    [[ -f "$prepare_json" && -f "$stage1_bin" && -f "$stage2_bin" && -f "$wifi_json" && -f "$wifi_bin" && -f "$PREPARE_RECOVERY_RESULT" && -f "$RTMP_WORKFLOW_STATE" ]] || {
      echo '[openframetap] Fixed recovery proposals, frames, evidence, or workflow state is missing.' >&2
      exit 2
    }
    [[ ! -e "$approval_consumed" ]] || {
      echo '[openframetap] Refused: this fixed one-time prepare/Wi-Fi recovery authorization is already consumed.' >&2
      exit 4
    }
    grep -Eq '"phase"[[:space:]]*:[[:space:]]*"wifi_connected_or_unknown"' "$RTMP_WORKFLOW_STATE" || {
      echo '[openframetap] Refused: prepare/Wi-Fi recovery requires wifi_connected_or_unknown state.' >&2
      exit 4
    }
    actual_stage1_sha256="$(sha256sum "$stage1_bin" | awk '{print tolower($1)}')"
    actual_stage2_sha256="$(sha256sum "$stage2_bin" | awk '{print tolower($1)}')"
    actual_wifi_sha256="$(sha256sum "$wifi_bin" | awk '{print tolower($1)}')"
    actual_recovery_result_sha256="$(sha256sum "$PREPARE_RECOVERY_RESULT" | awk '{print tolower($1)}')"
    [[ "$actual_stage1_sha256" == "$APPROVED_RECOVERY_STAGE1_SHA256" ]] || exit 5
    [[ "$actual_stage2_sha256" == "$APPROVED_RECOVERY_STAGE2_SHA256" ]] || exit 5
    [[ "$actual_wifi_sha256" == "$APPROVED_WIFI_RECOVERY_SHA256" ]] || exit 5
    [[ "$actual_recovery_result_sha256" == "$PREPARE_RECOVERY_RESULT_SHA256" ]] || exit 5
    grep -Fq '"recovery_result": "prepare_stage2_response_validated"' "$PREPARE_RECOVERY_RESULT" || {
      echo '[openframetap] Prior recovery evidence is not the validated Stage 2 result.' >&2
      exit 5
    }
    grep -Fq "\"frame_sha256\": \"$APPROVED_WIFI_RECOVERY_SHA256\"" "$wifi_json" || exit 5
    grep -Fq '"sequence_hex": "0x8C1A"' "$wifi_json" || exit 5
    grep -Fq "\"prepare_result_sha256\": \"$PREPARE_RECOVERY_RESULT_SHA256\"" "$wifi_json" || exit 5
    printf '%s\n' '[openframetap] COMMAND INVOCATION AUTHORIZES THREE FIXED FRAMES IN ONE BLE CONNECTION:'
    printf '[openframetap] Stage 1 02/E1 SHA-256: %s\n' "$APPROVED_RECOVERY_STAGE1_SHA256"
    printf '[openframetap] Stage 2 02/8E SHA-256: %s\n' "$APPROVED_RECOVERY_STAGE2_SHA256"
    printf '[openframetap] Stage 3 07/47 SHA-256: %s (private payload redacted)\n' "$APPROVED_WIFI_RECOVERY_SHA256"
    printf '%s\n' '[openframetap] Each later frame requires the exact preceding response; no RTMP config or stream start/stop is included.'
    printf 'consumed_at_utc=%s\nstage1_sha256=%s\nstage2_sha256=%s\nwifi_sha256=%s\n' \
      "$(timestamp)" "$APPROVED_RECOVERY_STAGE1_SHA256" "$APPROVED_RECOVERY_STAGE2_SHA256" "$APPROVED_WIFI_RECOVERY_SHA256" \
      >"$approval_consumed"
    deploy || exit $?
    run_remote rtmp-prepare-wifi-recovery-stage "set -eu
cd $REMOTE_DIR
mkdir -p artifacts/private/approved-prepare-wifi-recovery
chmod 700 artifacts/private artifacts/private/approved-prepare-wifi-recovery"
    status=$?
    [[ $status -eq 0 ]] || exit "$status"
    "$SCP_BIN" "${SSH_OPTIONS[@]}" "$prepare_json" \
      "$TARGET:${REMOTE_DIR#\~/}/artifacts/private/approved-prepare-wifi-recovery/prepare-proposal-private.json.new" || exit $?
    "$SCP_BIN" "${SSH_OPTIONS[@]}" "$wifi_json" \
      "$TARGET:${REMOTE_DIR#\~/}/artifacts/private/approved-prepare-wifi-recovery/wifi-proposal-private.json.new" || exit $?
    "$SCP_BIN" "${SSH_OPTIONS[@]}" "$RTMP_WORKFLOW_STATE" \
      "$TARGET:${REMOTE_DIR#\~/}/artifacts/private/pocket3-rtmp-workflow.json.new" || exit $?
    run_remote rtmp-prepare-wifi-recovery-finalize "set -eu
cd $REMOTE_DIR
mv artifacts/private/approved-prepare-wifi-recovery/prepare-proposal-private.json.new artifacts/private/approved-prepare-wifi-recovery/prepare-proposal-private.json
mv artifacts/private/approved-prepare-wifi-recovery/wifi-proposal-private.json.new artifacts/private/approved-prepare-wifi-recovery/wifi-proposal-private.json
mv artifacts/private/pocket3-rtmp-workflow.json.new artifacts/private/pocket3-rtmp-workflow.json
chmod 600 artifacts/private/approved-prepare-wifi-recovery/*.json artifacts/private/pocket3-rtmp-workflow.json"
    status=$?
    [[ $status -eq 0 ]] || exit "$status"
    run_remote_interactive rtmp-prepare-wifi-recovery-send \
      "cd $REMOTE_DIR && bash scripts/capture-pocket3.sh rtmp-prepare-wifi-recovery '$POCKET3_ADDRESS' 30 'artifacts/private/approved-prepare-wifi-recovery/prepare-proposal-private.json' 'artifacts/private/approved-prepare-wifi-recovery/wifi-proposal-private.json' 'artifacts/private/pocket3-rtmp-workflow.json'"
    status=$?
    stem="$(extract_stem ARTIFACT_DIR | tr -d '\r')"
    [[ "$stem" == private/pocket3-rtmp-prepare-wifi-recovery-* ]] || {
      echo '[openframetap] Missing private prepare/Wi-Fi recovery artifact marker' >&2
      exit 3
    }
    pull_dir "artifacts/$stem" "$ROOT_DIR/artifacts/private" || exit $?
    exit "$status"
    ;;
  pocket3-rtmp-send-approved-stream)
    [[ $# -eq 1 ]] || { usage >&2; exit 2; }
    [[ -t 0 ]] || {
      echo '[openframetap] Refused: fixed stream send requires the device owner at a real terminal.' >&2
      exit 4
    }
    proposal_json="$APPROVED_STREAM_DIR/proposal-private.json"
    proposal_bin="$APPROVED_STREAM_DIR/proposal.bin"
    approval_consumed="$APPROVED_STREAM_DIR/owner-command-invocation.consumed"
    [[ -f "$proposal_json" && -f "$proposal_bin" && -f "$RTMP_WORKFLOW_STATE" ]] || {
      echo '[openframetap] Fixed stream proposal or workflow state is missing.' >&2
      exit 2
    }
    [[ ! -e "$approval_consumed" ]] || {
      echo '[openframetap] Refused: this fixed one-time stream authorization is already consumed.' >&2
      exit 4
    }
    grep -Eq '"phase"[[:space:]]*:[[:space:]]*"stream_proposed"' "$RTMP_WORKFLOW_STATE" || {
      echo '[openframetap] Refused: fixed stream send requires stream_proposed state.' >&2
      exit 4
    }
    actual_sha256="$(sha256sum "$proposal_bin" | awk '{print tolower($1)}')"
    [[ "$actual_sha256" == "$APPROVED_STREAM_SHA256" ]] || {
      echo '[openframetap] Fixed stream proposal SHA-256 mismatch.' >&2
      exit 5
    }
    grep -Fq "\"frame_sha256\": \"$APPROVED_STREAM_SHA256\"" "$proposal_json" || exit 5
    grep -Fq '"command": "configure_live_stream"' "$proposal_json" || exit 5
    grep -Fq '"cmd_id": "0x78"' "$proposal_json" || exit 5
    printf '%s\n' '[openframetap] COMMAND INVOCATION AUTHORIZES ONE FIXED 08/78 STREAM FRAME.'
    printf '[openframetap] Frame SHA-256: %s (private RTMP URL redacted)\n' "$APPROVED_STREAM_SHA256"
    printf '%s\n' '[openframetap] No retry, 02/8E start/stop, Wi-Fi, camera, or gimbal frame is included.'
    printf 'consumed_at_utc=%s\nframe_sha256=%s\n' "$(timestamp)" "$APPROVED_STREAM_SHA256" \
      >"$approval_consumed"
    deploy || exit $?
    run_remote rtmp-stream-send-stage "set -eu
cd $REMOTE_DIR
.venv/bin/python -m openframetap video server status | grep -q '\"state\": \"running\"'
mkdir -p artifacts/private/approved-stream
chmod 700 artifacts/private artifacts/private/approved-stream"
    status=$?
    [[ $status -eq 0 ]] || exit "$status"
    "$SCP_BIN" "${SSH_OPTIONS[@]}" "$proposal_json" \
      "$TARGET:${REMOTE_DIR#\~/}/artifacts/private/approved-stream/proposal-private.json.new" || exit $?
    "$SCP_BIN" "${SSH_OPTIONS[@]}" "$RTMP_WORKFLOW_STATE" \
      "$TARGET:${REMOTE_DIR#\~/}/artifacts/private/pocket3-rtmp-workflow.json.new" || exit $?
    run_remote rtmp-stream-send-finalize "set -eu
cd $REMOTE_DIR
mv artifacts/private/approved-stream/proposal-private.json.new artifacts/private/approved-stream/proposal-private.json
mv artifacts/private/pocket3-rtmp-workflow.json.new artifacts/private/pocket3-rtmp-workflow.json
chmod 600 artifacts/private/approved-stream/proposal-private.json artifacts/private/pocket3-rtmp-workflow.json"
    status=$?
    [[ $status -eq 0 ]] || exit "$status"
    run_remote_interactive rtmp-stream-send \
      "cd $REMOTE_DIR && OPENFRAMETAP_COMMAND_INVOCATION_APPROVAL=1 OPENFRAMETAP_FIXED_PROPOSAL_SHA256='$APPROVED_STREAM_SHA256' bash scripts/capture-pocket3.sh rtmp-stream-proposal '$POCKET3_ADDRESS' 60 'artifacts/private/approved-stream/proposal-private.json' 'artifacts/private/pocket3-rtmp-workflow.json'"
    status=$?
    stem="$(extract_stem ARTIFACT_DIR | tr -d '\r')"
    [[ "$stem" == private/pocket3-rtmp-stream-* ]] || {
      echo '[openframetap] Missing private stream-send artifact marker' >&2
      exit 3
    }
    pull_dir "artifacts/$stem" "$ROOT_DIR/artifacts/private" || exit $?
    pull_file artifacts/private/pocket3-rtmp-workflow.json "$ROOT_DIR/artifacts/private" || true
    exit "$status"
    ;;
  pocket3-rtmp-send-approved-stream-start)
    [[ $# -eq 1 ]] || { usage >&2; exit 2; }
    proposal_json="$APPROVED_STREAM_START_DIR/proposal-private.json"
    proposal_bin="$APPROVED_STREAM_START_DIR/proposal.bin"
    approval_consumed="$APPROVED_STREAM_START_DIR/autonomous-reversible.consumed"
    [[ -f "$proposal_json" && -f "$proposal_bin" && -f "$RTMP_WORKFLOW_STATE" ]] || {
      echo '[openframetap] Fixed start proposal or workflow state is missing.' >&2
      exit 2
    }
    [[ ! -e "$approval_consumed" ]] || {
      echo '[openframetap] Refused: fixed one-time start proposal is already consumed.' >&2
      exit 4
    }
    grep -Eq '"phase"[[:space:]]*:[[:space:]]*"stream_sent"' "$RTMP_WORKFLOW_STATE" || {
      echo '[openframetap] Refused: start proposal requires stream_sent state.' >&2
      exit 4
    }
    actual_sha256="$(sha256sum "$proposal_bin" | awk '{print tolower($1)}')"
    [[ "$actual_sha256" == "$APPROVED_STREAM_START_SHA256" ]] || exit 5
    grep -Fq "\"frame_sha256\": \"$APPROVED_STREAM_START_SHA256\"" "$proposal_json" || exit 5
    grep -Fq '"command": "start_live_stream_transport"' "$proposal_json" || exit 5
    printf '%s\n' '[openframetap] AUTONOMOUS REVERSIBLE: one fixed 02/8E start frame.'
    printf '[openframetap] Frame SHA-256: %s\n' "$APPROVED_STREAM_START_SHA256"
    printf '%s\n' '[openframetap] No retry, stop, Wi-Fi, camera, or gimbal frame is included.'
    printf 'consumed_at_utc=%s\nframe_sha256=%s\n' "$(timestamp)" "$APPROVED_STREAM_START_SHA256" \
      >"$approval_consumed"
    deploy || exit $?
    run_remote rtmp-stream-start-stage "set -eu
cd $REMOTE_DIR
.venv/bin/python -m openframetap video server status | grep -q '\"state\": \"running\"'
mkdir -p artifacts/private/approved-stream-start
chmod 700 artifacts/private artifacts/private/approved-stream-start"
    status=$?
    [[ $status -eq 0 ]] || exit "$status"
    "$SCP_BIN" "${SSH_OPTIONS[@]}" "$proposal_json" \
      "$TARGET:${REMOTE_DIR#\~/}/artifacts/private/approved-stream-start/proposal-private.json.new" || exit $?
    "$SCP_BIN" "${SSH_OPTIONS[@]}" "$RTMP_WORKFLOW_STATE" \
      "$TARGET:${REMOTE_DIR#\~/}/artifacts/private/pocket3-rtmp-workflow.json.new" || exit $?
    run_remote rtmp-stream-start-finalize "set -eu
cd $REMOTE_DIR
mv artifacts/private/approved-stream-start/proposal-private.json.new artifacts/private/approved-stream-start/proposal-private.json
mv artifacts/private/pocket3-rtmp-workflow.json.new artifacts/private/pocket3-rtmp-workflow.json
chmod 600 artifacts/private/approved-stream-start/proposal-private.json artifacts/private/pocket3-rtmp-workflow.json"
    status=$?
    [[ $status -eq 0 ]] || exit "$status"
    run_remote rtmp-stream-start-send \
      "cd $REMOTE_DIR && OPENFRAMETAP_USER_INITIATED=1 OPENFRAMETAP_AUTONOMOUS_REVERSIBLE=1 OPENFRAMETAP_COMMAND_INVOCATION_APPROVAL=1 OPENFRAMETAP_FIXED_PROPOSAL_SHA256='$APPROVED_STREAM_START_SHA256' bash scripts/capture-pocket3.sh rtmp-stream-start-proposal '$POCKET3_ADDRESS' 60 'artifacts/private/approved-stream-start/proposal-private.json' 'artifacts/private/pocket3-rtmp-workflow.json'"
    status=$?
    stem="$(extract_stem ARTIFACT_DIR | tr -d '\r')"
    [[ "$stem" == private/pocket3-rtmp-stream-start-* ]] || {
      echo '[openframetap] Missing private stream-start artifact marker' >&2
      exit 3
    }
    pull_dir "artifacts/$stem" "$ROOT_DIR/artifacts/private" || exit $?
    pull_file artifacts/private/pocket3-rtmp-workflow.json "$ROOT_DIR/artifacts/private" || true
    exit "$status"
    ;;
  pocket3-rtmp-run-full-stream-session)
    [[ $# -eq 1 ]] || { usage >&2; exit 2; }
    prepare_json="$APPROVED_RECOVERY_DIR/proposal-private.json"
    wifi_json="$APPROVED_WIFI_RECOVERY_DIR/proposal-private.json"
    stream_json="$APPROVED_STREAM_DIR/proposal-private.json"
    start_json="$APPROVED_STREAM_START_DIR/proposal-private.json"
    session_consumed="$APPROVED_STREAM_START_DIR/full-stream-session.consumed"
    [[ -f "$prepare_json" && -f "$wifi_json" && -f "$stream_json" && -f "$start_json" && -f "$RTMP_WORKFLOW_STATE" ]] || exit 2
    [[ ! -e "$session_consumed" ]] || {
      echo '[openframetap] Refused: full-stream session is already consumed.' >&2
      exit 4
    }
    grep -Eq '"phase"[[:space:]]*:[[:space:]]*"waiting_for_rtmp"' "$RTMP_WORKFLOW_STATE" || {
      echo '[openframetap] Refused: full-stream session requires waiting_for_rtmp state.' >&2
      exit 4
    }
    printf '%s\n' '[openframetap] AUTONOMOUS REVERSIBLE: five response-gated frames in one BLE connection.'
    printf '%s\n' '[openframetap] No loop, random mutation, stop, camera, or gimbal command is included.'
    printf 'consumed_at_utc=%s\n' "$(timestamp)" >"$session_consumed"
    deploy || exit $?
    run_remote rtmp-full-stream-stage "set -eu
cd $REMOTE_DIR
.venv/bin/python -m openframetap video server status | grep -q '\"state\": \"running\"'
mkdir -p artifacts/private/approved-full-stream
chmod 700 artifacts/private artifacts/private/approved-full-stream"
    status=$?
    [[ $status -eq 0 ]] || exit "$status"
    "$SCP_BIN" "${SSH_OPTIONS[@]}" "$prepare_json" "$TARGET:${REMOTE_DIR#\~/}/artifacts/private/approved-full-stream/prepare.json.new" || exit $?
    "$SCP_BIN" "${SSH_OPTIONS[@]}" "$wifi_json" "$TARGET:${REMOTE_DIR#\~/}/artifacts/private/approved-full-stream/wifi.json.new" || exit $?
    "$SCP_BIN" "${SSH_OPTIONS[@]}" "$stream_json" "$TARGET:${REMOTE_DIR#\~/}/artifacts/private/approved-full-stream/stream.json.new" || exit $?
    "$SCP_BIN" "${SSH_OPTIONS[@]}" "$start_json" "$TARGET:${REMOTE_DIR#\~/}/artifacts/private/approved-full-stream/start.json.new" || exit $?
    "$SCP_BIN" "${SSH_OPTIONS[@]}" "$RTMP_WORKFLOW_STATE" "$TARGET:${REMOTE_DIR#\~/}/artifacts/private/pocket3-rtmp-workflow.json.new" || exit $?
    run_remote rtmp-full-stream-finalize "set -eu
cd $REMOTE_DIR
for name in prepare wifi stream start; do mv artifacts/private/approved-full-stream/\$name.json.new artifacts/private/approved-full-stream/\$name.json; done
mv artifacts/private/pocket3-rtmp-workflow.json.new artifacts/private/pocket3-rtmp-workflow.json
chmod 600 artifacts/private/approved-full-stream/*.json artifacts/private/pocket3-rtmp-workflow.json"
    status=$?
    [[ $status -eq 0 ]] || exit "$status"
    run_remote rtmp-full-stream-send \
      "cd $REMOTE_DIR && OPENFRAMETAP_AUTONOMOUS_REVERSIBLE=1 bash scripts/capture-pocket3.sh rtmp-full-stream-recovery '$POCKET3_ADDRESS' 60 'artifacts/private/approved-full-stream/prepare.json' 'artifacts/private/approved-full-stream/wifi.json' 'artifacts/private/approved-full-stream/stream.json' 'artifacts/private/approved-full-stream/start.json' 'artifacts/private/pocket3-rtmp-workflow.json'"
    status=$?
    stem="$(extract_stem ARTIFACT_DIR | tr -d '\r')"
    [[ "$stem" == private/pocket3-rtmp-full-stream-* ]] || {
      echo '[openframetap] Missing full-stream artifact marker' >&2
      exit 3
    }
    pull_dir "artifacts/$stem" "$ROOT_DIR/artifacts/private" || exit $?
    exit "$status"
    ;;
  pocket3-rtmp-capture-live-sample)
    seconds="${2:-8}"
    [[ $# -le 2 && "$seconds" =~ ^[1-9][0-9]*$ && "$seconds" -le 60 ]] || {
      echo 'capture seconds must be an integer from 1 to 60' >&2
      exit 2
    }
    proposal_json="$APPROVED_STREAM_DIR/proposal-private.json"
    [[ -f "$proposal_json" && -f "$RTMP_WORKFLOW_STATE" ]] || exit 2
    [[ -x "$LOCAL_PYTHON_BIN" && -x "$LOCAL_FFMPEG_BIN" && -x "$LOCAL_FFPROBE_BIN" ]] || {
      echo '[openframetap] Local Python/FFmpeg/FFprobe tools are unavailable.' >&2
      exit 2
    }
    run_remote rtmp-live-capture-preflight \
      "cd $REMOTE_DIR && .venv/bin/python -m openframetap video server status"
    status=$?
    [[ $status -eq 0 ]] || exit "$status"
    stamp="$(timestamp)"
    private_output="$ROOT_DIR/artifacts/private/pocket3-live-sample-$stamp"
    sanitized_output="$ROOT_DIR/artifacts/sanitized/pocket3-live-sample-$stamp"
    "$LOCAL_PYTHON_BIN" -m openframetap video capture-live \
      --proposal "$proposal_json" --address "$POCKET3_ADDRESS" --seconds "$seconds" \
      --private-output "$private_output" --sanitized-output "$sanitized_output" \
      --ffmpeg-bin "$LOCAL_FFMPEG_BIN" --ffprobe-bin "$LOCAL_FFPROBE_BIN" \
      --state-file "$RTMP_WORKFLOW_STATE"
    status=$?
    if [[ $status -eq 0 ]]; then
      "$SCP_BIN" "${SSH_OPTIONS[@]}" "$RTMP_WORKFLOW_STATE" \
        "$TARGET:${REMOTE_DIR#\~/}/artifacts/private/pocket3-rtmp-workflow.json.new" || exit $?
      run_remote rtmp-live-capture-state-finalize "set -eu
cd $REMOTE_DIR
mv artifacts/private/pocket3-rtmp-workflow.json.new artifacts/private/pocket3-rtmp-workflow.json
chmod 600 artifacts/private/pocket3-rtmp-workflow.json" || exit $?
    fi
    printf '[openframetap] PRIVATE_SAMPLE_DIR=%s\n' "$private_output"
    printf '[openframetap] SANITIZED_SAMPLE_DIR=%s\n' "$sanitized_output"
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
  pocket3-rtmp-configure-stream-key)
    [[ $# -eq 1 ]] || { usage >&2; exit 2; }
    [[ -t 0 ]] || {
      echo '[openframetap] Refused: RTMP stream-key setup requires the device owner at a real terminal.' >&2
      exit 4
    }
    deploy || exit $?
    run_remote_interactive rtmp-stream-key-setup \
      "cd $REMOTE_DIR && OPENFRAMETAP_USER_INITIATED=1 .venv/bin/python -m openframetap secrets configure-stream-key"
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
  pocket3-rtmp-propose-stream)
    [[ $# -eq 1 ]] || { usage >&2; exit 2; }
    deploy || exit $?
    run_remote rtmp-stream-proposal "set -eu
cd $REMOTE_DIR
.venv/bin/python -m openframetap pocket3 rtmp propose stream \\
  --address '$POCKET3_ADDRESS' \\
  --secret-file "\$HOME/.config/openframetap/secrets.env" \\
  --wifi-result artifacts/private/pocket3-rtmp-prepare-wifi-recovery-20260719-025302/summary.json \\
  --private-root artifacts/private/proposals \\
  --sanitized-root artifacts/sanitized/proposals \\
  --state-file artifacts/private/pocket3-rtmp-workflow.json"
    status=$?
    [[ $status -eq 0 ]] || exit "$status"
    stem="$(extract_stem PROPOSAL_STEM | tr -d '\r')"
    [[ "$stem" == stream-* ]] || {
      echo '[openframetap] Refused: missing stream proposal marker.' >&2
      exit 3
    }
    pull_dir "artifacts/private/proposals/$stem" "$ROOT_DIR/artifacts/private/proposals" || exit $?
    pull_dir "artifacts/sanitized/proposals/$stem" "$ROOT_DIR/artifacts/sanitized/proposals" || exit $?
    pull_file artifacts/private/pocket3-rtmp-workflow.json "$ROOT_DIR/artifacts/private" || exit $?
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
