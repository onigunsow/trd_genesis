---
id: SPEC-TRADING-029
type: acceptance
status: draft
created: 2026-05-26
---

# SPEC-TRADING-029 — Acceptance Criteria

## Definition of Done

본 SPEC 의 모든 phase (A → B → C → D[conditional] → E) 가 완료되고, Phase E
에서 실거래 paper KIS API 응답으로 오늘의 12 already-submitted orders 가
정확히 transition 되며, `positions` 테이블에 BUY 체결분이 가중평균 cost 로
UPSERT 되고, daily report "체결 N건" 메시지가 실제 체결 수를 반영하며, 기존
SPEC-022 / 023 / 025 / 026 의 동작이 회귀 없이 유지되는 시점.

---

## Acceptance Scenarios (Given / When / Then)

### AC-029-1 (REQ-029-1, REQ-029-2, REQ-029-3) — Paper 1주 BUY 체결 라이프사이클

- **Given**: paper 모드에서 ticker 086790 (하나금융지주) 1 주 BUY 시장가 주문이
  `submit_order()` 를 통해 KIS 에 제출되어 `orders.status='submitted'`, `kis_order_no='0000001977'`
  로 기록되어 있고, KIS 서버는 60 초 내 시장가 체결을 마쳤다
- **When**: 다음 fill_sync cron (60 초 후 발사) 이 KIS `inquire-daily-ccld` 를
  호출하여 응답에 해당 주문의 `tot_ccld_qty=1`, `ord_qty=1`, `avg_prvs=<fill_price>`
  가 포함된 row 가 도착하면
- **Then**:
  - `orders` 테이블의 해당 row 가 `status='filled'`, `fill_qty=1`,
    `fill_price=<KIS avg_prvs>`, `filled_at IS NOT NULL` 로 업데이트되어야 한다
  - `positions` 테이블에 `(ticker='086790', qty=1, avg_cost=<fill_price>,
    last_order_id=<orders.id>, last_updated=now())` row 가 INSERT 되어야 한다
  - `audit_log` 테이블에 `event_type='ORDER_FILLED'` event 와
    `event_type='POSITION_UPDATED'` event 가 모두 기록되어야 한다

### AC-029-2 (REQ-029-3) — 동일 ticker 연속 BUY 의 가중평균 avg_cost

- **Given**: paper 모드에서 ticker 005930 (삼성전자) 1 주 BUY 가 fill_price=70000
  으로 체결되어 `positions` 에 `(qty=1, avg_cost=70000)` row 가 존재한다
- **When**: 동일 ticker 의 두 번째 1 주 BUY 가 fill_price=80000 으로 체결되어
  apply_fill_to_position 이 ON CONFLICT UPDATE 를 수행하면
- **Then**:
  - `positions.qty` 가 2 가 되어야 한다
  - `positions.avg_cost` 가 `((1 * 70000) + (1 * 80000)) / (1 + 1) = 75000`
    (정수) 으로 계산되어야 한다
  - `last_order_id` 가 두 번째 주문의 orders.id 로 업데이트되어야 한다
  - `audit_log` 에 두 번째 POSITION_UPDATED event 가 기록되어야 한다

### AC-029-3 (REQ-029-2, REQ-029-3) — Partial fill 시나리오

- **Given**: paper 모드에서 ticker 281820 (하이닉스) 10 주 BUY 주문이 제출되어
  `orders.status='submitted', ord_qty=10` 로 기록되어 있다
- **When**: KIS `inquire-daily-ccld` 응답이 해당 주문의 `tot_ccld_qty=3`,
  `ord_qty=10`, `rmn_qty=7`, `cncl_yn='N'`, `rfus_yn='N'` 으로 도착하면
  (0 < tot_ccld_qty < ord_qty)
- **Then**:
  - `orders.status='partial'`, `fill_qty=3`, `fill_price=<KIS avg_prvs>` 로
    업데이트되어야 한다
  - `positions` 테이블에 `(ticker='281820', qty=3, avg_cost=<fill_price>)` row
    가 INSERT (또는 기존 row 가 있으면 UPSERT) 되어야 한다
  - `audit_log` 에 `event_type='ORDER_PARTIAL'` + `event_type='POSITION_UPDATED'`
    가 모두 기록되어야 한다

### AC-029-4 (REQ-029-3) — SELL 체결 시 qty 감소, avg_cost 보존

- **Given**: `positions` 에 `(ticker='086790', qty=2, avg_cost=50000,
  last_order_id=10)` row 가 존재한다
- **When**: ticker 086790 의 1 주 SELL 이 fill_price=55000 으로 체결되어
  apply_fill_to_position 이 호출되면
- **Then**:
  - `positions.qty` 가 1 로 감소해야 한다 (`GREATEST(2 - 1, 0)`)
  - `positions.avg_cost` 가 50000 그대로 유지되어야 한다 (변경 없음)
  - `last_order_id` 가 SELL 주문의 orders.id 로 업데이트되어야 한다
  - `audit_log` 에 POSITION_UPDATED event 가 기록되어야 한다 (side=sell, qty=1)

- **Given (추가)**: qty 가 1 인 상태에서 다시 1 주 SELL 이 체결되면
- **Then**: `positions.qty=0` 으로 업데이트되되 row 는 DELETE 되지 않고 유지되며,
  `avg_cost=50000` 도 그대로 보존되어야 한다 (ADR-029-4 보존 정책)

### AC-029-5 (REQ-029-1) — KIS rate limit 발생 시 기존 retry 가 처리

- **Given**: `inquire_fills_today` 호출 시점에 KIS 가 `EGW00201` (초당 거래건수
  초과) 응답을 반환한다
- **When**: 기존 `KisClient.get()` 의 rate-limit retry (`RATE_LIMIT_RETRIES=4`,
  exponential backoff) 가 자동 발동되면
- **Then**:
  - retry 4 회 안에 성공하면 fills 가 정상 파싱되어야 한다
  - retry 모두 실패하면 본 cycle 의 fill_sync 가 log 로 실패 기록 후 종료해야
    하며 (`_safe_call()` guard), 스케줄러는 crash 하지 않고 다음 60 초 cycle
    을 정상 발사해야 한다

### AC-029-6 (REQ-029-5) — `--dry-run` 첫 실행 시 DB 변경 없음

- **Given**: paper 모드에서 오늘의 10 already-submitted orders 가 DB 에 존재하고
  fill_sync 가 한 번도 실행되지 않은 상태이다
- **When**: 사용자가 `trading fill-sync --dry-run` CLI 명령을 실행하면
- **Then**:
  - 명령 종료 후 `orders` 테이블의 status / fill_qty / fill_price / filled_at
    필드가 실행 전과 동일해야 한다 (zero DB writes)
  - `positions` 테이블의 row 수와 내용이 실행 전과 동일해야 한다
  - `audit_log` 에 새로운 event 가 INSERT 되지 않아야 한다
  - stdout 에 intended transitions 가 출력되어야 한다 (예: `[DRY-RUN] orders.id=10
    submitted → filled (fill_qty=1, fill_price=53000)`)
  - exit code 0

### AC-029-7 (REQ-029-2) — KIS `cncl_yn='Y'` → cancelled

- **Given**: paper 모드에서 ticker 055550 (신한지주) 1 주 BUY 주문이 제출되어
  `orders.status='submitted'` 로 기록되어 있으나, 사용자가 KIS 웹포털에서
  해당 주문을 취소했다
- **When**: 다음 fill_sync 가 응답에서 해당 주문의 `tot_ccld_qty=0`,
  `cncl_yn='Y'` 를 발견하면
- **Then**:
  - `orders.status='cancelled'` 로 업데이트되어야 한다
  - `positions` 테이블에는 변경이 없어야 한다 (cancelled 는 position 업데이트
    트리거 아님)
  - `audit_log` 에 `event_type='ORDER_CANCELLED'` event 가 기록되어야 한다

### AC-029-8 (다운스트림 정합성) — daily report "체결 N건" 정확성

- **Given**: 본 SPEC 배포 후 paper 모드에서 1 영업일이 경과했고, 그 날 12 BUY
  주문 중 8 건이 filled, 1 건이 partial, 2 건이 rejected_by_kis, 1 건이
  cancelled 로 transition 되었다
- **When**: `reports/daily_report.py` 의 일일 리포트가 생성되면
- **Then**:
  - "체결 N건" 메시지가 정확히 9 건 (filled=8 + partial=1) 으로 표시되어야 한다
  - (이전 동작: submitted ∪ filled ∪ partial = 12 건으로 잘못 표시되던 것이
    수정됨)
  - SPEC-022/023 의 `data/universe.py` `SELECT DISTINCT ticker FROM positions
    WHERE qty > 0` 쿼리가 실제 보유 종목 list 를 반환해야 한다 (이전: 항상 빈
    list)

---

## Edge Cases

### EC-029-1 — Concurrent fill_sync invocations (cron + CLI race)

- **Given**: fill_sync cron 이 60 초 cycle 로 실행 중이고, 동시에 사용자가
  `trading fill-sync` CLI 를 manually 실행하여 두 프로세스가 같은 orders.id 를
  처리하려 한다
- **Then**: `SELECT ... FOR UPDATE` row lock 으로 인해 두 transition 이 직렬화
  되어야 하며, 한 transition 이 commit 된 후 두 번째 transition 은 이미
  `status != 'submitted'` 임을 발견하고 no-op 으로 skip 되어야 한다 (double
  transition 발생 안 함)

### EC-029-2 — Order with no `kis_order_no` (transport error pre-submission)

- **Given**: `orders` 테이블에 `status='error'`, `kis_order_no IS NULL` 인 row
  가 있다 (제출 자체가 실패한 case)
- **Then**: fill_sync 는 해당 row 를 처리 대상에서 제외해야 한다 (WHERE
  `kis_order_no IS NOT NULL` 필터)

### EC-029-3 — KIS returns row for unknown order_id (manual KIS web order)

- **Given**: KIS `inquire-daily-ccld` 응답에 로컬 `orders` 테이블에 존재하지
  않는 `odno` row 가 포함되어 있다 (예: 사용자가 KIS 웹포털에서 직접 주문)
- **Then**: fill_sync 는 해당 row 를 처리 대상에서 제외하고 log 에 warning 을
  기록해야 한다 (`[WARN] KIS fill row for unknown order: odno=<x>`). 스케줄러
  는 crash 하지 않고 나머지 fills 를 정상 처리해야 한다

### EC-029-4 — Already-filled order arrives again in next cycle

- **Given**: `orders.status='filled'`, `fill_qty=1`, `fill_price=70000` 인 row
  가 존재한다 (이전 cycle 에서 이미 transition 완료)
- **When**: 다음 cycle 의 `inquire-daily-ccld` 응답에 동일 주문이 다시
  포함되어 있다 (KIS 는 종일 모든 체결을 반환)
- **Then**: `apply_fill_to_order` 는 `SELECT FOR UPDATE` 후 `status='filled'`
  임을 확인하고 no-op (UPDATE 발생 안 함). `audit_log` 에 중복 event 가 INSERT
  되지 않아야 한다 (양분 transition 방지)

### EC-029-5 — Zero-qty positions row + new BUY arrives

- **Given**: `positions` 에 `(ticker='086790', qty=0, avg_cost=50000)` row 가
  존재한다 (이전 매수 → 전량 매도 후 보존된 상태)
- **When**: 동일 ticker 의 1 주 BUY 가 fill_price=60000 으로 체결되어 ON
  CONFLICT UPDATE 가 발동하면
- **Then**: 가중평균 분모가 `(0 + 1) = 1` 이므로 0 나눗셈은 발생하지 않고,
  `avg_cost = ((0 * 50000) + (1 * 60000)) / (0 + 1) = 60000` 으로 정확히 새
  fill_price 로 reset 되어야 한다. `qty=1` 로 업데이트.

### EC-029-6 — `INQR_END_DT` boundary at 15:30 KST

- **Given**: fill_sync cron 이 15:30 KST 시점에 발사된다 (KRX close)
- **When**: KIS `inquire-daily-ccld` 가 호출되면
- **Then**: 시장 closing 직전 / 직후 체결도 정상 응답에 포함되어야 한다.
  스케줄러의 KRX trading day guard 가 15:30 KST 까지 cron 발사를 허용해야 한다
  (`scheduler/runner.py` 기존 `_wrap()` 패턴 그대로 사용)

---

---

# v0.2.0 Acceptance Criteria (REDESIGN — balance reconcile + name + pct)

> AC-029-1 ~ AC-029-8 (위) 는 v0.1.0 의 inquire-daily-ccld 기반 시나리오로,
> 데이터 소스 전환에 따라 **AC-029-9 ~ AC-029-15 로 대체/보강** 된다.
> v0.1.0 AC 중 audit_log / FOR UPDATE / dry-run / EC-029-1·3·4 의 일반 동작은
> 여전히 유효하다. 아래는 v0.2.0 의 핵심 검증 항목.

## AC-029-9 (REQ-029-6, REQ-029-7) — balance reconcile 로 submitted → filled

- **Given**: paper 모드에서 ticker 005930 (삼성전자) 1 주 BUY 가
  `submit_order()` 로 제출되어 `orders.status='submitted'`,
  `kis_order_no IS NOT NULL`, `side='buy'`, `qty=1` 로 기록되어 있고,
  `positions` 에는 005930 행이 없거나 `qty=0` 이다 (already_accounted=0)
- **When**: 다음 fill-sync cycle 이 `balance(client)` 를 호출하여 holdings 에
  `(ticker='005930', qty=1, avg_cost=70000)` 가 포함되어 도착하면
- **Then**:
  - `orders` 해당 row 가 `status='filled'`, `fill_qty=1`, `fill_price=70000`,
    `filled_at IS NOT NULL` 로 업데이트되어야 한다
  - `audit_log` 에 `event_type='ORDER_FILLED'` event 가 기록되어야 한다
  - **`inquire-daily-ccld` 는 호출되지 않아야 한다** (REQ-029-6)

## AC-029-10 (REQ-029-7) — 동일 ticker 복수 submitted 주문 FIFO 배분

- **Given**: ticker 000660 에 submitted BUY 주문이 두 개 존재한다 —
  주문 A (`ts` 이른 것, `qty=3`), 주문 B (`ts` 늦은 것, `qty=5`).
  `positions` 의 000660 `already_accounted=0`
- **When**: fill-sync 가 balance holdings 에서 `(000660, held_qty=5)` 를 본다면
  (newly_filled=5)
- **Then**:
  - 주문 A (oldest-first) 가 먼저 `qty=3` 전량 배분 → `filled`, `fill_qty=3`
  - 주문 B 에 남은 `5-3=2` 가 배분 → `partial`, `fill_qty=2` (`ord_qty=5`)
  - 다음 cycle 에서 balance 가 `held_qty=8` 이 되면 (already_accounted=5,
    newly_filled=3) 주문 B 의 잔여 3 이 채워져 `partial → filled` 로 전이

## AC-029-11 (REQ-029-7) — newly_filled=0 이면 no-op

- **Given**: ticker 035420 의 submitted BUY 주문이 있고 balance holdings 의
  `held_qty` 가 `already_accounted` 와 동일하다 (newly_filled=0)
- **Then**: 해당 주문은 `submitted` 로 유지되고, `orders` UPDATE 도
  `audit_log` 도 발생하지 않아야 한다 (idempotent)

## AC-029-12 (REQ-029-8) — positions = balance mirror

- **Given**: `inquire-balance` 가 holdings `[(005930, qty=2, avg_cost=71000),
  (000660, qty=1, avg_cost=150000)]` 를 반환하고, `positions` 테이블에는
  이전에 보유했던 035720 (`qty=4`) 행이 남아 있다
- **When**: fill-sync 가 positions mirror 를 수행하면
- **Then**:
  - `positions` 의 005930 = `(qty=2, avg_cost=71000)`, 000660 =
    `(qty=1, avg_cost=150000)` 로 UPSERT 되어야 한다 (avg_cost 는 KIS
    `pchs_avg_pric` 그대로, 정수 가중평균 재계산 없음)
  - 035720 행은 balance 에 없으므로 `qty=0` 으로 업데이트되되 **DELETE 되지
    않아야** 한다 (ADR-029-4 보존)
  - `audit_log` 에 `event_type='POSITION_SYNCED'` event 가 기록되어야 한다

## AC-029-13 (REQ-029-9) — Telegram 매매 알림 종목명 표시

- **Given**: 매수 실행 후 `tg.trade_briefing` 이 호출되는 시점에 ticker
  005930 의 주문이 막 제출되어 아직 `inquire-balance` holdings 에 없을 수 있다
- **When**: orchestrator 가 `name=ticker_name("005930")` 을 전달하면
- **Then**:
  - 알림 메시지에 `005930 삼성전자 1주 매수 ...` 와 같이 종목명이 표시되어야
    한다 (`name=None` 이 아님)
  - `ticker_name()` 은 `pykrx.stock.get_market_ticker_name` 으로 해석하고
    동일 ticker 재호출 시 `lru_cache` 로 네트워크 재호출 없이 반환해야 한다
- **Given (fallback)**: pykrx 호출이 예외를 던지는 경우
- **Then**: `context.TICKER_NAMES` static dict 로 폴백하고, 거기에도 없으면
  `None` 을 반환해 trade_briefing 이 종목명 없이 정상 렌더링되어야 한다 (크래시
  금지)

## AC-029-14 (REQ-029-10) — 잔액 % 합계 100%

- **Given**: balance 가 `cash_d2=8,787,740`, `stock_eval=3,128,400`,
  `total_assets(tot_evlu_amt)=9,919,870` 를 반환한다 (라이브 관측값)
- **When**: trade_briefing 의 `cash_pct` / `equity_pct` 가 계산되면
- **Then**:
  - `invest_basis = 8,787,740 + 3,128,400 = 11,916,140`
  - `cash_pct = 8,787,740 / 11,916,140 ≈ 73.7%`,
    `equity_pct = 3,128,400 / 11,916,140 ≈ 26.3%`
  - **`cash_pct + equity_pct == 100.0%`** (±0.1 반올림 허용) 이어야 한다
    (이전: 88.6% + 31.5% = 120% 버그 수정)
  - headline `자산:` 금액은 `total_assets` (9,919,870) 그대로 표시 가능

## AC-029-15 (REQ-029-10) — invest_basis=0 가드

- **Given**: 신규 계좌로 `cash_d2=0`, `stock_eval=0` 이다 (invest_basis=0)
- **Then**: 0 나눗셈 없이 `cash_pct=0.0`, `equity_pct=0.0` 으로 처리되어야 한다

## v0.2.0 Edge Cases

### EC-029-7 — held_qty < already_accounted (외부 매도 / 데이터 reset)

- **Given**: `positions.already_accounted` 합이 4 인데 balance held_qty 가
  2 로 줄었다 (사용자가 KIS 웹포털에서 직접 매도 등)
- **Then**: `newly_filled = max(0, held_qty - already_accounted)` 로 clamp 하여
  음수 배분이 발생하지 않아야 한다. submitted BUY 주문은 전이되지 않고, positions
  mirror 가 balance 의 실제 `qty=2` 로 정정되어야 한다 (balance = source of truth)

### EC-029-8 — ticker held in balance, 그러나 submitted 주문 없음 (수기 매수)

- **Given**: balance 에 `(078930, qty=10)` 이 있으나 로컬 `orders` 에 078930
  submitted BUY 가 없다 (KIS 웹 직접 매수)
- **Then**: orders 전이는 발생하지 않고, positions mirror 만 078930 을 UPSERT
  해야 한다 (positions 는 항상 balance 를 미러). orders 와 positions 는 독립적
  으로 처리됨

### EC-029-9 — pykrx 종목명 해석 캐시 일관성

- **Given**: 동일 ticker 005930 에 대해 `ticker_name()` 이 한 cycle 에서 여러 번
  호출된다
- **Then**: pykrx 네트워크 호출은 최초 1 회만 발생하고 이후는 `lru_cache` 에서
  반환되어야 한다 (테스트는 pykrx 를 mock 하여 호출 횟수 1 회 검증)

## v0.2.0 File-level Test Mapping

| Test file | Covers |
|---|---|
| `tests/kis/test_fills_balance_reconcile.py` (NEW) | REQ-029-6/7/8 — FIFO attribution, positions mirror, no inquire-daily-ccld |
| `tests/kis/test_account_balance_basis.py` (NEW) | REQ-029-10 — `invest_basis` field, pct consistency, zero guard |
| `tests/data/test_ticker_names.py` (NEW) | REQ-029-9 — pykrx resolver, lru_cache, fallback chain |
| `tests/alerts/test_trade_briefing_pct.py` (NEW) | REQ-029-10 — cash_pct+equity_pct==100, name rendered |
| `tests/scheduler/test_fill_sync_cron.py` (UPDATE) | cron now drives balance reconcile |
| `tests/cli/test_cli_fill_sync.py` (UPDATE) | `--dry-run` previews balance-reconcile transitions, zero DB writes |

---

## Backward Compatibility

### BC-029-1 — 기존 cron 및 persona 시스템 무회귀

- **Given**: SPEC-029 Phase C 가 배포되어 fill_sync 60s cron 이 등록되었다
- **When**: 영업일이 진행되면
- **Then**: 기존 pre_market 07:30 cron, SPEC-024 adaptive intraday cron,
  SPEC-026 의 단기과열 softening 로직, news_crawl 정기 polling, daily_report
  cron 등 모든 기존 cron 이 정상 발사되어야 하고, fill_sync 와의 시간대 중복
  이 있어도 race condition 또는 KIS rate limit 위반이 발생하지 않아야 한다

### BC-029-2 — SPEC-025 blocked-aware screener 호환성

- **Given**: SPEC-025 의 blocked-aware screener 가 KIS `inquire-balance` 와
  blocked_tickers.json 을 참조한다
- **When**: 본 SPEC 이 `positions` 테이블에 실제 holdings 를 채우기 시작한다
- **Then**: SPEC-025 의 screener 동작 (blocked ticker exclusion, candidate
  scoring) 은 변경 없이 정상 동작해야 한다. `positions` 변화는 SPEC-022/023
  의 universe expansion 만 영향 (의도된 효과)

### BC-029-3 — SPEC-026 단기과열 softening 호환성

- **Given**: SPEC-026 의 단기과열 softening 이 단기과열(55) ticker 의 매수를
  size-cap + limit-order 로 통과시킨다
- **When**: 단기과열 ticker 의 매수가 체결되어 본 SPEC 이 positions 에 UPSERT
  한다
- **Then**: SPEC-026 의 매수 결정 로직은 변경 없이 정상 동작하고, fill 후 본
  SPEC 이 표준 BUY 처리와 동일하게 positions 업데이트해야 한다 (단기과열 여부
  는 fill 단계에서 별도 처리 없음)

### BC-029-4 — Risk limits 의 daily order count 정확성 향상

- **Given**: SPEC-026 의 `risk/limits.py:42-52` `daily_order_count_today()` 가
  현재 `status='submitted'` 도 "체결" 로 카운트한다 (research.md §1.3 root cause)
- **When**: 본 SPEC 이 status 를 filled / partial / cancelled / rejected 로
  정확히 전이시킨다
- **Then**: daily_order_count_today() 가 더 정확한 값을 반환하기 시작한다
  (의도된 정확도 향상). 단 SPEC-026 의 daily cap 동작 자체는 변경 없음 (cap
  비교는 여전히 동일 metric 사용)

---

## Test Strategy

### Unit Tests (Phase A → Phase B GREEN gate)

- `tests/kis/test_fills_inquiry.py`: REQ-029-1
  - Mock response → FillRow parsing (5 scenarios: filled, partial, pending, cancelled, rejected)
  - Paper vs live tr_id dispatch (VTTC8001R / TTTC8001R)
  - Request params match research.md §3.2 spec
  - Rate-limit retry via existing client mechanism

- `tests/kis/test_fills_order_transition.py`: REQ-029-2
  - Five status transition scenarios
  - Concurrent transition blocked by SELECT FOR UPDATE
  - reject reason recorded in orders.response jsonb
  - audit_log emitted per transition

- `tests/db/test_positions_upsert.py`: REQ-029-3
  - First buy → INSERT with correct avg_cost
  - Second buy → weighted-avg avg_cost (integer arithmetic)
  - Sell → qty decrement, avg_cost preserved
  - Sell to zero → row retained
  - Zero-qty + new buy → avg_cost reset to new fill_price
  - POSITION_UPDATED audit_log emitted

### Integration Tests (Phase C)

- `tests/scheduler/test_fill_sync_cron.py`:
  - Cron 등록 확인, KRX trading day guard, 시장 시간 외 no-op
  - `_safe_call()` 가 failure 시 스케줄러 crash 방지

- `tests/cli/test_fill_sync_command.py`:
  - `--dry-run` 시 DB 변경 없음 (before/after diff)
  - `--start YYYYMMDD` flag 가 INQR_STRT_DT override
  - Exit codes (0/1/2)

### End-to-end Smoke (Phase E, manual)

- Docker compose paper 환경에서 redeploy 후 첫 fill_sync 가 오늘의 12 already-
  submitted orders 를 transition
- audit_log 에 ORDER_FILLED / ORDER_PARTIAL / ORDER_CANCELLED / ORDER_REJECTED_BY_KIS
  / POSITION_UPDATED event 실제 발생 확인
- daily_report 의 "체결 N건" 메시지가 실제 체결 수 반영 확인
- SPEC-022/023 universe expansion 이 실제 holdings 반영 확인

### Regression Tests

- `pytest tests/` 전체 green (특히 SPEC-022 / 023 / 025 / 026 의 test suite)
- 기존 cron 시간대 정상 발화 확인 (smoke + log 검토)

---

## Quality Gates

- 모든 신규 코드 test coverage ≥ 85% (TRUST 5 Tested)
- `ruff check src/trading/kis/fills.py src/trading/db/positions.py` clean
  (TRUST 5 Readable, Unified)
- (가능 시) `pyright` 또는 `mypy` strict 모드 clean (TRUST 5 Readable)
- 모든 SQL parameterised query 사용, raw string interpolation 금지
  (TRUST 5 Secured)
- 모든 transition / position update 에 audit_log 기록 (TRUST 5 Trackable)
- KIS API rate limit 우회 시도 0 건 (BC compliance)
- `@MX:WARN` 가 Phase E 통과 시점에 제거됨 (KIS 필드 검증 완료)
- live mode 진입 코드 0 건 (`live_unlocked=false` 유지)

---

## Verification Tools

- `pytest` (unit + integration)
- `psycopg` 직접 connection 으로 SELECT FOR UPDATE 동작 검증
- Docker compose 통합 환경 paper smoke test
- KIS API mock (`tests/kis/fixtures/inquire_ccld_response.json` 기반)
- 실거래 paper API call (Phase E only) — 응답 페이로드 직접 로깅 + research.md
  §3.3 와 cross-check
- `docker exec trading-postgres psql ...` 로 orders / positions / audit_log
  테이블 직접 조회

---

## Acceptance Sign-off

- Phase A 완료: 모든 RED 테스트 작성 완료, 기존 test suite green 유지
- Phase B 완료: Phase A 의 모든 테스트 GREEN, `ruff` + type check clean
- Phase C 완료: 통합 테스트 `test_fill_sync_cron` + `test_fill_sync_command` 통과
- Phase D 완료 (조건부): `orders.filled_at` 컬럼 확인 또는 마이그레이션 022 적용
- Phase E 통과 = 본 SPEC 의 최종 acceptance:
  - 실거래 paper KIS API 응답으로 오늘의 12 orders 정상 transition
  - KIS 응답 필드 매핑 검증 완료 (`@MX:WARN` 제거)
  - `positions` 테이블에 BUY 체결분 가중평균 cost 로 UPSERT 확인
  - daily report "체결 N건" 정확성 확인
  - SPEC-022/023 universe expansion 동작 정상화 확인
  - 회귀 테스트 0 건 실패
