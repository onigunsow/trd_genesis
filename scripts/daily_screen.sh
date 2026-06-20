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
LOG="$TRADING_DIR/logs/daily_screen.log"

# REQ-053-A1: claude CLI 경로 견고 해소 — (1) command -v, (2) .local/bin 폴백
# REQ-053-A2: 어느 쪽도 없으면 ERROR 로그 후 non-zero 종료(유료 API 경로 미발동)
CLAUDE="$(command -v claude 2>/dev/null || true)"
[ -x "$CLAUDE" ] || CLAUDE="/home/onigunsow/.local/bin/claude"
if [ ! -x "$CLAUDE" ]; then
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] ERROR: claude CLI binary not found (command -v / .local/bin) — aborting, NO paid API" >> "$LOG"
    exit 1
fi

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
    # REQ-053-A4: 기계적 폴백(top-20) 보존 — 유료 API가 아닌 순수 로컬 폴백
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
