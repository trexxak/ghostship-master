#!/usr/bin/env bash
set -euo pipefail

: "${TICK_LOOP_INTERVAL:=45}"
: "${TICK_LOOP_JITTER:=10}"

while true; do
  python forum_simulator/manage.py run_tick --origin "docker-loop" || true
  sleep_time=$TICK_LOOP_INTERVAL
  if [ "$TICK_LOOP_JITTER" -gt 0 ]; then
    jitter=$(( RANDOM % (TICK_LOOP_JITTER + 1) ))
    sleep_time=$(( TICK_LOOP_INTERVAL + jitter ))
  fi
  sleep "$sleep_time"
done
