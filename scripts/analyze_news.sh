#!/bin/bash
# analyze_news.sh — HOST script that calls Claude Code CLI to analyze pending articles.
#
# Called by host cron (not inside Docker). Reads pending_analysis.json from shared
# volume, passes the prompt to claude CLI, writes analysis_results.json.
#
# Prerequisites:
#   - Claude Code CLI installed (Node.js on host)
#   - Max subscription (no per-call cost)
#   - data/ directory shared between host and container
set -euo pipefail

TRADING_DIR="/home/onigunsow/trading"
PENDING="$TRADING_DIR/data/pending_analysis.json"
RESULTS="$TRADING_DIR/data/analysis_results.json"
CLAUDE="/home/onigunsow/.nvm/versions/node/v24.13.0/bin/claude"
LOG="$TRADING_DIR/logs/analyze_news.log"

log() {
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] $1" >> "$LOG"
}

log "Starting analysis run"

# Check if pending file exists and is non-empty
if [ ! -f "$PENDING" ] || [ ! -s "$PENDING" ]; then
    log "No pending articles (file missing or empty)"
    exit 0
fi

# Extract the prompt text from JSON to a temp file (avoids ARG_MAX limits)
PROMPT_FILE=$(mktemp /tmp/claude_prompt_XXXXXX.txt)
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
log "Sending $PROMPT_LINES lines to Claude CLI"

# Call Claude Code CLI with the analysis prompt via stdin pipe
# -p: non-interactive print mode (reads prompt from stdin when no arg given)
# --tools "": disable all tools (pure text analysis, no file operations)
RESPONSE=$(cat "$PROMPT_FILE" | $CLAUDE -p --tools "" 2>>"$LOG")
EXIT_CODE=$?

if [ $EXIT_CODE -eq 0 ] && [ -n "$RESPONSE" ]; then
    # Write response to results file
    echo "$RESPONSE" > "$RESULTS"
    # Remove pending file to signal completion
    rm -f "$PENDING"
    log "Analysis complete — results written to $RESULTS ($(echo "$RESPONSE" | wc -c) bytes)"
else
    log "ERROR: Claude CLI failed (exit=$EXIT_CODE), keeping pending file for retry"
    exit 1
fi
