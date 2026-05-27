---
id: SPEC-TRADING-029
version: 0.1.0
status: draft
created: 2026-05-26
updated: 2026-05-26
author: onigunsow
priority: high
issue_number: 0
domain: TRADING
title: "KIS order lifecycle sync — fill confirmation, status transitions, positions UPSERT"
related_specs:
  - SPEC-TRADING-022
  - SPEC-TRADING-023
  - SPEC-TRADING-025
  - SPEC-TRADING-026
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

## Acceptance & Traceability

자세한 acceptance criteria 는 [acceptance.md](./acceptance.md) 참조.
구현 phase / milestone / MX tag strategy 는 [plan.md](./plan.md) 참조.

본 SPEC 의 implementation 진입은 `/moai:2-run SPEC-TRADING-029` 로 진행한다.
현재 branch (`fix/SPEC-TRADING-026-overheating-softening`) 에서 그대로 작업
지속 (사용자 결정 2026-05-26: 분리된 브랜치 / GitHub Issue 생성 보류).
