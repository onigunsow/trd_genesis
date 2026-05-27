# SPEC-TRADING-029 Research — KIS Order Lifecycle Sync

**Authored:** 2026-05-26
**Phase:** 0.5 Deep Research (plan workflow)
**Context branch:** `fix/SPEC-TRADING-026-overheating-softening`

---

## 1. Background — Discovery (2026-05-26 debug session)

### 1.1 Observable symptom

Today's paper trading cycle showed 12 BUY order briefings on Telegram (10 succeeded, 2 risk-rejected). However, the system state diverged from the briefings in three serious ways:

| Briefing claim | DB reality |
|---|---|
| "086790 1주 매수 @ (시장가)" × 7 cycles | `orders` table: 7 rows, all `status='submitted'`, `fill_qty=NULL`, `fill_price=NULL` |
| "주식 12.0%" final equity ratio | `positions` table: **0 rows** |
| Daily report "체결 10건" | Actual fills (status='filled' or 'partial') = **0** |

### 1.2 Direct DB evidence

```
mode  |  status   | count
------+-----------+------
paper | rejected  |   2
paper | submitted |  10
```

```
positions table: 0 rows
```

KIS server did receive the orders (every successful submission has a `kis_order_no` assigned, e.g. 0000001977, 0000002308, 0000006180, ...) — so the gap is purely on the local side.

### 1.3 Root cause — missing post-submission lifecycle

Codebase audit confirms the entire fill confirmation pipeline does not exist:

| Search pattern | Hits |
|---|---|
| `inquire-ccnl`, `inquire-daily-ccld`, `VTTC8001R`, `TTTC8001R`, `ccld`, `체결조회` | **0** |
| `UPDATE orders ... status='filled'` or `status='partial'` | **0** |
| `INSERT INTO positions`, `UPSERT positions` | **0** |
| Scheduled fill sync / reconciliation hooks | **0** |

The only `UPDATE orders` SQL in the codebase is at `src/trading/kis/order.py:117,128,166` and is bounded to submission-time persistence (`status='submitted'/'rejected'/'error'`). Nothing after that.

### 1.4 Why daily KIS balance keeps drifting

`src/trading/personas/orchestrator.py:1034-1035` (and 4 more callsites) computes:
```python
ca_pct = (bal_after["cash_d2"] / bal_after["total_assets"] * 100)
eq_pct = (bal_after["stock_eval"] / bal_after["total_assets"] * 100)
```

The fields come from KIS `inquire-balance` (paper VTTC8434R / live TTTC8434R). KIS's `tot_evlu_amt` is NOT `cash_d2 + stock_eval` — it includes D+2 정산금, CMA, 미수금 etc. The percentages therefore do not sum to 100 (today: cash 100.1% + equity 10.9% = 111%). **This briefing bug is out of scope for SPEC-029** (deferred to SPEC-028 or SPEC-030); it is documented here as a related contextual finding.

---

## 2. Affected code areas

### 2.1 Order submission (existing)

`src/trading/kis/order.py`:
- `submit_order()` (line 48) — single chokepoint for all order submissions
- Persists `status='submitted'` then returns; no further lifecycle
- KIS response stored in `orders.response` (jsonb) and `kis_order_no`

### 2.2 KIS REST client (existing, reusable)

`src/trading/kis/client.py`:
- `KisClient.get()` / `post()` already implement auth, tr_id paper/live dispatch, rate-limit retry (4 attempts × backoff)
- `RATE_LIMIT_MSG_CODES = {"EGW00201"}` (초당 거래건수 초과)
- New fill-inquiry calls reuse `client.get(...)` — no new auth/transport work needed

### 2.3 KIS balance read (existing, reference pattern)

`src/trading/kis/account.py:10-75`:
- `balance(client)` — clean reference for GET inquiry endpoints
- Same pattern (`output1` = per-row list, `output2[0]` = summary) likely applies to inquire-daily-ccld

### 2.4 Scheduler (extension point)

`src/trading/scheduler/runner.py`:
- APScheduler `BlockingScheduler` with KST cron triggers
- `_wrap()` enforces KRX trading-day guard
- Existing pattern: register cron in `main()` (line 95+), e.g. `_run_news_crawl`, `_run_intraday_cycle`
- New fill_sync cron will register similarly

### 2.5 DB schema

`positions` table (verified via `\d positions` 2026-05-26):
```
 id            bigint            (PK, autoincr)
 ticker        text              (NOT NULL, UNIQUE)
 qty           integer           (NOT NULL, CHECK >= 0)
 avg_cost      integer           (NOT NULL, DEFAULT 0)
 last_updated  timestamptz       (NOT NULL, DEFAULT now())
 last_order_id bigint            (FK → orders.id ON DELETE SET NULL)
```

`orders` table (relevant columns):
- `status text` — currently 'submitted' / 'rejected' / 'error'; SPEC-029 will introduce 'filled' / 'partial' / 'cancelled' transitions
- `fill_qty integer NULL`, `fill_price integer NULL` — schema-present, never written (verified by query)
- `kis_order_no text NULL` — populated at submission; the join key for fill inquiry

`filled_at` column: needs schema verification. If missing → migration 022.

### 2.6 Downstream consumers that will become accurate

- `src/trading/risk/limits.py:42-52` `daily_order_count_today()` — currently counts `submitted` as "executed", which masked the SPEC-026 softening over-trigger.
- `src/trading/risk/emergency.py:281-284` daily PnL — only counts `filled/partial`, so today's PnL is permanently 0 regardless of mock fills.
- `src/trading/reports/daily_report.py:51` "체결 N건" — currently double-meanings ("submitted ∪ filled ∪ partial"); will become accurate.
- `src/trading/data/universe.py:50-61` (SPEC-022/023) — `SELECT DISTINCT ticker FROM positions WHERE qty > 0` — currently always empty; will start returning real holdings once SPEC-029 ships.

---

## 3. KIS 체결조회 API contract (best-effort, confirm during implementation)

### 3.1 Endpoint and tr_id

- Path: `GET /uapi/domestic-stock/v1/trading/inquire-daily-ccld`
- tr_id:
  - paper: `VTTC8001R`
  - live: `TTTC8001R`
  - (Long-range >3개월: `CTSC9115R` — out of scope; today's window is intraday)

### 3.2 Request parameters (from KIS docs search)

| Param | Description | Plan value |
|---|---|---|
| `CANO` | 종합계좌번호 | `client.account_prefix` |
| `ACNT_PRDT_CD` | 계좌상품코드 | `client.account_suffix` |
| `INQR_STRT_DT` | 조회시작일자 (YYYYMMDD) | today (KST) |
| `INQR_END_DT` | 조회종료일자 (YYYYMMDD) | today (KST) |
| `SLL_BUY_DVSN_CD` | 매도매수구분 (00=전체) | `"00"` |
| `INQR_DVSN` | 조회구분 (00=역순) | `"00"` |
| `PDNO` | 종목번호 | empty or per-call ticker |
| `CCLD_DVSN` | 체결구분 (00=전체/01=체결/02=미체결) | `"00"` (전체 — partial 감지) |
| `ORD_GNO_BRNO` | 주문채번지점번호 | empty |
| `ODNO` | 주문번호 | empty (조회 시), 또는 특정 주문 핀포인트 |
| `INQR_DVSN_3` | 조회구분3 (00=전체) | `"00"` |
| `INQR_DVSN_1` | 조회구분1 | `""` |
| `CTX_AREA_FK100` | 연속조회검색조건 | `""` (first page) |
| `CTX_AREA_NK100` | 연속조회키 | `""` (first page) |

### 3.3 Response fields (provisional — verify against KIS sample on first call)

Based on KIS naming conventions and the `output1[]` per-row pattern observed in `inquire-balance`:

| Field (best guess) | Semantic | Use in SPEC-029 |
|---|---|---|
| `odno` | 주문번호 | join key vs `orders.kis_order_no` |
| `ord_dt` | 주문일자 | today filter |
| `pdno` | 종목번호 | sanity check vs `orders.ticker` |
| `sll_buy_dvsn_cd` | 01=매도 / 02=매수 | sanity check vs `orders.side` |
| `ord_qty` | 주문수량 | match `orders.qty` |
| `tot_ccld_qty` | 총체결수량 | → `orders.fill_qty` |
| `avg_prvs` or `pchs_avg_pric` | 평균체결단가 | → `orders.fill_price` |
| `cncl_yn` | 취소여부 (Y/N) | → status='cancelled' |
| `rfus_yn` | 거부여부 (Y/N) | → status='rejected' |
| `rmn_qty` | 잔량 (미체결) | partial detection (rmn_qty > 0 AND tot_ccld_qty > 0) |

**Risk:** exact field names not verified from KIS docs (portal blocked WebFetch with 403/404, no auth). Implementation MUST verify the first response payload against KIS sample logs and adjust mapping in `src/trading/kis/fills.py`. Mark with `@MX:WARN` until verified.

### 3.4 Status transition decision matrix

```
tot_ccld_qty == 0 AND cncl_yn == 'N' AND rfus_yn == 'N'
  → leave orders.status = 'submitted' (still pending)

tot_ccld_qty == ord_qty
  → orders.status = 'filled'
  + UPSERT positions (BUY: qty += fill_qty, avg_cost recompute; SELL: qty -= fill_qty)
  + audit ORDER_FILLED

0 < tot_ccld_qty < ord_qty
  → orders.status = 'partial'
  + UPSERT positions (same as filled, with fill_qty)
  + audit ORDER_PARTIAL

cncl_yn == 'Y'
  → orders.status = 'cancelled'
  + audit ORDER_CANCELLED

rfus_yn == 'Y'
  → orders.status = 'rejected' (with KIS reject reason)
  + audit ORDER_REJECTED_BY_KIS
```

---

## 4. Implementation approach (proposed)

### 4.1 New module `src/trading/kis/fills.py`

Single file, three functions:
- `inquire_fills_today(client) -> list[FillRow]` — single GET call, return parsed list of today's order fills (paper/live transparent)
- `apply_fill_to_order(fill: FillRow, conn) -> str` — UPDATE one order to filled/partial/cancelled/rejected, return new status
- `apply_fill_to_position(fill: FillRow, conn) -> None` — UPSERT positions for BUY, UPDATE for SELL, with weighted-avg cost
- All three transactional via single connection; `fill_sync()` orchestrator wraps them with audit_log writes

### 4.2 Module `src/trading/db/positions.py` (optional split)

If `fills.py` becomes too large, split positions UPSERT logic into a dedicated DB module. Reference: existing `src/trading/db/session.py` (audit, connection helpers).

### 4.3 Scheduler integration

`src/trading/scheduler/runner.py`:
- Register `fill_sync` cron: every 60s during 09:00-15:30 KST on KRX trading days (~390 calls/day, KIS rate limit OK)
- Wrap in `_safe_call` (existing) so failures are logged but never crash scheduler
- Also expose CLI entrypoint `trading fill-sync` (manual retry / dry-run)

### 4.4 Retroactive sync (REQ-029-5)

First fill_sync invocation post-deployment runs with `INQR_STRT_DT = today` regardless of past dates; today's 10 submitted orders will be detected on first call automatically. No separate backfill code needed. For >1 day backfill, manual `trading fill-sync --start YYYYMMDD` CLI.

### 4.5 Concurrency

Multiple sources can transition the same order in race (e.g. fill_sync cron + manual `trading fill-sync` CLI). Pattern: `SELECT ... FOR UPDATE` on the orders row inside the same transaction as the UPDATE; `positions` table uses `INSERT ... ON CONFLICT (ticker) DO UPDATE` for atomic UPSERT.

---

## 5. Reference implementations in this codebase

| Reference | Location | Lesson |
|---|---|---|
| KIS GET with output1/output2 parsing | `src/trading/kis/account.py:10-75` | Field-mapping pattern, paper/live tr_id dispatch |
| Atomic UPDATE + audit_log | `src/trading/kis/order.py:128-198` | Step 3 transactional pattern (commit-on-success, audit fallback on error) |
| APScheduler cron registration | `src/trading/scheduler/runner.py:95+ (main)` | `_wrap()` + `_safe_call()` + KST timezone |
| Test fixtures for KIS mock | `tests/risk/test_market_safety_overheat.py`, `tests/screener/test_overheat_softening.py` | Existing mocks reuse pattern |
| Postgres ON CONFLICT UPSERT | _(no existing example in src/trading/)_ | Will be SPEC-029's first |

---

## 6. Risks and constraints

| Risk | Severity | Mitigation |
|---|---|---|
| KIS response field names not verified | HIGH | First-call payload logging + assertion + `@MX:WARN`; manual cross-check against KIS portal during /moai run |
| KIS rate limit during retroactive backfill | MED | Existing `_is_rate_limited()` + 4-retry backoff handles it; no extra work |
| Same order updated concurrently (cron + CLI) | MED | `SELECT ... FOR UPDATE` row lock |
| positions table grows unbounded over time | LOW | qty=0 rows retained for avg-cost history; periodic cleanup is out of scope |
| paper KIS may report different field shapes than live | MED | Tests must cover both tr_ids; first paper deploy will surface mismatches |
| SPEC-022/023 (universe expansion) behavior changes once positions has data | MED | This is the intended outcome; regression tests must run end-to-end |
| Migration 022 (filled_at column) if missing | LOW | Inspect schema first; add migration only if needed |
| 12 already-submitted orders today could mass-update on first deploy | LOW | Single fill_sync call handles them; auditable via audit_log |

---

## 7. Out of scope (deferred)

- **briefing 잔액% 계산식 수정** (SPEC-028 또는 SPEC-030 후보) — distinct concern: KIS balance API semantics misuse, separate from fill sync
- **SPEC-028 6 REQ** (overheat per-day cap, halt-state gate, sector classifier, circuit-breaker reset, ticker name display, balance display integrity) — distinct branch
- **다음 영업일 자동 미체결 주문 취소/이관 정책** — separate SPEC
- **live mode 활성화** — `live_unlocked=false` 유지
- **잔고/주문가능금액 변화 polling** — separate inquiry endpoint
- **종목별 손익 추적 (taxable lot-by-lot)** — separate concern

---

## 8. Verification surface (user review)

This research.md is a verification artifact per MoAI plan workflow Phase 0.5. The user should review and flag misunderstandings before SPEC creation:

- [ ] Section 1 — debug findings match user's recollection of today's session
- [ ] Section 2 — affected code areas are complete (no missing dependencies)
- [ ] Section 3 — KIS API contract assumptions are acceptable (verify on first call is OK)
- [ ] Section 4 — implementation approach (60s cron polling, fills.py + positions.py split) is approved
- [ ] Section 6 — risk mitigations are sufficient

Any "NOTE: ..." inline annotation added to this file will be addressed in the next annotation cycle.

---

**Sources consulted:**
- KIS Developers portal (apiportal.koreainvestment.com) — blocked 403, contract inferred from web search summaries
- [KIS official open-trading-api repo](https://github.com/koreainvestment/open-trading-api) — file listings only, no schema visible
- [python-kis (Soju06)](https://github.com/Soju06/python-kis) — high-level wrapper, schema abstracted
- [wikidocs.net/239581](https://wikidocs.net/239581) — Korean Python tutorial, blocked 403
- Live codebase audit: `src/trading/kis/order.py`, `src/trading/kis/account.py`, `src/trading/kis/client.py`, `src/trading/scheduler/runner.py`, `src/trading/risk/limits.py`, `src/trading/personas/orchestrator.py`
- Live DB inspection: `docker exec trading-postgres psql ...` on `orders`, `positions`, `\dt` 2026-05-26 22:00 KST
