#!/bin/bash
# start_watcher.sh — Start persona watcher in background with PID tracking.
#
# SPEC-015 REQ-RUNNER-03-6: Supports auto-restart via systemd or tmux.
#
# Usage:
#   bash scripts/start_watcher.sh          # start in background
#   bash scripts/start_watcher.sh --check  # check if running
set -uo pipefail

TRADING_DIR="/home/onigunsow/trading"
PID_FILE="$TRADING_DIR/data/persona_watcher.pid"
WATCHER_SCRIPT="$TRADING_DIR/scripts/persona_watcher.sh"
LOG="$TRADING_DIR/logs/persona_watcher.log"

mkdir -p "$(dirname "$PID_FILE")" "$(dirname "$LOG")"

# Check if already running
if [ -f "$PID_FILE" ]; then
    OLD_PID=$(cat "$PID_FILE")
    if kill -0 "$OLD_PID" 2>/dev/null; then
        if [ "${1:-}" = "--check" ]; then
            echo "Persona watcher is running (PID $OLD_PID)"
            exit 0
        fi
        echo "Persona watcher already running (PID $OLD_PID)"
        exit 0
    else
        echo "Stale PID file found (PID $OLD_PID not running), cleaning up"
        rm -f "$PID_FILE"
    fi
fi

if [ "${1:-}" = "--check" ]; then
    echo "Persona watcher is NOT running"
    exit 1
fi

# Start watcher in background
nohup bash "$WATCHER_SCRIPT" >> "$LOG" 2>&1 &
echo $! > "$PID_FILE"
echo "Persona watcher started (PID $(cat "$PID_FILE"))"
echo "Log: $LOG"
