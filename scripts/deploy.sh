#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TARGET="${ROCK4D_SSH_HOST:-${OPENFRAMETAP_SSH_TARGET:-qqice@100.125.223.67}}"
REMOTE_DIR="${OPENFRAMETAP_REMOTE_DIR:-~/openframetap-runtime}"
SSH_BIN="${OPENFRAMETAP_SSH_BIN:-ssh}"
SSH_OPTIONS=(-o BatchMode=yes -o ConnectTimeout=15)
RSYNC_EXCLUDES=(
  --exclude=.git/
  --exclude=.venv/
  --exclude=runtime/
  --exclude=artifacts/
  --exclude=__pycache__/
  --exclude='*.pyc'
  --exclude='*.pcap'
  --exclude='*.btsnoop'
  --exclude='*.log'
  --exclude='.pytest_cache/'
  --exclude='*.egg-info/'
)
TAR_EXCLUDES=(
  --exclude='artifacts'
  --exclude='artifacts/*'
  --exclude='./.git'
  --exclude='./.git/*'
  --exclude='./.venv'
  --exclude='./.venv/*'
  --exclude='./runtime'
  --exclude='./runtime/*'
  --exclude='./artifacts'
  --exclude='./artifacts/*'
  --exclude='*/__pycache__'
  --exclude='*/__pycache__/*'
  --exclude='*.pyc'
  --exclude='*.pcap'
  --exclude='*.btsnoop'
  --exclude='*.log'
  --exclude='.pytest_cache'
  --exclude='.pytest_cache/*'
  --exclude='*.egg-info'
  --exclude='*.egg-info/*'
)

printf '[openframetap] SSH target: %s\n' "$TARGET"
printf '[openframetap] Deploy source: %s\n' "$ROOT_DIR"
printf '[openframetap] Deploy destination: %s:%s\n' "$TARGET" "$REMOTE_DIR"

if command -v rsync >/dev/null 2>&1; then
  rsync -av --delete "${RSYNC_EXCLUDES[@]}" \
    -e "$SSH_BIN ${SSH_OPTIONS[*]}" \
    "$ROOT_DIR/" "$TARGET:$REMOTE_DIR/"
  status=$?
else
  printf '%s\n' '[openframetap] Local rsync unavailable; using tar-over-SSH fallback with the same exclusions.'
  "$SSH_BIN" "${SSH_OPTIONS[@]}" "$TARGET" \
    "set -eu; mkdir -p $REMOTE_DIR/.deploy-stage $REMOTE_DIR/artifacts; find $REMOTE_DIR/.deploy-stage -mindepth 1 -maxdepth 1 -exec rm -rf -- {} +"
  (cd "$ROOT_DIR" && git ls-files --cached --others --exclude-standard | \
    tar "${TAR_EXCLUDES[@]}" -cf - -T -) | \
    "$SSH_BIN" "${SSH_OPTIONS[@]}" "$TARGET" \
      "set -eu; tar -xf - -C $REMOTE_DIR/.deploy-stage; cd $REMOTE_DIR; find . -mindepth 1 -maxdepth 1 ! -name .venv ! -name artifacts ! -name runtime ! -name .deploy-stage -exec rm -rf -- {} +; cp -a .deploy-stage/. .; rm -rf .deploy-stage"
  status=$?
fi

printf '[openframetap] Deploy exit status: %s\n' "$status"
exit "$status"
