#!/bin/bash
# daily_screen.sh — HOST script that calls Claude Code CLI for LLM-driven screening.
#
# Called by host cron at 06:35 KST (after container mechanical filter at 06:30).
# Reads pending_screen.json, passes prompt to claude CLI, writes screened_tickers.json.
#
# Prerequisites:
#   - Claude Code CLI installed (Node.js on host)
#   - Max subscription (no per-call cost)
#   - data/ directory shared between host and container
set -euo pipefail

TRADING_DIR="/home/onigunsow/trading"
PENDING="$TRADING_DIR/data/pending_screen.json"
RESULTS="$TRADING_DIR/data/screened_tickers.json"
CLAUDE="/home/onigunsow/.nvm/versions/node/v24.13.0/bin/claude"
LOG="$TRADING_DIR/logs/daily_screen.log"

log() {
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] $1" >> "$LOG"
}

log "Starting LLM screening"

# Check if pending file exists and is non-empty
if [ ! -f "$PENDING" ] || [ ! -s "$PENDING" ]; then
    log "No pending screen data (file missing or empty)"
    exit 0
fi

# Extract the prompt text from JSON to a temp file (avoids ARG_MAX limits)
PROMPT_FILE=$(mktemp /tmp/claude_screen_XXXXXX.txt)
trap 'rm -f "$PROMPT_FILE"' EXIT

python3 -c "
import json, sys
with open('$PENDING') as f:
    data = json.load(f)
with open('$PROMPT_FILE', 'w') as out:
    out.write(data['prompt'])
" 2>>"$LOG"

if [ ! -s "$PROMPT_FILE" ]; then
    log "ERROR: Failed to extract prompt from pending file"
    exit 1
fi

PROMPT_LINES=$(wc -l < "$PROMPT_FILE")
log "Sending $PROMPT_LINES lines to Claude CLI for screening"

# Call Claude Code CLI with the screening prompt via stdin pipe
# -p: non-interactive print mode
# --tools "": disable all tools (pure text analysis, no file operations)
RESPONSE=$(cat "$PROMPT_FILE" | $CLAUDE -p --tools "" 2>>"$LOG")
EXIT_CODE=$?

if [ $EXIT_CODE -eq 0 ] && [ -n "$RESPONSE" ]; then
    echo "$RESPONSE" > "$RESULTS"
    rm -f "$PENDING"
    log "LLM screening complete — results written to $RESULTS ($(echo "$RESPONSE" | wc -c) bytes)"
else
    log "Claude Code failed (exit=$EXIT_CODE), creating fallback from mechanical results"
    # Fallback: extract top 20 from mechanical candidates
    python3 -c "
import json
d = json.load(open('$PENDING'))
fallback = [
    {'ticker': c['ticker'], 'name': c.get('name', ''), 'reason': 'mechanical filter'}
    for c in d.get('candidates', [])[:20]
]
json.dump(fallback, open('$RESULTS', 'w'), ensure_ascii=False, indent=2)
" 2>>"$LOG"
    rm -f "$PENDING"
    log "Fallback screening written (mechanical top 20)"
fi
