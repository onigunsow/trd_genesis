#!/bin/bash
# stop_watcher.sh — Stop persona watcher gracefully.
set -uo pipefail

TRADING_DIR="/home/onigunsow/trading"
PID_FILE="$TRADING_DIR/data/persona_watcher.pid"
HEARTBEAT_FILE="$TRADING_DIR/data/persona_watcher.heartbeat"

if [ ! -f "$PID_FILE" ]; then
    echo "No PID file found — watcher may not be running"
    exit 0
fi

PID=$(cat "$PID_FILE")

if kill -0 "$PID" 2>/dev/null; then
    echo "Stopping persona watcher (PID $PID)..."
    kill "$PID"
    # Wait up to 5 seconds for graceful shutdown
    for i in $(seq 1 5); do
        if ! kill -0 "$PID" 2>/dev/null; then
            break
        fi
        sleep 1
    done
    # Force kill if still running
    if kill -0 "$PID" 2>/dev/null; then
        kill -9 "$PID" 2>/dev/null
    fi
    echo "Persona watcher stopped"
else
    echo "Watcher process $PID not found (already stopped)"
fi

rm -f "$PID_FILE"
rm -f "$HEARTBEAT_FILE"
