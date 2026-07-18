#!/usr/bin/env bash
set -u
output_dir=""
previous=""
for argument in "$@"; do
  if [[ "$previous" == "--output" ]]; then
    output_dir="$argument"
    break
  fi
  previous="$argument"
done
[[ -n "$output_dir" ]] || exit 42
mkdir -p "$output_dir"
: >"$output_dir/notifications.jsonl"
: >"$output_dir/duml-frames.jsonl"
: >"$output_dir/events.jsonl"
: >"$output_dir/unknown-frames.jsonl"
trap '
  printf "%s\n" "{\"fff5_write_count\":0,\"saved_after_interrupt\":true}" >"$output_dir/session.json"
  printf "%s\n" "{\"notifications_received\":0}" >"$output_dir/message-counts.json"
  exit 130
' INT
kill -INT "$$"
sleep 1
exit 99
