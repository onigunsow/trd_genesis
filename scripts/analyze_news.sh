#!/bin/bash
# analyze_news.sh — HOST script that calls Claude Code CLI to analyze pending
# article chunks (SPEC-TRADING-062 Stage 2 — REQ-062-C2).
#
# Called by host cron (not inside Docker). Reads data/pending_chunks/chunk_*.json
# from the shared volume — each chunk holds at most HOST_CHUNK_SIZE articles
# (see analyzer.py) — calls `claude -p` ONCE PER CHUNK, and writes
# data/analysis_chunks/result_<chunk_id>.json. A chunk that fails or returns
# an empty response keeps its pending file for retry next slot; the loop
# CONTINUES with the remaining chunks (one bad chunk no longer blocks the
# rest of the batch — 2026-07-08/09 incident: a single 94-98 article batch
# scrambled almost 100% of the time and produced zero throughput all day).
#
# Prerequisites:
#   - Claude Code CLI installed (Node.js on host)
#   - Max subscription (no per-call cost)
#   - data/ directory shared between host and container
set -euo pipefail

TRADING_DIR="/home/onigunsow/trading"
CHUNKS_DIR="$TRADING_DIR/data/pending_chunks"
RESULTS_DIR="$TRADING_DIR/data/analysis_chunks"
LOG="$TRADING_DIR/logs/analyze_news.log"

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

log "Starting analysis run"

mkdir -p "$RESULTS_DIR"

# REQ-062-C2: 대기 중인 청크가 없으면 이번 슬롯은 할 일 없음.
# process substitution 이므로 find 실패(디렉터리 없음 등)가 set -e 를 건드리지 않는다.
mapfile -t CHUNK_FILES < <(find "$CHUNKS_DIR" -maxdepth 1 -name 'chunk_*.json' -type f 2>/dev/null | sort)

if [ ${#CHUNK_FILES[@]} -eq 0 ]; then
    log "No pending chunks (dir missing or empty)"
    exit 0
fi

CHUNKS_OK=0
CHUNKS_FAILED=0

for CHUNK_FILE in "${CHUNK_FILES[@]}"; do
    [ -s "$CHUNK_FILE" ] || { log "Skipping empty chunk file: $CHUNK_FILE"; continue; }

    CHUNK_BASE=$(basename "$CHUNK_FILE" .json)
    CHUNK_ID=${CHUNK_BASE#chunk_}
    RESULT_FILE="$RESULTS_DIR/result_${CHUNK_ID}.json"

    # Extract the prompt text from JSON to a temp file (avoids ARG_MAX limits)
    PROMPT_FILE=$(mktemp /tmp/claude_prompt_XXXXXX.txt)

    if ! python3 -c "
import json, sys
with open('$CHUNK_FILE') as f:
    data = json.load(f)
with open('$PROMPT_FILE', 'w') as out:
    out.write(data['prompt'])
" 2>>"$LOG"; then
        log "ERROR: Chunk $CHUNK_ID: failed to extract prompt — skipping (kept for retry)"
        rm -f "$PROMPT_FILE"
        CHUNKS_FAILED=$((CHUNKS_FAILED + 1))
        continue
    fi

    if [ ! -s "$PROMPT_FILE" ]; then
        log "ERROR: Chunk $CHUNK_ID: extracted prompt is empty — skipping (kept for retry)"
        rm -f "$PROMPT_FILE"
        CHUNKS_FAILED=$((CHUNKS_FAILED + 1))
        continue
    fi

    PROMPT_LINES=$(wc -l < "$PROMPT_FILE")
    log "Chunk $CHUNK_ID: sending $PROMPT_LINES lines to Claude CLI"

    # Call Claude Code CLI with the analysis prompt via stdin pipe
    # -p: non-interactive print mode (reads prompt from stdin when no arg given)
    # --tools "": disable all tools (pure text analysis, no file operations)
    #
    # Exit-code capture is deliberately done via `if VAR=$(...); then` rather
    # than a bare assignment followed by `$?` — under `set -e` + `pipefail`,
    # a bare `VAR=$(failing_cmd)` aborts the script immediately and the `$?`
    # line below it never runs. Wrapping it as an `if` condition is exempt
    # from `-e`, which is required here so one chunk's failure doesn't kill
    # the whole loop (REQ-062-C2: "loop CONTINUES with remaining chunks").
    if RESPONSE=$(cat "$PROMPT_FILE" | "$CLAUDE" -p --tools "" 2>>"$LOG"); then
        EXIT_CODE=0
    else
        EXIT_CODE=$?
    fi
    rm -f "$PROMPT_FILE"

    if [ "$EXIT_CODE" -eq 0 ] && [ -n "$RESPONSE" ]; then
        echo "$RESPONSE" > "$RESULT_FILE"
        rm -f "$CHUNK_FILE"
        log "Chunk $CHUNK_ID: analysis complete — results written to $RESULT_FILE ($(echo "$RESPONSE" | wc -c) bytes)"
        CHUNKS_OK=$((CHUNKS_OK + 1))
    elif [ "$EXIT_CODE" -eq 0 ]; then
        # exit=0 but empty response: transient (not a real failure).
        # Keep the chunk's pending file so the next cron slot retries it —
        # no data loss, and the other chunks are unaffected.
        log "WARN: Chunk $CHUNK_ID: Claude CLI returned empty response (exit=0) — transient, keeping pending file to retry next slot"
        CHUNKS_FAILED=$((CHUNKS_FAILED + 1))
    else
        # 2026-07-13 관측성: 실패 시 stdout(에러 메시지가 stdout으로 올 수 있음)
        # 앞부분을 남긴다 — 7/10 4시간 장애가 무언의 exit=1로만 기록돼 원인
        # (사용량 한도 의심) 확정이 불가능했다.
        RESPONSE_HEAD=$(printf '%s' "$RESPONSE" | head -c 200 | tr '\n' ' ')
        log "ERROR: Chunk $CHUNK_ID: Claude CLI failed (exit=$EXIT_CODE), keeping pending file for retry; stdout[:200]=${RESPONSE_HEAD:-<empty>}"
        CHUNKS_FAILED=$((CHUNKS_FAILED + 1))
    fi
done

log "Analysis run finished: $CHUNKS_OK chunk(s) ok, $CHUNKS_FAILED chunk(s) failed/retry"

if [ "$CHUNKS_OK" -eq 0 ] && [ "$CHUNKS_FAILED" -gt 0 ]; then
    exit 1
fi
exit 0
