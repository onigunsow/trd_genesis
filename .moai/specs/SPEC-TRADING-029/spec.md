---
id: SPEC-TRADING-029
version: 0.2.0
status: draft
created: 2026-05-26
updated: 2026-05-28
author: onigunsow
priority: high
issue_number: 0
domain: TRADING
title: "KIS order lifecycle sync — balance-reconcile fill tracking, stock-name display, balance-% integrity"
related_specs:
  - SPEC-TRADING-022
  - SPEC-TRADING-023
  - SPEC-TRADING-025
  - SPEC-TRADING-026
changelog:
  - version: 0.2.0
    date: 2026-05-28
    summary: >-
      REDESIGN. v0.1.0 의 inquire-daily-ccld (VTTC8001R) 데이터 소스가
      모의투자(paper) 환경에서 당일 체결을 전혀 반환하지 않음이 2026-05-28
      라이브에서 확정됨 (msg_cd 70070000, output1=[]). 데이터 소스를 검증된
      inquire-balance (VTTC8434R) 로 전환. 동시에 SPEC-030 으로 분리될 예정이던
      두 결함을 흡수: (2) Telegram 매매 알림 종목명 미표시, (3) 잔액 % 합계 >100%
      버그. 세 문제를 하나의 SPEC-029 v0.2.0 으로 통합. REQ-029-6 ~ REQ-029-10
      신규/개정, REQ-029-1/2/3 의 데이터 소스 부분 supersede.
  - version: 0.1.0
    date: 2026-05-26
    summary: >-
      Initial — inquire-daily-ccld 기반 fill 확인 + orders 전이 + positions
      가중평균 UPSERT. (배포되었으나 paper 환경 데이터 소스 결함으로 실패.)
---

# SPEC-TRADING-029 — KIS Order Lifecycle Sync

## Overview

오늘 (2026-05-26) 페이퍼 트레이딩 사이클에서 시스템이 KIS 에 12 개 BUY 주문을
정상 제출했지만, **체결 확인 단계가 코드베이스에 전혀 존재하지 않아** `orders`
테이블은 전부 `status='submitted'` 로 멈춰 있고 `positions` 테이블은 0 rows 인
상태가 드러났다. KIS 서버는 주문을 수신했고 `kis_order_no` 도 모두 부여되었으나,
로컬 DB 는 "주문을 보냈지만 체결되었는지 모르는" 상태에서 영구히 정지한다.

본 SPEC 은 누락된 **post-submission 라이프사이클** 을 추가한다:

1. KIS `inquire-daily-ccld` 체결조회 API 호출 (paper `VTTC8001R` / live `TTTC8001R`)
2. 응답을 기반으로 `orders.status` 를 `submitted → filled / partial / cancelled / rejected` 로 전이
3. 체결 확정 시 `positions` 테이블에 가중평균 cost 로 UPSERT
4. APScheduler cron 으로 시장 시간 (09:00–15:30 KST) 매 60 초 폴링
5. 수동 재시도/백필용 CLI 서브커맨드 (`trading fill-sync`)

상세한 진단 근거와 KIS API contract, 코드베이스 영향 범위는
[research.md](./research.md) 참조.

## Root Cause (verified 2026-05-26)

research.md §1 ~ §2 의 요약:

1. **체결 확인 코드 0건**: `inquire-ccnl` / `inquire-daily-ccld` / `VTTC8001R` /
   `TTTC8001R` / `ccld` / `체결조회` 등 모든 후보 키워드에 대해 코드베이스 grep
   결과 0 hit. `UPDATE orders ... status='filled'` 등의 전이 SQL 도 0건.
   `INSERT INTO positions` 도 0건.
2. **로컬 DB 증거**: paper 모드 `orders` 테이블 = 10 submitted + 2 rejected,
   `positions` 테이블 = 0 rows. KIS 서버 측에는 `kis_order_no` 가 모두 부여되어
   있으므로 (e.g. 0000001977, 0000002308, 0000006180) gap 은 순수 로컬 측.
3. **다운스트림 왜곡**:
   - `risk/limits.py:42-52` `daily_order_count_today()` 가 `submitted` 도 "체결"
     로 카운트하여 SPEC-026 softening 의 over-trigger 를 가려왔음
   - `reports/daily_report.py:51` "체결 N건" 메시지가 실제 체결 0 건임에도 10건
     으로 표시 (submitted ∪ filled 의 합)
   - SPEC-022/023 의 `data/universe.py` `WHERE qty > 0` 쿼리가 늘 빈 결과 →
     universe expansion 자체가 무의미하게 동작 중

## Requirements (EARS)

### REQ-029-1 (P0, CRITICAL) — KIS 체결조회 API integration

**WHEN** there exists at least one `orders` row with `status='submitted'`,
`kis_order_no IS NOT NULL`, and `ts` within today (KST), **THEN** the system
SHALL invoke KIS daily order execution inquiry
(`GET /uapi/domestic-stock/v1/trading/inquire-daily-ccld`,
paper `tr_id=VTTC8001R` / live `tr_id=TTTC8001R`) **once per sync cycle** and
parse all rows in the response.

**Implementation notes** (per research.md §3, §4.1):

- 신규 모듈 `src/trading/kis/fills.py`, 진입점 함수
  `inquire_fills_today(client) -> list[FillRow]`
- 기존 `KisClient.get()` 의 rate-limit retry (`RATE_LIMIT_RETRIES=4`,
  `RATE_LIMIT_MSG_CODES={"EGW00201"}`) 그대로 재사용 — 신규 transport 작업 없음
- 요청 파라미터는 research.md §3.2 표 기준
- 응답 필드명 (`odno`, `tot_ccld_qty`, `avg_prvs` / `pchs_avg_pric`, `cncl_yn`,
  `rfus_yn` 등) 은 KIS 공식 문서 직접 접근이 차단되어 **provisional** —
  첫 호출 시 응답 페이로드 전체를 로깅하고 가정과 대조 후 매핑을 확정해야 함.
  매핑 검증 전까지는 해당 파싱 블록에 `@MX:WARN` + `@MX:REASON` 적용 (plan.md
  §MX tag strategy 참조)

### REQ-029-2 (P0) — orders.status lifecycle transitions

**WHEN** a `FillRow` from KIS reports `tot_ccld_qty == ord_qty`
→ THE SYSTEM SHALL set `orders.status='filled'`,
`fill_qty=tot_ccld_qty`, `fill_price=avg_prvs (or pchs_avg_pric)`,
`filled_at=now()`, and INSERT `audit_log` with `event_type='ORDER_FILLED'`.

**WHEN** `0 < tot_ccld_qty < ord_qty` → THE SYSTEM SHALL set
`orders.status='partial'` with the same fields and audit_log
`event_type='ORDER_PARTIAL'`.

**WHEN** `cncl_yn='Y'` → THE SYSTEM SHALL set `orders.status='cancelled'` and
audit_log `event_type='ORDER_CANCELLED'`.

**WHEN** `rfus_yn='Y'` → THE SYSTEM SHALL set `orders.status='rejected'` with
the KIS reject reason recorded in `orders.response` (jsonb) and audit_log
`event_type='ORDER_REJECTED_BY_KIS'`.

**WHEN** `tot_ccld_qty=0 AND cncl_yn='N' AND rfus_yn='N'`
→ THE SYSTEM SHALL leave `orders.status='submitted'` unchanged (no-op).

**Constraints** (per research.md §3.4, §4.5):

- All transitions transactional within a single connection (commit-on-success;
  rollback + audit_log on error following the `src/trading/kis/order.py:128-198`
  pattern)
- `SELECT ... FOR UPDATE` on `orders.id` to prevent concurrent transition (e.g.
  cron + CLI race)
- Schema verification gate: confirm `orders.filled_at timestamptz` column
  exists; if missing, add migration `022_add_filled_at.sql` before implementation
  (plan.md Phase D)

### REQ-029-3 (P0) — positions UPSERT on fill

**WHEN** `orders.status` transitions to `'filled'` OR `'partial'`
(REQ-029-2), **THEN**:

- **IF** `orders.side='buy'` → THE SYSTEM SHALL UPSERT `positions` row via
  `INSERT INTO positions(ticker, qty, avg_cost, last_order_id, last_updated)
  VALUES (...) ON CONFLICT (ticker) DO UPDATE SET
    qty = positions.qty + EXCLUDED.qty,
    avg_cost = ((positions.qty * positions.avg_cost) +
                (EXCLUDED.qty * EXCLUDED.avg_cost))
               / (positions.qty + EXCLUDED.qty),
    last_order_id = EXCLUDED.last_order_id,
    last_updated = now()`
- **IF** `orders.side='sell'` → THE SYSTEM SHALL UPDATE `positions`:
  `qty = GREATEST(positions.qty - fill_qty, 0)`, `avg_cost` unchanged,
  `last_order_id=order_id`, `last_updated=now()`. When `qty` reaches 0, the
  row SHALL be retained (do not DELETE) so avg-cost history is preserved.

THE SYSTEM SHALL also INSERT `audit_log` with `event_type='POSITION_UPDATED'`
recording the side, fill_qty, and resulting avg_cost.

**Constraints** (per research.md §2.5, §4.2):

- positions DB 모듈 위치는 `src/trading/db/positions.py` (신규 파일) 우선; 만약
  `src/trading/kis/fills.py` 가 작아서 co-locate 가 합리적이면 plan.md 에서
  최종 결정 (ADR-029-2 참조)
- `ON CONFLICT (ticker)` 가 atomic UPSERT 를 보장하므로 positions 측 별도
  row-lock 불필요 (단, orders 측은 REQ-029-2 의 FOR UPDATE 적용)
- 가중평균 계산은 `positions.avg_cost integer` 컬럼 타입을 유지하기 위해 정수
  산술을 사용 (Python 측에서 round/division 수행 후 정수 캐스팅) — 부동소수점
  drift 방지

### REQ-029-4 (P1) — Scheduler cron integration

**WHEN** current time is between 09:00 and 15:30 KST on a KRX trading day,
**THEN** THE SYSTEM SHALL execute `fill_sync()` once per minute (60-second
polling interval) via APScheduler, wrapped in the existing `_safe_call()`
guard so that failures log but never crash the scheduler process.

THE SYSTEM SHALL also expose a CLI subcommand `trading fill-sync [--start
YYYYMMDD] [--dry-run]` for manual retry and historical backfill.

**Constraints** (per research.md §2.4, §4.3):

- Cron registration in `src/trading/scheduler/runner.py:main()` following the
  existing `_wrap()` + `_safe_call()` pattern (cf. `_run_news_crawl`,
  `_run_intraday_cycle`)
- 60s interval × 390 trading minutes ≈ 390 calls/day, well under KIS sustained
  rate limit (모의투자 1초 5회) — no additional throttling required
- CLI subcommand co-located with existing `trading halt` / `trading resume` in
  `src/trading/cli.py`

### REQ-029-5 (P1) — Retroactive sync on first deployment

**WHEN** the first `fill_sync()` runs after SPEC-029 deployment **AND** there
exist `status='submitted'` orders from prior days, **THEN** THE SYSTEM SHALL
detect them via the standard inquiry (`INQR_STRT_DT=today`) and transition
them per REQ-029-2/3.

THE SYSTEM SHALL provide a `--dry-run` flag on the CLI subcommand so the first
execution preview can be inspected before any UPDATE is committed.

**Constraints** (per research.md §4.4):

- 별도 backfill 코드 경로 없음 — 오늘의 `INQR_STRT_DT=today` 요청이 오늘 이미
  제출된 12 건을 자동으로 surface 함
- 다일 백필이 필요한 드문 경우, CLI `--start YYYYMMDD` 플래그가 INQR_STRT_DT 를
  override
- `--dry-run` 은 intended transitions 를 stdout 에 출력하고 zero DB writes 를
  수행 (transactional dry-run; not "execute then rollback")

## Non-Goals (Out of Scope)

- **잔액 % 계산 정정** (briefing "주식 12.0%" bug): research.md §1.4 참조,
  SPEC-028 또는 SPEC-030 으로 분리
- **SPEC-028 6 REQ** (overheat per-day cap, halt-state gate, sector classifier,
  circuit-breaker reset, ticker name display, balance display integrity):
  별도 branch
- **다음 영업일 미체결 주문 자동 취소/이관 정책**: 별도 SPEC
- **Live mode 활성화**: `live_unlocked=false` 유지
- **잔고/주문가능금액 폴링**: 별도 inquiry endpoint (`inquire-balance` 외 추가)
  필요 — 분리
- **종목별 손익 lot-by-lot tracking**: 가중평균 avg_cost 만 유지; lot 단위
  세무 추적은 별도 concern
- **`positions.qty=0` 행 cleanup**: avg-cost 이력 보존 위해 유지, 주기적 cleanup
  은 본 SPEC 의 책임 아님

---

# Amendment v0.2.0 — REDESIGN (2026-05-28)

> 위 §Overview ~ §Non-Goals 까지는 **v0.1.0 의 원본 기록** 으로 보존한다.
> 본 v0.2.0 amendment 가 데이터 소스와 scope 를 재정의하며, 충돌하는 부분은
> 본 절이 우선한다.

## v0.2.0 Root Cause — v0.1.0 가 왜 실패했나 (verified live 2026-05-28)

배포된 v0.1.0 의 `fill_sync` cron 은 첫 실거래일 동안 **300 회 무결점으로
실행되었으나 매 호출이 `queried=0`** 을 반환했다. 직접 API 테스트로 다음이
확정되었다:

- KIS `inquire-daily-ccld` (paper `tr_id=VTTC8001R`) 는 **모든 `CCLD_DVSN`
  값(00/01/02)** 에 대해 `output1=[]` 과 함께
  `"모의투자 조회할 내역(자료)이 없습니다"` (msg_cd `70070000`) 를 반환한다.
- 즉 이 엔드포인트는 **모의투자(paper) 환경에서 당일 체결을 노출하지 않는다.**
- 계좌번호는 정확하다 (`CANO=50185724`): **동일 계좌에 대한
  `inquire-balance` 는 보유 5 종목을 정확히 반환** 한다. 따라서 주문은 실제로
  KIS 에서 체결되고 있으나 `inquire-daily-ccld` 가 intraday 로 볼 수 없는 것.

결론: v0.1.0 의 엔드포인트 선택이 paper 모드에 대해 **근본적으로 잘못** 되었다.
검증된 `inquire-balance` 로 데이터 소스를 전환한다.

## v0.2.0 Scope — 세 문제를 하나의 SPEC 으로 (SPEC-030 흡수)

본 amendment 는 사용자 결정에 따라 다음 세 가지를 **하나의 SPEC-029 v0.2.0**
으로 함께 해결한다 (별도 SPEC-030 신설하지 않음):

1. **Fill tracking (핵심 수정)** — `submitted` 에 영구히 멈춘 orders 를
   `inquire-balance` reconcile 로 `filled`/`partial` 전이.
2. **종목명 표시** — Telegram 매매 알림이 `name=None` 으로 종목명을 비움.
3. **잔액 % 버그** — `현금 % + 주식 %` 합계가 100% 를 초과 (라이브 관측:
   현금 88.6% + 주식 31.5% = 120%).

## v0.2.0 Data-Source Switch

- v0.1.0: `inquire-daily-ccld` (`VTTC8001R` paper / `TTTC8001R` live) — **폐기**.
- v0.2.0: `inquire-balance`
  (`/uapi/domestic-stock/v1/trading/inquire-balance`,
  `VTTC8434R` paper / `TTTC8434R` live) — **검증된 동작**.
- 진입점은 기존 `src/trading/kis/account.py` 의 `balance(client)` 를 그대로
  재사용한다. 이 함수는 이미 summary (cash_d2, buyable, total_assets,
  stock_eval, pnl_total, nrcvb_buy_amt) 와 ticker 별 holdings (ticker, name,
  qty, avg_cost, current_price, eval_amount, pnl_amount, pnl_pct) 를 반환한다.

## v0.2.0 핵심 설계 결정 (ADR 요약, 상세는 plan.md)

- **balance reconcile + FIFO attribution**: `inquire-balance` 는 ticker 별
  *현재 누적 보유 수량* 만 제공하고 주문별 체결을 제공하지 않는다. 따라서
  ticker 별 submitted BUY 주문을 가장 오래된 것부터 (FIFO) `held_qty -
  already_accounted` 만큼 채워 `filled`/`partial` 로 전이한다 (REQ-029-7).
- **positions = balance mirror**: v0.1.0 의 정수 가중평균 재구성을 폐기하고,
  `positions` 테이블을 매 sync 마다 `inquire-balance` holdings 의 직접 미러로
  UPSERT 한다 (avg_cost = KIS `pchs_avg_pric` 그대로). 더 이상 부동소수점/정수
  drift 가 없다 (REQ-029-8). v0.1.0 ADR-029-5 (정수 산술) 는 **무효화**.
- **fill_price 근사**: balance 는 주문별 체결가를 주지 않으므로 전이 시
  `fill_price = KIS pchs_avg_pric` (ticker 누적 평단가) 를 기록한다. 단일 주문
  단위 정확 체결가가 아님을 명시 (known approximation).
- **inquire-psbl-rvsecncl 미채택 (defer)**: 정정취소가능주문조회
  (`/uapi/domestic-stock/v1/trading/inquire-psbl-rvsecncl`, live `TTTC0084R`)
  로 partial/cancel 을 정밀 구분할 수 있으나, **이 엔드포인트는
  inquire-daily-ccld 와 동일한 "주문조회" 계열** 이고 KIS 공식 예제에 paper(V)
  tr_id 가 없다 — paper 에서 동일하게 빈 응답일 위험이 매우 높다 (research.md
  §v0.2 참조). 사용자 선호(단순 balance-only)에 따라 **현 단계 미도입**.
  - **Tradeoff (명시)**: balance-only 는 "KIS 에서 취소됨"과 "아직 미체결"을
    구분하지 못한다 (둘 다 held_qty 증가 없음). 따라서 cancel/reject 자동 전이는
    본 amendment 의 scope 에서 제외하고, 미체결 stale order 의 영업일 말 정리
    정책은 별도 SPEC 으로 미룬다.

## Requirements (EARS) — v0.2.0

> REQ-029-6 ~ REQ-029-10 신규/개정. REQ-029-1 (inquire-daily-ccld 호출) 과
> REQ-029-2/3 의 *데이터 소스 및 가중평균 산술* 부분은 아래 REQ 로 supersede 된다.
> REQ-029-4 (scheduler cron) / REQ-029-5 (dry-run, 첫 배포 retroactive) 는 그대로
> 유효하되, 호출 대상이 `inquire_fills_today` → `reconcile_from_balance` 로 바뀐다.

### REQ-029-6 (P0, CRITICAL) — Data source = inquire-balance [supersedes REQ-029-1]

**WHEN** a fill-sync cycle runs, **THE SYSTEM SHALL** obtain current holdings via
`inquire-balance` (paper `VTTC8434R` / live `TTTC8434R`) by reusing
`trading.kis.account.balance(client)`, and **SHALL NOT** call
`inquire-daily-ccld` (deprecated for paper).

### REQ-029-7 (P0) — Balance-reconcile orders transition (FIFO) [revises REQ-029-2]

**WHEN** there exist `orders` rows with `status='submitted'`,
`kis_order_no IS NOT NULL`, and `side='buy'`, **THEN** for each ticker the
system **SHALL** compute `newly_filled = held_qty - already_accounted`
(where `already_accounted` = Σ `fill_qty` over the ticker's already-transitioned
`filled`/`partial` rows), and **SHALL** allocate `newly_filled` to the ticker's
submitted BUY orders **oldest-first (FIFO)**:

- allocation `>= order.qty` → `status='filled'`, `fill_qty=order.qty`,
  `fill_price=<balance pchs_avg_pric>`, `filled_at=now()`, audit `ORDER_FILLED`
- `0 < allocation < order.qty` → `status='partial'`, `fill_qty=allocation`,
  same fields, audit `ORDER_PARTIAL`
- allocation `== 0` → leave `status='submitted'` (no-op)

All transitions are transactional with `SELECT ... FOR UPDATE` on the order row
(cron + CLI race protection, carried over from v0.1.0).

**WHILE** balance-only data is the source, the system **SHALL NOT** auto-transition
orders to `cancelled` or `rejected` (out of scope per ADR; see Tradeoff above).

### REQ-029-8 (P0) — positions = balance mirror [revises REQ-029-3]

**WHEN** a fill-sync cycle runs, **THE SYSTEM SHALL** mirror `inquire-balance`
holdings into the `positions` table:

- For each held ticker (`hldg_qty > 0`): UPSERT
  `(ticker, qty=hldg_qty, avg_cost=pchs_avg_pric, last_updated=now())`
  via `ON CONFLICT (ticker) DO UPDATE`.
- For tickers present in `positions` but **absent** from current balance holdings:
  set `qty=0` (retain row per v0.1.0 ADR-029-4; do not DELETE).
- audit `POSITION_SYNCED` recording the ticker, qty, and avg_cost.

The integer weighted-average reconstruction of v0.1.0 (REQ-029-3, ADR-029-5)
is **removed** — avg_cost comes directly from KIS `pchs_avg_pric`.

### REQ-029-9 (P1) — Stock name display in trade alerts [new, absorbs SPEC-030]

**WHEN** a trade briefing is sent (`tg.trade_briefing`), **THE SYSTEM SHALL**
resolve the ticker's display name and pass it as `name` (not `None`).

- Name source: `pykrx.stock.get_market_ticker_name(ticker)` via a new resolver
  `trading.data.ticker_names.ticker_name(ticker)` with an in-memory `lru_cache`.
- **WHY pykrx not balance**: at trade-alert time the freshly submitted order may
  not yet appear in `inquire-balance` holdings, so the balance `prdt_name` is
  unreliable for the alert; pykrx resolves any KRX ticker independently.
- **IF** the resolver fails (network/pykrx error) **THEN** the system **SHALL**
  fall back to the static `context.TICKER_NAMES` dict, and finally to `None`
  (trade_briefing already renders gracefully when `name` is `None`).
- The hardcoded 5-entry `TICKER_NAMES` in `context.py:25` **SHALL** be demoted
  to an offline fallback only (dynamic universe per SPEC-022/023 makes a
  hardcoded dict insufficient).

### REQ-029-10 (P1) — Balance percentage consistency [new, absorbs SPEC-030]

**WHEN** `trade_briefing` displays `현금 %` and `주식 %`, **THE SYSTEM SHALL**
ensure the two percentages are computed against a **single consistent
denominator** so they sum to 100%.

- Root cause: KIS `tot_evlu_amt` (total_assets) **≠** `dnca_tot_amt` (cash_d2)
  `+` `scts_evlu_amt` (stock_eval). Verified live: `8,787,740 + 3,128,400 =
  11,916,140 ≠ 9,919,870`. v0.1.0 callers used `total_assets` as the denominator
  for both numerators which came from different KIS fields → sum > 100%.
- Fix: define `invest_basis = cash_d2 + stock_eval`; then
  `cash_pct = cash_d2 / invest_basis`, `equity_pct = stock_eval / invest_basis`.
  These sum to exactly 100%.
- `total_assets` (tot_evlu_amt) **MAY** still be displayed as the headline asset
  figure (it reflects KIS's full valuation including unrealized P&L and D+2
  settlement timing); only the two percentages switch to `invest_basis`.
- The `-79,700` `pnl_total` is a genuine unrealized loss, unrelated to this bug.

## v0.2.0 Non-Goals (additions / changes)

- `inquire-psbl-rvsecncl` 정밀 partial/cancel 구분: **deferred** (위 ADR).
- `cancelled` / `rejected` 자동 전이: balance-only 한계로 **out of scope**.
  (KIS 측 거부는 제출 단계에서 이미 `status='rejected'` 로 기록되므로 fill-sync
  단계의 책임 아님.)
- per-order 정확 체결가: balance 는 ticker 평단가만 제공 → **out of scope**
  (approximation 명시).
- 미체결 stale order 영업일 말 정리: **별도 SPEC**.
- v0.1.0 의 정수 가중평균 산술: **삭제** (positions = balance mirror 로 대체).

## Acceptance & Traceability

자세한 acceptance criteria 는 [acceptance.md](./acceptance.md) 참조 (v0.2.0
AC-029-9 ~ AC-029-15 추가). 구현 phase / file-change list 는 [plan.md](./plan.md)
참조 (v0.2.0 Phase F ~ J). inquire-psbl-rvsecncl 조사 결과는 [research.md]
(./research.md) §v0.2 참조.

본 SPEC 의 implementation 진입은 `/moai:2-run SPEC-TRADING-029` 로 진행한다.
현재 branch (`fix/SPEC-TRADING-026-overheating-softening`) 에서 그대로 작업
지속 (사용자 결정 2026-05-26: 분리된 브랜치 / GitHub Issue 생성 보류).
