#!/bin/bash
# persona_watcher.sh — Host-side watcher for CLI persona execution.
#
# SPEC-015 REQ-RUNNER-03-*: Monitors data/persona_calls/ for prompt files,
# executes claude -p --max-turns 1, writes results to data/persona_results/.
#
# Prerequisites:
#   - Claude Code CLI installed (Node.js on host)
#   - Max subscription (no per-call cost)
#   - data/ directory shared between host and container
#
# Usage:
#   bash scripts/persona_watcher.sh          # foreground
#   bash scripts/start_watcher.sh            # background with PID tracking
set -uo pipefail

TRADING_DIR="/home/onigunsow/trading"
CALLS_DIR="$TRADING_DIR/data/persona_calls"
RESULTS_DIR="$TRADING_DIR/data/persona_results"
HEARTBEAT_FILE="$TRADING_DIR/data/persona_watcher.heartbeat"
CLAUDE="/home/onigunsow/.nvm/versions/node/v24.13.0/bin/claude"
LOG="$TRADING_DIR/logs/persona_watcher.log"

# REQ-RUNNER-03-5: Poll interval (seconds)
POLL_INTERVAL=2
# REQ-SCHED-07-4: Heartbeat update interval (seconds)
HEARTBEAT_INTERVAL=30

mkdir -p "$CALLS_DIR" "$RESULTS_DIR" "$(dirname "$LOG")"

log() {
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] $1" >> "$LOG"
}

update_heartbeat() {
    touch "$HEARTBEAT_FILE"
}

log "=== Persona watcher started (PID $$) ==="
update_heartbeat
LAST_HEARTBEAT=$(date +%s)

while true; do
    # REQ-SCHED-07-4: Update heartbeat every 30 seconds
    NOW=$(date +%s)
    if (( NOW - LAST_HEARTBEAT >= HEARTBEAT_INTERVAL )); then
        update_heartbeat
        LAST_HEARTBEAT=$NOW
    fi

    # REQ-RUNNER-03-5: Process files in FIFO order (sorted by filename timestamp)
    for call_file in $(ls -1 "$CALLS_DIR"/*.json 2>/dev/null | sort); do
        [ -f "$call_file" ] || continue

        BASENAME=$(basename "$call_file")
        RESULT_FILE="$RESULTS_DIR/$BASENAME"

        # Extract prompt from call file
        PROMPT=$(python3 -c "
import json, sys
try:
    with open('$call_file') as f:
        data = json.load(f)
    print(data['prompt'])
except Exception as e:
    print(f'ERROR: {e}', file=sys.stderr)
    sys.exit(1)
" 2>>"$LOG")

        if [ $? -ne 0 ] || [ -z "$PROMPT" ]; then
            log "ERROR: empty/invalid prompt from $BASENAME"
            # REQ-RUNNER-03-3: Write error result
            echo "{\"error\": \"invalid_prompt\", \"exit_code\": 1, \"persona\": \"unknown\", \"timestamp\": \"$(date -Iseconds)\", \"response_text\": \"\", \"execution_seconds\": 0}" > "$RESULT_FILE"
            # REQ-RUNNER-03-7: Remove call file after processing
            rm -f "$call_file"
            continue
        fi

        # Extract persona name for logging
        PERSONA=$(python3 -c "import json; print(json.load(open('$call_file')).get('persona','unknown'))" 2>/dev/null || echo "unknown")

        log "Processing $PERSONA ($BASENAME)"
        START_TIME=$(date +%s%N)

        # REQ-RUNNER-03-2: Pipe prompt to claude -p --max-turns 1
        # Write prompt to temp file to avoid ARG_MAX limits
        PROMPT_FILE=$(mktemp /tmp/persona_prompt_XXXXXX.txt)
        echo "$PROMPT" > "$PROMPT_FILE"

        RESPONSE=$(cat "$PROMPT_FILE" | $CLAUDE -p --max-turns 1 2>>"$LOG")
        EXIT_CODE=$?
        rm -f "$PROMPT_FILE"

        END_TIME=$(date +%s%N)
        EXEC_MS=$(( (END_TIME - START_TIME) / 1000000 ))
        EXEC_SECONDS=$(echo "scale=1; $EXEC_MS / 1000" | bc 2>/dev/null || echo "0")

        if [ $EXIT_CODE -eq 0 ] && [ -n "$RESPONSE" ]; then
            # S-2: Result file JSON schema
            python3 -c "
import json, sys
response_text = sys.stdin.read()
result = {
    'persona': '$PERSONA',
    'timestamp': '$(date -Iseconds)',
    'response_text': response_text,
    'execution_seconds': float('$EXEC_SECONDS'),
    'exit_code': 0,
    'error': None,
}
with open('$RESULT_FILE', 'w') as f:
    json.dump(result, f, ensure_ascii=False)
" <<< "$RESPONSE" 2>>"$LOG"

            # REQ-RUNNER-03-7: Remove call file after writing result
            rm -f "$call_file"
            RESULT_BYTES=$(wc -c < "$RESULT_FILE" 2>/dev/null || echo "0")
            log "Done $PERSONA -- ${EXEC_SECONDS}s, ${RESULT_BYTES} bytes"
        else
            # REQ-RUNNER-03-3: Write error result on CLI failure
            log "FAILED $PERSONA (exit=$EXIT_CODE)"
            python3 -c "
import json
result = {
    'persona': '$PERSONA',
    'timestamp': '$(date -Iseconds)',
    'response_text': '',
    'execution_seconds': float('$EXEC_SECONDS'),
    'exit_code': $EXIT_CODE,
    'error': 'cli_failed (exit=$EXIT_CODE)',
}
with open('$RESULT_FILE', 'w') as f:
    json.dump(result, f, ensure_ascii=False)
" 2>>"$LOG"
            # REQ-RUNNER-03-7: Remove call file
            rm -f "$call_file"
        fi

        # Update heartbeat after each processed file
        update_heartbeat
        LAST_HEARTBEAT=$(date +%s)
    done

    sleep $POLL_INTERVAL
done
