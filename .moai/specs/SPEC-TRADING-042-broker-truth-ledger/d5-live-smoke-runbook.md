# D5 — live 체결조회(TTTC8001R) 스모크 게이트 Runbook

작성 2026-06-21 · 기준 789163a · 상태: **미실행(준비 완료)**

## 이게 뭐고, 뭐가 아닌가
- **목적**: SPEC-042 D5 — 라이브 체결조회 TR `TTTC8001R`가 실제 작동하는지 1주 매수→매도 round-trip으로 검증(`smoke_gate.py` 5항목 중 (e) tr_id_field_compatible 실증). 통과 시 `SMOKE_GATE_PASS` 기록 → D5 해소.
- **아님**: 실매매 개시가 아님. 실거래는 별도로 **음수 엣지**(OOS NO-GO)에 막혀 있고 D5 통과로 풀리지 않음. 이건 "배관 점검"이다.
- **비용**: 실제 돈. 저가 1주(예: 1주 수천~수만원) + 왕복 수수료. 손익 무관(실행 정합만 검증).
- **시점**: KRX 장중(평일 09:00~15:30)만. 휴장일/장외엔 주문 거부.

## 사전 점검 (이미 확인됨, 2026-06-21)
- ✅ live 자격증명 설정됨: `KIS_LIVE_ACCOUNT/APP_KEY/APP_SECRET`.
- ✅ `trading smoke-gate --max-qty N` 명령 존재(`cli.py:526`). `--max-qty` 필수(0=차단), 옵션 `--max-notional`.
- ✅ 이중 가드: 발주는 **trading_mode=live AND live_unlocked=True**일 때만(`cli.py:639`, `order.py` live_unlocked 게이트). paper거나 잠금이면 무발주 종료.
- ✅ 현재 안전 기본값: `trading_mode=paper`, `live_unlocked=false`, `halt_state=false`.

## 실행 단계 (운영자, 장중 ~5분) — 각 단계 확인 후 진행
> ⚠️ 전부 함께 진행 권장(특히 5번 원복 누락 금지). 명령은 호스트 `~/trading`에서 `!` 접두사로 실행 가능.

**0. 테스트 종목 선택** (저가·고유동, 1주 부담 최소)
   - 후보는 실행 직전 현재가로 정함. 보유 중 저가 종목 또는 KODEX 200 류 ETF 1주(수천~1만원대)면 비용 최소.

**1. live 모드 전환** (env + 재배포)
   - `TRADING_MODE=live`로 변경(`.env` 파일 — 정확한 위치는 실행 시 함께 확인) 후:
     ```bash
     cd ~/trading && make redeploy
     docker exec trading-app python -c "from trading.config import get_settings; print(get_settings().trading_mode)"   # → TradingMode.LIVE 확인
     ```

**2. live_unlocked 해제** (의도적 DB 직접 — CLI 없음)
   ```bash
   docker exec trading-postgres psql -U trading -d trading -c "UPDATE system_state SET live_unlocked=true, updated_by='operator-d5' WHERE id=1;"
   docker exec trading-postgres psql -U trading -d trading -tAc "SELECT trading_mode, live_unlocked FROM system_state WHERE id=1;"   # live|t 확인
   ```

**3. 스모크 게이트 실행** (실제 1주 매수→매도)
   ```bash
   docker exec trading-app trading smoke-gate --max-qty 1
   ```
   - 정직 고지 출력 후 1주 BUY→SELL, 체결 확인(live TR), 5항목 판정.

**4. 결과 확인** (PASS 기록)
   ```bash
   docker exec trading-postgres psql -U trading -d trading -c "SELECT ts, event_type, details->>'reasons' FROM audit_log WHERE event_type IN ('SMOKE_GATE_PASS','SMOKE_GATE_FAIL') ORDER BY ts DESC LIMIT 1;"
   ```
   - `SMOKE_GATE_PASS` → D5 해소. `SMOKE_GATE_FAIL`이면 reasons로 (e) tr_id 항목 원인 분석(TTTC8001R/필드명 보정 필요할 수 있음).

**5. ★원복 (필수 — 누락 금지)**
   ```bash
   docker exec trading-postgres psql -U trading -d trading -c "UPDATE system_state SET live_unlocked=false, updated_by='operator-d5-revert' WHERE id=1;"
   ```
   - 그리고 `TRADING_MODE=paper`로 되돌린 뒤 `make redeploy`. 최종 `paper|f` 확인.
   - 보유하게 된 잔량 정리(매도 미체결 시 다음 사이클/워치독이 처리, 또는 수동).

## 실패/안전
- 장외·휴장 실행 시 주문 거부(무해). halt_state=true면 먼저 `trading resume` 필요.
- 어느 단계든 멈추면 **5번 원복부터** 실행해 paper/잠금 안전상태로 복귀.
- FAIL은 `SMOKE_GATE_PASS`를 가리지 않음(audit append-only). 원인 보정 후 재시도 가능.

## 권고
- 엣지가 음수인 현 시점엔 **선택 사항**(배관 점검). 실매매 임박 시 실행이 자연스러움. 지금 할지/미룰지는 운영자 판단.
