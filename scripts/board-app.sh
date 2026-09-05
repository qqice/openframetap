#!/usr/bin/env bash
# Native ROCK 4D entry point: no SSH and no Windows dependency.
set -euo pipefail
cd "$(dirname "$(readlink -f "$0")")/.."
export OPENFRAMETAP_GIT_HEAD="$(git rev-parse HEAD)"
case "${1:-start}" in
  start)
    mkdir -p runtime
    exec 9>runtime/desktop-launch.lock
    flock -n 9 || exit 0
    if .venv/bin/python -m openframetap app --status | grep -q '"state": "running"'; then
      exit 0
    fi
    .venv/bin/python -m openframetap app --background --session-mode normal \
      --control-mode live --duration 0
    ;;
  stop|status) .venv/bin/python -m openframetap app --"$1" ;;
  test) .venv/bin/python -m pytest -q ;;
  *) echo 'Usage: board-app.sh [start|stop|status|test]' >&2; exit 2 ;;
esac
