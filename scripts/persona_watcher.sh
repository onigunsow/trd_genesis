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

# REQ-053-E1: flock 단일 인스턴스 가드 — 고아 이전 인스턴스의 중복 처리 방지.
# REQ-053-E2: 두 번째 인스턴스는 즉시 exit 0 (systemd Restart 루프 방지, E3).
# 락 FD는 프로세스 종료 시 자동 해제 → 정상 재시작 시 새 인스턴스가 즉시 획득.
exec 200>/tmp/persona_watcher.lock
flock -n 200 || { echo "[$(date '+%Y-%m-%d %H:%M:%S')] persona_watcher already running — exiting" >&2; exit 0; }

TRADING_DIR="/home/onigunsow/trading"
CALLS_DIR="$TRADING_DIR/data/persona_calls"
RESULTS_DIR="$TRADING_DIR/data/persona_results"
HEARTBEAT_FILE="$TRADING_DIR/data/persona_watcher.heartbeat"
CLAUDE="/home/onigunsow/.local/bin/claude"
LOG="$TRADING_DIR/logs/persona_watcher.log"

# REQ-RUNNER-03-5: Poll interval (seconds)
POLL_INTERVAL=2
# REQ-SCHED-07-4: Heartbeat update interval (seconds)
HEARTBEAT_INTERVAL=30
# SPEC-052 후속: CLI 경화 — 타임아웃·재시도 설정.
# 성공 호출 최대 ~189s(micro/macro) → 300s 여유 마진.
CLAUDE_TIMEOUT=300
MAX_ATTEMPTS=2

mkdir -p "$CALLS_DIR" "$RESULTS_DIR" "$(dirname "$LOG")"

log() {
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] $1" >> "$LOG"
}

update_heartbeat() {
    touch "$HEARTBEAT_FILE"
}

# SPEC-052 후속: CLI 경화 — 빈응답 발생 시 운영자 텔레그램 소프트 알림 (6h 쓰로틀).
# 인증 만료와 달리 장초반 부하성 빈응답은 SPEC-052 latch(3연속) 전에는 텔레그램 무음.
# 이 함수로 단건 실패도 운영자에게 전달 (크레딧 누수 없음, strict 가드 유지).
send_persona_soft_alert() {
    local persona="${1:-unknown}"
    local exit_code="${2:-1}"
    local state_file="$TRADING_DIR/data/persona_empty_alert.state"
    local now
    now=$(date +%s)

    # 6h 쓰로틀 체크 (21600초)
    if [ -f "$state_file" ]; then
        local last_alert
        last_alert=$(cat "$state_file" 2>/dev/null || echo "0")
        # set -u 안전: ${last_alert:-0}
        last_alert="${last_alert:-0}"
        if (( now - last_alert < 21600 )); then
            return 0
        fi
    fi

    # .env에서 텔레그램 크레덴셜 파싱 (set -u 안전: ${VAR:-} 사용)
    local bot_token=""
    local chat_id=""
    if [ -f "$TRADING_DIR/.env" ]; then
        bot_token=$(grep -E '^TELEGRAM_BOT_TOKEN_TRADING=' "$TRADING_DIR/.env" 2>/dev/null \
            | head -1 | cut -d'=' -f2- | tr -d '"'"'"' ' 2>/dev/null || true)
        chat_id=$(grep -E '^TELEGRAM_CHAT_ID=' "$TRADING_DIR/.env" 2>/dev/null \
            | head -1 | cut -d'=' -f2- | tr -d '"'"'"' ' 2>/dev/null || true)
    fi

    # 크레덴셜 없으면 조용히 종료 (set -u 안전)
    if [ -z "${bot_token:-}" ] || [ -z "${chat_id:-}" ]; then
        return 0
    fi

    local msg="⚠️ 페르소나 CLI 빈응답 — ${persona} 재시도도 실패(exit=${exit_code}). 인증은 정상(토큰 자동갱신), 장초반 부하 의심. 크레딧 누수 0(strict 가드). 6h 쓰로틀."

    curl -s -m 10 \
        "https://api.telegram.org/bot${bot_token}/sendMessage" \
        --data-urlencode "chat_id=${chat_id}" \
        --data-urlencode "text=${msg}" \
        >> "$LOG" 2>&1 || true

    # 성공 여부와 무관하게 state 갱신 (과다 알림 방지)
    echo "$now" > "$state_file"
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

        # SPEC-052 후속: CLI 경화 — 프롬프트 파일은 루프 밖에서 생성, 재시도 시 재사용.
        # START_TIME은 루프 전에 기록 → EXEC_SECONDS = 전체 경과시간(재시도 포함).
        PROMPT_FILE=$(mktemp /tmp/persona_prompt_XXXXXX.txt)
        echo "$PROMPT" > "$PROMPT_FILE"

        START_TIME=$(date +%s%N)
        RESPONSE=""
        EXIT_CODE=1
        ATTEMPT=0

        while (( ATTEMPT < MAX_ATTEMPTS )); do
            ATTEMPT=$(( ATTEMPT + 1 ))

            # timeout 124 반환 시 재시도 대상 (실패 시도로 간주).
            # stdin redirect (<) 사용 — cat 파이프 대신 timeout이 프로세스 직접 소유.
            RESPONSE=$(timeout "${CLAUDE_TIMEOUT}s" "$CLAUDE" -p --max-turns 1 < "$PROMPT_FILE" 2>>"$LOG")
            EXIT_CODE=$?

            if [ "$EXIT_CODE" -eq 0 ] && [ -n "$RESPONSE" ]; then
                break  # 성공 — 루프 종료
            fi

            # 재시도 남은 경우 로그 + 대기
            if (( ATTEMPT < MAX_ATTEMPTS )); then
                log "RETRY $PERSONA — 빈응답/실패 (exit=$EXIT_CODE, len=${#RESPONSE}), 재시도"
                sleep 3
            fi
        done

        # 프롬프트 파일 제거 — 재시도 루프 종료 후
        rm -f "$PROMPT_FILE"

        END_TIME=$(date +%s%N)
        EXEC_MS=$(( (END_TIME - START_TIME) / 1000000 ))
        EXEC_SECONDS=$(echo "scale=1; $EXEC_MS / 1000" | bc 2>/dev/null || echo "0")

        if [ "$EXIT_CODE" -eq 0 ] && [ -n "$RESPONSE" ]; then
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
            # REQ-RUNNER-03-3: Write error result on CLI failure (양쪽 시도 모두 실패)
            # 2026-07-14 관측성: 버려지던 stdout 앞 300자를 로그에 남긴다 — 7/13
            # decision 종일 실패(exit=1, len=95 고정)의 정체가 무언의 exit=1로만
            # 기록돼 원인 확정이 불가능했다. len=95 고정 문자열의 실체를 포착한다.
            RESPONSE_HEAD=$(printf '%s' "$RESPONSE" | head -c 300 | tr '\n' ' ')
            log "FAILED $PERSONA (exit=$EXIT_CODE, len=${#RESPONSE}); stdout[:300]=${RESPONSE_HEAD:-<empty>}"
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

            # SPEC-052 후속: CLI 경화 — 양쪽 시도 실패 시 운영자 소프트 알림 (6h 쓰로틀).
            # SPEC-052 latch는 3연속 실패 기준 — 단건 실패는 이 경로로만 운영자 전달.
            send_persona_soft_alert "$PERSONA" "$EXIT_CODE"
        fi

        # Update heartbeat after each processed file
        update_heartbeat
        LAST_HEARTBEAT=$(date +%s)
    done

    sleep $POLL_INTERVAL
done
