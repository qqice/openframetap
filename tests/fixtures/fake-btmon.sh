#!/usr/bin/env bash
set -u
trap 'printf stopped >"${BTMON_STOP_FILE:?}"; exit 0' INT TERM
if [[ "${1:-}" == "-w" && -n "${2:-}" ]]; then
  : >"$2"
fi
printf started >"${BTMON_START_FILE:?}"
while :; do
  sleep 1
done
