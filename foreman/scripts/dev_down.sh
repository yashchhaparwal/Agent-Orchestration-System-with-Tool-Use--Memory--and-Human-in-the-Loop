#!/usr/bin/env bash
# Stop the app processes started by dev_up.sh (Docker services keep running).
cd "$(dirname "$0")/.."
[ -f .run/pids.txt ] && { xargs -r kill < .run/pids.txt 2>/dev/null || true; rm -f .run/pids.txt .run/*.pid; }
pkill -f "orchestrator.worker" 2>/dev/null || true
echo "app processes stopped; Docker services still up (make down to stop them)"
