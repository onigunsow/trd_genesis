#!/bin/bash
# probe_quality_fundamentals.sh — HOST 일회성: SPEC-059 퀄리티 팩터 데이터 가용성 probe.
#
# 호스트 cron "42 9 26 6 *"(2026-06-26 09:42 KST 장중)에서 1회 실행 후 자가 제거.
# probe 본체(.py)를 컨테이너 stdin 으로 파이프 → JSON 회수 → 텔레그램 보고.
# 읽기 전용(DB 미접촉). KRX 안정 장중에만 의미.
set -uo pipefail

TRADING_DIR="/home/onigunsow/trading"
LOG="$TRADING_DIR/logs/probe_quality_fundamentals.log"
PY="$TRADING_DIR/scripts/probe_quality_fundamentals.py"

log() { echo "[$(date '+%F %T')] $1" >> "$LOG"; }

log "SPEC-059 펀더멘털 probe 시작"
result=$(docker exec -i trading-app python - < "$PY" 2>>"$LOG")
log "결과 JSON: $result"

# 텔레그램 요약 보고 (메시지를 환경변수로 안전 전달)
SUMMARY="SPEC-059 펀더멘털 probe 결과 — 생존편향-free 퀄리티 데이터 가용성:
$result"
MSG="$SUMMARY" docker exec -e MSG trading-app python -c \
    "import os; from trading.alerts import telegram as t; t.system_briefing('SPEC-059', os.environ['MSG'])" \
    >>"$LOG" 2>&1 || true

# 일회성 — crontab 라인 자가 제거
( crontab -l 2>/dev/null | grep -v 'probe_quality_fundamentals.sh' ) | crontab - 2>>"$LOG" || true
log "probe 종료 (crontab 라인 제거)"
