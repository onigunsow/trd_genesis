#!/bin/bash
# backfill_missing_ohlcv.sh — HOST 일회성: 2026-06-25 누락 OHLCV 장중 백필.
#
# 배경: 2026-06-25 16:00 일봉 갱신이 KRX 장애로 0/20 실패 → DB에 06-25 OHLCV 결손.
#       KRX 로그인은 장중(09:00~15:30 KST)에만 안정적이므로 장중에 1회 백필.
# 호출: 호스트 cron "13 10 26 6 *" (2026-06-26 10:13 KST 1회). 실행 후 자기 자신
#       crontab 라인 제거(true one-shot).
# 원칙: 모든 트레이딩 실행은 컨테이너 안에서(docker exec). 호스트 직접 실행 금지
#       (DB 미접근 hang). 백필은 읽기전용 KRX 조회 + 멱등 upsert → 매매 미발생.
set -uo pipefail

TRADING_DIR="/home/onigunsow/trading"
LOG="$TRADING_DIR/logs/backfill_ohlcv.log"
TARGET="2026-06-25"

log() { echo "[$(date '+%F %T')] $1" >> "$LOG"; }

remove_self_cron() {
    ( crontab -l 2>/dev/null | grep -v 'backfill_missing_ohlcv.sh' ) | crontab - 2>>"$LOG" || true
}

notify() {
    # 텔레그램 보고 — 메시지를 환경변수로 안전 전달(따옴표/특수문자 회피)
    MSG="$1" docker exec -e MSG trading-app python -c \
        "import os; from trading.alerts import telegram as t; t.system_briefing('DATA', os.environ['MSG'])" \
        >>"$LOG" 2>&1 || true
}

log "백필 probe 시작 (target=$TARGET)"

# 1) 이미 채워졌으면 no-op (멱등)
existing=$(docker exec trading-postgres psql -U trading -d trading -t -A -c \
    "SELECT count(*) FROM ohlcv WHERE ts::date='$TARGET';" 2>>"$LOG" | tr -d '[:space:]')
if [ "${existing:-0}" -gt 0 ]; then
    log "이미 $TARGET OHLCV $existing 행 존재 — 백필 생략"
    notify "06-25 OHLCV 이미 ${existing} 행 존재 — 백필 불필요(자동 정상화됨)."
    remove_self_cron
    exit 0
fi

# 2) 장중 백필 (refresh_ohlcv + refresh_flows; 마지막 캐시+1 → today 증분)
log "백필 실행 (docker exec refresh_ohlcv/refresh_flows)"
result=$(docker exec trading-app python -c "
from trading.scripts import refresh_market_data as r
o = r.refresh_ohlcv()
f = r.refresh_flows()
print(f\"ohlcv ok={o['success_count']}/{o['total_tickers']} rows={o['total_rows_upserted']} | \"
      f\"flows ok={f['success_count']}/{f['total_tickers']} rows={f['total_rows_upserted']}\")
" 2>>"$LOG")
log "결과: $result"

# 3) 검증
after=$(docker exec trading-postgres psql -U trading -d trading -t -A -c \
    "SELECT count(*) FROM ohlcv WHERE ts::date='$TARGET';" 2>>"$LOG" | tr -d '[:space:]')
log "백필 후 $TARGET 행 수: ${after:-0}"

# 4) 보고 + 자가 제거(일회성)
if [ "${after:-0}" -gt 0 ]; then
    notify "06-25 OHLCV 장중 백필 완료 — ${after} 행 복구. (${result})"
    log "성공 — crontab 라인 제거"
else
    notify "06-25 OHLCV 백필 실패 — 장중에도 KRX 불안정 가능. logs/backfill_ohlcv.log 확인, 다음 장중 재시도 검토 필요. (${result})"
    log "실패 — KRX 장중 불안정 가능"
fi
remove_self_cron
log "probe 종료"
