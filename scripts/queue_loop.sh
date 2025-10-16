#!/usr/bin/env bash
set -euo pipefail

: "${QUEUE_LOOP_INTERVAL:=60}"

while true; do
  python forum_simulator/manage.py process_generation_queue --limit "${QUEUE_LOOP_LIMIT:=12}" || true
  sleep "$QUEUE_LOOP_INTERVAL"
done
