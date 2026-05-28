---
id: SPEC-TRADING-029
type: plan
status: draft
created: 2026-05-26
---

# SPEC-TRADING-029 — Implementation Plan

## Strategic Approach

본 SPEC 은 코드베이스에 0 라인으로 존재하지 않는 **post-submission 라이프사이클**
을 신규로 추가한다. 기존 KIS 주문 제출 코드 (`src/trading/kis/order.py`) 와
KIS REST client (`src/trading/kis/client.py`) 는 변경 없이 재사용한다 (research.md
§2.1, §2.2). 본 SPEC 의 모든 신규 코드는 기존 5 persona / scheduler / risk
시스템에 직교 (additive only) 한다.

### Core Architecture Decision Records

#### ADR-029-1: 단일 모듈 `src/trading/kis/fills.py` 우선, db split optional

**결정**: 체결조회 + orders 전이 + positions UPSERT 의 3 함수를 우선 단일 모듈
`src/trading/kis/fills.py` 에 배치. 파일이 ~300 LOC 이내로 유지되면 그대로,
초과 시 positions 로직만 `src/trading/db/positions.py` 로 분리.

**Why**: research.md §4.1 - §4.2 의 권장. positions UPSERT 가 본 SPEC 의
첫 ON CONFLICT 사용처이고 (코드베이스 내 기존 예제 없음 — research.md §5),
fills.py 와 가까이 있는 편이 초기 maintenance 가 쉬움. fills.py 가 커지면
즉시 split.

**Trade-off**: 단일 모듈 시 fills.py 가 KIS API + DB 양쪽 책임을 짊어짐. 분리
시 import 그래프가 더 명확하지만 초기에는 over-engineering.

#### ADR-029-2: APScheduler 60s polling, WebSocket / push 미사용

**결정**: 체결 확인은 KIS REST `inquire-daily-ccld` 60 초 polling. WebSocket
push / KIS event stream 은 미사용.

**Why**:

- SPEC-024 의 KIS WebSocket 통합은 Stage 2 deferred (still planning-only)
- 60s × 390min = 390 calls/day 가 KIS 모의투자 sustained limit (초당 5회) 의
  fraction 에 불과
- 60s 지연이 박세훈 페르소나의 mid-term horizon (수일 ~ 수주) 에서 acceptable
- WebSocket 도입은 SPEC-024 Phase 2 와 함께 별도 SPEC 으로 마이그레이션

**Trade-off**: 1-second 단위 fill 감지 불가. 단, paper 트레이딩에서 1 주 단위
거래는 시장가 즉시 체결이 일반적이므로 60s 내 감지 가능.

#### ADR-029-3: TDD 우선 (RED → GREEN → REFACTOR)

**결정**: 본 SPEC 은 새로운 코드 작성이 대부분 (체결 라이프사이클이 0 라인 →
+ ~400 LOC) 이고 기존 동작 분석 (DDD ANALYZE-PRESERVE-IMPROVE) 필요성이 낮다.
TDD 사이클 RED → GREEN → REFACTOR 가 적합.

**Why**: research.md §1.3 가 확인한 대로 보존해야 할 기존 동작이 없다 (체결
확인 코드 자체가 0 hit). KIS mock fixture (research.md §5 의 `tests/risk/`,
`tests/screener/`) 가 이미 존재하여 RED 단계 진입이 용이.

**Trade-off**: 첫 KIS 호출 응답 페이로드를 보기 전 파싱 mock 을 작성해야 하는
순서가 필요 — Phase A 에서 mock 응답 fixture 를 research.md §3.3 의 best-guess
스키마 기준으로 만들고, Phase E 의 실제 호출에서 mismatch 발견 시 fixture 와
파서를 함께 수정.

#### ADR-029-4: `positions.qty=0` 행 보존, DELETE 금지

**결정**: SELL 체결로 qty 가 0 에 도달해도 positions row 는 그대로 유지하고
DELETE 하지 않는다. avg_cost 는 마지막 BUY 시점 값 그대로.

**Why**:

- avg-cost 이력 보존 → 추후 종목별 누적 손익 분석 가능
- ON CONFLICT UPSERT 가 단순화 (re-buy 시 기존 row 가 그대로 사용됨, avg_cost
  공식이 자연스럽게 동작: qty=0 인 경우 새 fill_qty * fill_price 가 평균이 됨,
  단 0 나눗셈 방지를 위해 가중평균 분모가 0 인 경우 새 fill_price 로 reset)
- 행 수가 크게 증가하지 않음 (KOSPI 전체 ~2000 ticker, 실거래 종목은 수십 단위)

**Trade-off**: positions 행이 계속 누적. 주기적 cleanup 은 별도 SPEC.

#### ADR-029-5: 가중평균 정수 산술

**결정**: avg_cost 계산은 Python 내에서 정수 산술 + 마지막 round 로 처리,
`avg_cost integer` 컬럼 타입 유지.

**Why**: 부동소수점 누적 drift 방지. KRW 단위로 1원 미만 손실은 acceptable
(주식 가격이 정수 KRW 단위이므로 의미 없음).

**Trade-off**: 정수 round-down 으로 인한 극소 drift (수십 회 매수 누적 시 ~1원
이내) — 무시 가능.

---

## Phase A — TDD RED (failing tests with KIS mock fixtures)

### Scope

KIS 체결조회 응답 mock fixture 작성 + 3 함수 (`inquire_fills_today`,
`apply_fill_to_order`, `apply_fill_to_position`) 의 failing 테스트 작성.

### Pre-conditions

- `tests/risk/`, `tests/screener/` 의 KIS client mock pattern 검토 (research.md §5)
- `orders`, `positions`, `audit_log` 스키마 read-only 확인

### Milestones (priority-based)

#### Primary Goal: Mock fixture
- `tests/kis/conftest.py` 또는 `tests/kis/fixtures/inquire_ccld_response.json`:
  research.md §3.3 의 best-guess 스키마 기반 sample response
  - 1 row: tot_ccld_qty == ord_qty (filled scenario)
  - 1 row: 0 < tot_ccld_qty < ord_qty (partial scenario)
  - 1 row: tot_ccld_qty=0, cncl_yn=N, rfus_yn=N (still pending scenario)
  - 1 row: cncl_yn='Y' (cancelled scenario)
  - 1 row: rfus_yn='Y' (rejected scenario)
- KIS REST mock helper (`tests/kis/_mock_client.py`) — `KisClient.get()` 응답을
  fixture JSON 으로 stub

#### Primary Goal: Failing tests for `inquire_fills_today`
- `tests/kis/test_fills_inquiry.py`:
  - `test_inquire_fills_returns_parsed_rows`: mock response → 5 FillRow 객체
  - `test_inquire_fills_paper_uses_VTTC8001R_tr_id`
  - `test_inquire_fills_live_uses_TTTC8001R_tr_id`
  - `test_inquire_fills_today_request_params_match_spec` (research.md §3.2)
  - `test_inquire_fills_handles_rate_limit_via_existing_retry`

#### Secondary Goal: Failing tests for `apply_fill_to_order`
- `tests/kis/test_fills_order_transition.py`:
  - `test_filled_when_tot_ccld_eq_ord_qty`
  - `test_partial_when_tot_ccld_lt_ord_qty`
  - `test_cancelled_when_cncl_yn_y`
  - `test_rejected_when_rfus_yn_y_with_reason`
  - `test_noop_when_pending`
  - `test_concurrent_transition_blocked_by_for_update` (uses two connections)
  - `test_orders_response_jsonb_records_reject_reason`

#### Secondary Goal: Failing tests for `apply_fill_to_position`
- `tests/db/test_positions_upsert.py`:
  - `test_first_buy_inserts_position_with_correct_avg_cost`
  - `test_second_buy_recomputes_weighted_avg_cost` (integer arithmetic)
  - `test_sell_decrements_qty_avg_cost_unchanged`
  - `test_sell_to_zero_qty_retains_row` (no DELETE)
  - `test_audit_log_position_updated_emitted`

### Validation
- 전체 신규 테스트 RED 상태 (failing — 모듈 미존재 또는 NotImplementedError)
- 기존 test suite 회귀 없음 (`pytest tests/` 전체 green 유지)

### Risks
- KIS 응답 필드명 미검증 (research.md §6 HIGH risk): Phase E 의 실거래 API
  호출에서 mismatch 발견 시 fixture + 파서를 함께 수정. 본 phase 의 mock 은
  research.md §3.3 의 best-guess 그대로 두고 `@MX:WARN` + `@MX:REASON` 적용.

---

## Phase B — TDD GREEN (implement modules, all tests pass)

### Scope
`src/trading/kis/fills.py` 신설 + (필요 시) `src/trading/db/positions.py` 신설.
Phase A 의 모든 테스트 통과까지 minimal implementation.

### Pre-conditions
- Phase A RED 상태 확인 (모든 신규 테스트 failing)
- `orders.filled_at` 컬럼 존재 여부 확인 — 없으면 Phase D 먼저 실행

### Milestones (priority-based)

#### Primary Goal: `src/trading/kis/fills.py`
구조:
```
class FillRow (dataclass):
    kis_order_no: str
    ticker: str
    side: str  # 'buy' / 'sell'
    ord_qty: int
    tot_ccld_qty: int
    avg_price: int
    cncl_yn: bool
    rfus_yn: bool
    rfus_reason: str | None

def inquire_fills_today(client: KisClient) -> list[FillRow]:
    """GET /uapi/domestic-stock/v1/trading/inquire-daily-ccld.
    paper tr_id=VTTC8001R, live tr_id=TTTC8001R.
    Returns parsed FillRow list (today's fills only).
    """

def apply_fill_to_order(fill: FillRow, conn) -> str:
    """SELECT FOR UPDATE → UPDATE orders → audit_log. Returns new status."""

def apply_fill_to_position(fill: FillRow, conn, *, new_status: str) -> None:
    """UPSERT positions for BUY, UPDATE for SELL. audit_log POSITION_UPDATED."""

def fill_sync(*, dry_run: bool = False) -> dict:
    """Orchestrator. Single transaction per fill. Returns summary stats."""
```

- KIS 응답 파싱 블록에 `@MX:WARN` + `@MX:REASON="KIS field names provisional;
  verify on first call payload"` 적용
- 가중평균 계산 블록에 `@MX:NOTE="integer arithmetic to match positions.avg_cost
  integer column type"` 적용
- `fill_sync` 함수에 `@MX:ANCHOR` 적용 (fan_in: scheduler cron + CLI subcommand
  + future manual callers)

#### Secondary Goal: `src/trading/db/positions.py` (conditional split)
ADR-029-1 에 따라 fills.py 가 ~300 LOC 초과 시에만 split. 그렇지 않으면 본 phase
스킵.

### Validation
- 전체 Phase A 테스트 GREEN
- 기존 test suite 회귀 없음
- `ruff check src/trading/kis/fills.py` + `pyright` (또는 mypy) clean

### Risks
- 가중평균 정수 산술 edge case (qty=0 인 row 에 SELL 도착 시 0 나눗셈): GREEN
  단계에서 명시적 GREATEST + 분모 0 가드
- ON CONFLICT 처음 사용 → SQL 문법 오류: 단위 테스트로 보호

---

## Phase C — Scheduler integration + CLI subcommand

### Scope
APScheduler 등록 + `trading fill-sync` CLI 서브커맨드. REQ-029-4 / REQ-029-5
구현.

### Pre-conditions
- Phase B GREEN

### Milestones (priority-based)

#### Primary Goal: Scheduler cron
- `src/trading/scheduler/runner.py:main()` 에서 `_wrap(fill_sync)` 를 60s
  interval cron 으로 등록 (KST 09:00–15:30, KRX trading day guard)
- `_safe_call()` 로 wrap → 실패 시 log 만 출력, 스케줄러 crash 금지
- 기존 cron (pre_market 07:30 / adaptive intraday / news_crawl 등) 회귀 없음
  확인

#### Secondary Goal: CLI subcommand
- `src/trading/cli.py` 에 `fill-sync` 서브커맨드 추가 (기존 `halt` / `resume`
  옆에 nest)
- Flags: `--start YYYYMMDD` (INQR_STRT_DT override), `--dry-run` (zero DB
  writes, stdout-only intended transitions)
- Exit code 0 (success), 1 (KIS error), 2 (DB error)

### Validation
- 통합 테스트 `tests/scheduler/test_fill_sync_cron.py`: cron 등록 확인,
  KRX guard 동작, 시장 시간 외 no-op
- 통합 테스트 `tests/cli/test_fill_sync_command.py`: `--dry-run` 시 DB 변경
  없음 확인 (before/after row count 동일)
- Manual smoke: docker compose 환경에서 `trading fill-sync --dry-run` 실행

### Risks
- 시장 시간 guard 누락 → 휴장일 / 야간 시간에도 KIS 호출 시도: `_wrap()` 의
  KRX trading day check 그대로 사용 (`scheduler/runner.py` 기존 패턴)
- cron + CLI 동시 실행 race: REQ-029-2 의 `SELECT ... FOR UPDATE` 가 보호

---

## Phase D — Migration (conditional)

### Scope
`orders.filled_at timestamptz` 컬럼 존재 여부 확인. 없으면 마이그레이션 022
추가.

### Pre-conditions
- `\d orders` 로 컬럼 직접 확인 (Phase A 시작 전 또는 Phase B 시작 전 권장)

### Milestones

#### Primary Goal (conditional)
- 컬럼 부재 확인 시: `migrations/022_add_filled_at.sql` (또는 alembic
  revision) 작성
  ```sql
  ALTER TABLE orders ADD COLUMN IF NOT EXISTS filled_at timestamptz NULL;
  CREATE INDEX IF NOT EXISTS idx_orders_filled_at ON orders(filled_at)
    WHERE filled_at IS NOT NULL;
  ```
- 컬럼 존재 시: 본 phase 스킵

### Validation
- 마이그레이션 후 `\d orders` 에 `filled_at` 컬럼 존재
- 기존 rows 의 `filled_at IS NULL` 확인 (NULLable 으로 추가됨)

### Risks
- alembic 또는 raw SQL migration tool 선택 — 기존 프로젝트 패턴 확인 후 결정

---

## Phase E — Live verification (paper mode, real KIS API)

### Scope
오늘 (또는 다음 영업일) 의 12 개 already-submitted orders 를 첫 fill_sync
호출에서 실거래 API 응답으로 처리. KIS 응답 페이로드 검증.

### Pre-conditions
- Phase A ~ C 통과
- redeploy 완료
- paper 모드 active

### Milestones

#### Primary Goal: First-call payload verification
- 첫 fill_sync 호출 시 응답 페이로드 전체 (`output1[]`, `output2[]`) 를
  `[INFO] KIS inquire-daily-ccld response` 로깅
- research.md §3.3 의 best-guess 필드명과 cross-check
- mismatch 발견 시 즉시 fills.py 파싱 블록 수정 + Phase A fixture 동시 수정 +
  retest

#### Primary Goal: Retroactive sync
- 오늘 paper 모드의 10 submitted orders 가 first fill_sync 호출에서 transition
  되는지 audit_log 로 확인
- `positions` 테이블에 BUY 체결분이 UPSERT 되었는지 확인
- daily report "체결 N건" 메시지가 실제 체결 수를 정확히 반영하는지 확인

#### Secondary Goal: MX:WARN release
- 응답 필드명 검증 완료 시 fills.py 의 `@MX:WARN` 제거 → `@MX:NOTE` 로 강등
  (검증된 매핑 기록)

### Validation
- audit_log 에 ORDER_FILLED / ORDER_PARTIAL / ORDER_CANCELLED / ORDER_REJECTED_BY_KIS
  / POSITION_UPDATED event 가 실제 발생
- `SELECT status, COUNT(*) FROM orders WHERE DATE(ts)=current_date GROUP BY 1`
  결과가 submitted=0 + filled/partial/cancelled/rejected 의 합
- SPEC-022/023 의 universe expansion 이 실제 holdings 를 반영 (지난주까지 0
  rows 였던 `data/universe.py` 쿼리가 결과 반환)

### Risks
- KIS 응답이 best-guess 와 크게 다르면 Phase E 가 길어짐 — 응답 로깅 + 분석
  + fixture 재작성 + retest. 본 SPEC 의 acceptance 는 Phase E 통과까지.

---

## Tech Stack

기존 의존성 그대로, 신규 의존성 없음:

- **HTTP**: `httpx` (via `KisClient` in `src/trading/kis/client.py`)
- **DB**: `psycopg` (via existing `src/trading/db/session.py` connection helper)
- **Scheduler**: `APScheduler.BlockingScheduler` (existing)
- **CLI**: `typer` 또는 `argparse` (기존 `trading halt` / `resume` 와 동일 framework)
- **Testing**: `pytest`, `pytest-mock`, existing KIS mock helpers

Python 3.13+ (이미 프로젝트 표준)

---

## Risk Matrix (from research.md §6, condensed)

| Risk | Severity | Mitigation |
|---|---|---|
| KIS response field names not verified | HIGH | Phase A mock + Phase E first-call payload logging + `@MX:WARN` + `@MX:REASON` 블록까지 검증 보류 |
| KIS rate limit during retroactive backfill | MED | 기존 `_is_rate_limited()` + 4-retry backoff 그대로 (research.md §2.2) |
| Same order updated concurrently (cron + CLI) | MED | `SELECT ... FOR UPDATE` row lock (REQ-029-2 constraint) |
| `positions` row 누적 (qty=0 retained) | LOW | 의도적 보존 (ADR-029-4); cleanup 은 별도 SPEC |
| Paper vs live API field shape divergence | MED | Phase A fixture 가 paper 기준 → live 전환 시 별도 검증 (live mode 자체가 본 SPEC 의 out-of-scope) |
| SPEC-022/023 동작 변화 (positions 가 비지 않게 됨) | MED | 의도된 결과. Phase E regression test 로 universe expansion 정상화 확인 |
| Migration 022 누락 시 filled_at INSERT 실패 | LOW | Phase D 컬럼 존재 확인을 Phase B 전에 수행 |
| 12 already-submitted orders 의 mass-update | LOW | 첫 fill_sync 한 번에 처리됨, audit_log 로 추적 가능, `--dry-run` 으로 사전 inspect 가능 |
| 가중평균 정수 산술의 누적 drift | LOW | KRW 단위로 무의미 (수십 회 매수 누적 시 ~1원 이내) |
| `orders.fill_price` NULL → NOT NULL 전이 시 기존 row 처리 | LOW | NULLable 그대로 유지, 신규 row 만 채움 |

---

## MX Tag Strategy

- `@MX:ANCHOR` on `fill_sync()` 함수 (fan_in ≥ 3: scheduler cron + CLI
  subcommand + 추후 manual / test 호출)
- `@MX:WARN` + `@MX:REASON="KIS field names provisional per research.md §3.3;
  first-call payload verification required before removing this warning"` on
  the KIS response parsing block in `inquire_fills_today()` (until Phase E
  validates the field mapping)
- `@MX:NOTE="weighted-average cost uses integer arithmetic to match
  positions.avg_cost integer column type; sub-KRW drift is acceptable"` on the
  avg_cost calculation block in `apply_fill_to_position()`
- `@MX:NOTE="orders.status transition matrix per SPEC-029 REQ-029-2 §3.4
  decision table"` on the status branching block in `apply_fill_to_order()`
- `@MX:TODO` 는 본 SPEC 에 해당 없음 (TDD GREEN 시점에 모두 해소)

---

## Reference Implementations (from research.md §5)

| Reference | Location | Lesson |
|---|---|---|
| KIS GET with output1/output2 parsing | `src/trading/kis/account.py:10-75` (`balance()`) | Field-mapping pattern, paper/live tr_id dispatch via `client.is_paper` |
| Atomic UPDATE + audit_log transactional | `src/trading/kis/order.py:128-198` | Step 3 commit-on-success, audit_log fallback on error |
| APScheduler cron + KRX guard | `src/trading/scheduler/runner.py:95+ (main)` | `_wrap()` + `_safe_call()` + KST timezone |
| KIS client mock fixture pattern | `tests/risk/test_market_safety_overheat.py`, `tests/screener/test_overheat_softening.py` | Mock `KisClient.get()` return value 패턴 |
| Postgres ON CONFLICT UPSERT | *(no existing example)* | 본 SPEC 의 첫 사용처 — Postgres docs §6.4 |

---

## Cross-Phase Quality Gates

- 모든 신규 코드 test coverage ≥ 85% (TRUST 5)
- `ruff check` + (가능 시) `pyright` clean
- 모든 신규 SQL parameterised query 사용 (SQL injection 방지)
- 기존 test suite green 유지 (회귀 없음)
- KIS API rate limit 우회 금지 — 기존 retry mechanism 만 사용
- live mode 진입 코드 추가 금지 (`live_unlocked=false` 유지)
- `audit_log` 가 모든 status transition + position update 를 기록
- Phase E 통과 시 `@MX:WARN` 제거 (KIS 필드 검증 완료 마커)

---

## Dependencies and Sequencing

```
Phase D (filled_at column check, optional migration)
    ↓
Phase A (TDD RED with mock fixtures)
    ↓
Phase B (TDD GREEN — implementation)
    ↓
Phase C (Scheduler + CLI)
    ↓
Phase E (Live KIS API verification + retroactive sync of today's 12 orders)
```

- Phase D 는 Phase B 시작 전 완료 (filled_at INSERT 가 컬럼을 요구)
- Phase A → B → C 는 일반적 TDD 사이클 직렬
- Phase E 는 redeploy 후 실거래 paper API 검증 — 본 SPEC 의 최종 acceptance gate
- 모든 phase 는 기존 SPEC-022 / 023 / 025 / 026 의 동작 회귀 없이 진행

---

# v0.2.0 Redesign Plan (REDESIGN — supersedes data-source parts of Phase A–E)

> v0.1.0 Phase A–E 는 inquire-daily-ccld 기반이었고 paper 환경에서 실패했다.
> v0.2.0 은 검증된 `inquire-balance` 로 데이터 소스를 전환하고, 종목명 표시 +
> 잔액 % 버그를 흡수한다. Methodology: **TDD (RED → GREEN → REFACTOR)**.
> migration `022_add_filled_at.sql` 는 **이미 적용됨 — 유지** (Phase D 재실행 불필요).

## v0.2.0 Architecture Decision Records

### ADR-029-6: 데이터 소스 = inquire-balance (inquire-daily-ccld 폐기)

**결정**: `inquire-daily-ccld` (VTTC8001R) 폐기, `inquire-balance` (VTTC8434R)
로 전환. `src/trading/kis/account.py:balance()` 재사용.

**Why**: 2026-05-28 라이브에서 inquire-daily-ccld 가 paper 환경 모든 CCLD_DVSN
값에 대해 `output1=[]` + msg_cd 70070000 ("모의투자 조회 내역 없음") 확정. 동일
계좌의 inquire-balance 는 보유 5 종목 정상 반환.

**Trade-off**: balance 는 주문별 체결이 아닌 ticker 누적 보유만 제공 → 주문 ↔
체결 1:1 매핑 불가. FIFO attribution (ADR-029-7) 로 우회.

### ADR-029-7: FIFO attribution 으로 orders 전이

**결정**: ticker 별 `newly_filled = max(0, held_qty - already_accounted)` 를
계산해 submitted BUY 주문에 oldest-first 로 배분. `already_accounted` =
해당 ticker 의 이미 filled/partial 된 row 들의 `fill_qty` 합.

**Why**: balance 가 누적 보유만 주므로, 직전 sync 까지 인식한 체결분을 빼야 이번
cycle 의 신규 체결분을 안다. FIFO 는 가장 직관적이고 결정론적.

**Trade-off**: 동일 ticker 동시 다발 주문의 정확한 주문-체결 매핑은 보장 못 함
(KIS 가 부분 체결을 어느 주문에 귀속시키는지 알 수 없음). paper 중기 horizon
에서 acceptable. 정밀 매핑이 필요하면 inquire-psbl-rvsecncl 도입 (deferred).

### ADR-029-8: positions = balance mirror (정수 가중평균 폐기)

**결정**: `positions` 를 매 sync 마다 balance holdings 의 직접 미러로 UPSERT.
`avg_cost = KIS pchs_avg_pric`. balance 에 없는 보유 ticker 는 `qty=0` (행 보존).
v0.1.0 의 정수 가중평균 재구성 (ADR-029-5) **무효화**.

**Why**: balance 가 source of truth 이므로 로컬에서 평단가를 재계산할 이유가
없다. drift 제거 + 코드 단순화. positions 가 항상 KIS 실제 보유와 일치.

**Trade-off**: positions 가 balance polling 주기에 종속 (60s). 즉시성은 약간
떨어지나 mid-term horizon 에서 무의미.

### ADR-029-9: 종목명 = pykrx resolver + lru_cache

**결정**: `trading.data.ticker_names.ticker_name(ticker)` 신설.
`pykrx.stock.get_market_ticker_name` + `functools.lru_cache`. 폴백 체인:
pykrx → `context.TICKER_NAMES` static dict → `None`.

**Why**: 매매 알림 시점에 갓 제출된 주문은 balance holdings 에 아직 없을 수
있어 balance `prdt_name` 이 unreliable. pykrx 는 KRX 전 종목을 독립 해석.
pykrx 는 이미 의존성 (`pykrx_adapter.py`), KRX_ID/KRX_PW 설정 완료.

**Trade-off**: pykrx 첫 호출 지연 (lru_cache 로 1 회만). 네트워크 실패 시 폴백.

### ADR-029-10: 잔액 % 분모 = invest_basis (cash_d2 + stock_eval)

**결정**: `balance()` 반환 dict 에 `invest_basis = cash_d2 + stock_eval` 추가.
callers 는 `cash_pct = cash_d2/invest_basis`, `equity_pct = stock_eval/invest_basis`
로 계산 → 합계 100% 보장. headline `total_assets` (tot_evlu_amt) 는 그대로 표시.

**Why**: KIS `tot_evlu_amt ≠ dnca_tot_amt + scts_evlu_amt` (검증:
8,787,740 + 3,128,400 = 11,916,140 ≠ 9,919,870). 분자와 분모가 다른 필드를
쓰면 합계 ≠ 100%. 일관된 분모로 해결. 2 개 caller site 중복 방지 위해 balance()
가 basis 를 제공.

**Trade-off**: invest_basis 는 KIS 의 "총자산"과 다름 (미수/D+2/미실현손익 차이).
이는 의도된 것 — % 는 "투자 원금 기준 배분"을, headline 금액은 "KIS 총평가"를
각각 표현. tot_evlu_amt 의 진짜 의미를 account.py 주석에 명시.

---

## Phase F — TDD RED (v0.2.0 failing tests)

### Scope
balance-reconcile / positions-mirror / ticker-name / pct 의 failing 테스트 작성.

### Pre-conditions
- v0.1.0 의 `tests/kis/test_fills_inquiry.py` / `test_fills_order_transition.py` /
  `tests/db/test_positions_upsert.py` 검토 후, inquire-daily-ccld 기반 테스트는
  **삭제 또는 balance-reconcile 기반으로 재작성** (오래된 가정 제거)
- `account.balance()` 의 holdings/summary 필드 read-only 재확인

### Milestones (priority-based)

#### Primary Goal — balance reconcile 테스트
`tests/kis/test_fills_balance_reconcile.py` (NEW):
- `test_balance_reconcile_uses_inquire_balance_not_daily_ccld`
  (account.balance 가 호출되고 inquire-daily-ccld 는 호출 안 됨 — mock assert)
- `test_single_submitted_buy_fills_when_held_qty_matches` (AC-029-9)
- `test_fifo_allocation_across_two_submitted_orders` (AC-029-10)
- `test_partial_when_allocation_lt_order_qty` (AC-029-10)
- `test_noop_when_newly_filled_zero` (AC-029-11)
- `test_held_qty_less_than_accounted_clamps_to_zero` (EC-029-7)
- `test_no_cancel_or_reject_autotransition` (balance-only scope)

#### Primary Goal — positions mirror 테스트
같은 파일 또는 `tests/kis/test_positions_mirror.py`:
- `test_positions_upsert_from_balance_holdings` (AC-029-12)
- `test_avg_cost_taken_from_pchs_avg_pric_not_recomputed` (AC-029-12)
- `test_unheld_ticker_zeroed_not_deleted` (AC-029-12)
- `test_position_synced_audit_emitted`
- `test_held_ticker_without_local_order_only_mirrors_positions` (EC-029-8)

#### Secondary Goal — ticker name + pct 테스트
- `tests/data/test_ticker_names.py` (NEW): pykrx mock → name 반환,
  lru_cache 1 회 호출 (EC-029-9), 예외 시 TICKER_NAMES 폴백, 최종 None (AC-029-13)
- `tests/kis/test_account_balance_basis.py` (NEW): `invest_basis` 필드 존재,
  값 = cash_d2 + stock_eval, invest_basis=0 가드 (AC-029-15)
- `tests/alerts/test_trade_briefing_pct.py` (NEW): cash_pct+equity_pct==100
  (AC-029-14), name 이 메시지에 렌더링됨 (AC-029-13)

### Validation
- 전체 신규 테스트 RED. 기존 비관련 suite green 유지.

### Risks
- balance holdings 의 정확한 필드 (`hldg_qty`, `pchs_avg_pric`) 는 account.py
  에서 이미 검증됨 (라이브 5 종목 반환) → v0.1.0 의 HIGH-risk 필드 미검증 문제
  해소. `@MX:WARN` 불필요.

---

## Phase G — TDD GREEN (rewrite fills.py + helpers)

### Scope
`src/trading/kis/fills.py` **전면 재작성** + `account.py` invest_basis 추가 +
`ticker_names.py` 신설. Phase F 테스트 전부 GREEN.

### Milestones (priority-based)

#### Primary Goal — `src/trading/kis/fills.py` REWRITE
신규 구조 (제안):
```
def reconcile_from_balance(client, *, dry_run=False) -> dict:
    """v0.2.0 orchestrator. balance() → FIFO orders transition + positions mirror.
    Replaces v0.1.0 inquire_fills_today/apply_fill_to_order/apply_fill_to_position."""

def _transition_orders_fifo(holdings, conn, *, dry_run) -> list[dict]:
    """Per-ticker: newly_filled = max(0, held_qty - already_accounted);
    allocate oldest-first to submitted BUY orders (FOR UPDATE)."""

def _mirror_positions(holdings, conn, *, dry_run) -> int:
    """UPSERT each held ticker (avg_cost=pchs_avg_pric); zero out unheld rows."""
```
- 기존 `fill_sync` 진입점 이름 유지 가능 (scheduler/CLI 호환) →
  `fill_sync(client, *, dry_run)` 가 내부적으로 `reconcile_from_balance` 호출.
  또는 scheduler/CLI 의 import 를 새 이름으로 갱신 (REFACTOR 단계 결정).
- `inquire_fills_today` / `apply_fill_to_order` / `apply_fill_to_position` /
  `FillRow` / `_FIRST_CALL` / inquire-daily-ccld 상수 **제거**.
- `@MX:ANCHOR` on `reconcile_from_balance` (fan_in: cron + CLI + tests).
- `@MX:NOTE` on FIFO attribution block (배분 규칙 = SPEC-029 v0.2.0 REQ-029-7).
- v0.1.0 의 `@MX:WARN` (KIS 필드 provisional) **제거** — balance 필드는 검증됨.

#### Primary Goal — `src/trading/kis/account.py`
- `balance()` 반환 dict 에 `"invest_basis": cash_d2 + stock_eval` 추가.
- `tot_evlu_amt` 의 의미 (미실현손익/ D+2 포함, basis 와 다름) 주석 보강.
- 기존 필드 (`cash_d2`, `stock_eval`, `holdings[].name=prdt_name`,
  `holdings[].avg_cost=pchs_avg_pric`, `holdings[].qty=hldg_qty`) 는 그대로 —
  변경 없음, 검증만.

#### Secondary Goal — `src/trading/data/ticker_names.py` (NEW)
```
@lru_cache(maxsize=2048)
def ticker_name(ticker: str) -> str | None:
    """pykrx → context.TICKER_NAMES → None. Lazy pykrx import."""
```

### Validation
- Phase F 전부 GREEN. 기존 suite 회귀 없음.
- `ruff check` + (가능 시) `pyright` clean.

### Risks
- `fill_sync` 이름 유지 vs 신규 이름: scheduler/CLI import 영향. GREEN 에서
  내부 위임으로 호환, REFACTOR 에서 정리.

---

## Phase H — Wire callers (orchestrator + telegram) + CLI/cron update

### Scope
종목명 + 잔액 % fix 를 caller site 에 연결. cron/CLI 가 balance reconcile 호출.

### Milestones (priority-based)

#### Primary Goal — orchestrator 2 개 caller site
`src/trading/personas/orchestrator.py` line ~1036, ~1460 의 `tg.trade_briefing(...)`:
- `name=None` → `name=ticker_name(sig["ticker"])`
- `ca_pct` / `eq_pct` 계산을 `total_assets` 분모 → `bal_after["invest_basis"]`
  분모로 교체 (invest_basis=0 가드 포함)

#### Primary Goal — `src/trading/alerts/telegram.py`
- `trade_briefing` 시그니처는 **변경 없음** (이미 `name` + `cash_pct` +
  `equity_pct` 수용/렌더). caller 가 일관된 값을 넘기도록 하는 것으로 충분.
- (선택) 방어적으로 trade_briefing 내부에서 `cash_pct + equity_pct` 가 100 을
  크게 벗어나면 경고 로그 — REFACTOR 판단.

#### Primary Goal — `src/trading/personas/context.py`
- `TICKER_NAMES` 를 "offline fallback only" 로 주석 강등. (resolver 가 1차 소스)
- context 내 종목명 lookup 이 있으면 `ticker_name()` 경유로 변경.

#### Secondary Goal — scheduler / CLI
- `src/trading/scheduler/runner.py`: `fill_sync` cron 이 balance reconcile 를
  호출하도록 유지/갱신 (cron 자체는 이미 등록됨 — 호출 대상만 검증).
- `src/trading/cli.py`: `trading fill-sync [--dry-run] [--start]` 가 balance
  reconcile 를 호출하도록 갱신. (`--start` 는 balance 에 의미 없으므로 deprecate
  또는 no-op 처리 — balance 는 항상 현재 시점. REFACTOR 에서 결정.)

### Validation
- `tests/scheduler/test_fill_sync_cron.py` (UPDATE), `tests/cli/test_cli_fill_sync.py`
  (UPDATE) GREEN.
- `tests/alerts/test_trade_briefing_pct.py` GREEN.

### Risks
- `--start YYYYMMDD` flag 가 balance 모델에서 무의미해짐 → 사용자 혼동 방지 위해
  명확한 deprecation 메시지.

---

## Phase I — REFACTOR + simplify

### Scope
중복 제거, 네이밍 정리 (`fill_sync` vs `reconcile_from_balance`), pct 계산
헬퍼 추출 여부 검토.

### Milestones
- pct 계산이 2 caller site 에 중복되면 작은 헬퍼로 추출 (`invest_basis` 기반).
- dead code (v0.1.0 잔재) 완전 제거 확인.
- 전체 suite green 유지, coverage ≥ 85%.

---

## Phase J — Live verification (paper, real KIS balance)

### Scope
redeploy 후 첫 reconcile cycle 이 paper 모드 보유 5 종목으로 submitted orders 를
실제 전이하고 positions 를 미러하는지 검증.

### Milestones
- 첫 cycle 후 `SELECT status, COUNT(*) FROM orders WHERE side='buy' AND
  DATE(ts)=current_date GROUP BY 1` 가 submitted 감소 + filled/partial 증가
- `SELECT * FROM positions WHERE qty > 0` 가 balance 5 종목과 일치
- Telegram 매매 알림에 종목명 표시 + `현금 % + 주식 % = 100%` 확인
- SPEC-022/023 universe expansion 이 positions 반영 (이전: 0 rows)

### Validation
- audit_log 에 ORDER_FILLED / ORDER_PARTIAL / POSITION_SYNCED event 발생
- 회귀 0 건

---

## v0.2.0 EXACT File-Change List (for /moai:2-run)

| File | Action | What |
|---|---|---|
| `src/trading/kis/fills.py` | **REWRITE** | balance reconcile + FIFO + positions mirror; remove inquire-daily-ccld, FillRow, weighted-avg, @MX:WARN |
| `src/trading/kis/account.py` | **EDIT** | add `invest_basis` field; document `tot_evlu_amt`; verify hldg_qty/pchs_avg_pric (no behavior change) |
| `src/trading/data/ticker_names.py` | **NEW** | `ticker_name()` pykrx + lru_cache + fallback chain |
| `src/trading/personas/context.py` | **EDIT** | demote TICKER_NAMES to fallback; route name lookups through resolver |
| `src/trading/alerts/telegram.py` | **EDIT (minimal)** | `trade_briefing` signature unchanged; optional defensive pct-sum log |
| `src/trading/personas/orchestrator.py` | **EDIT** | both caller sites (~1036, ~1460): `name=ticker_name(...)`, pct denominator → `invest_basis` |
| `src/trading/scheduler/runner.py` | **EDIT (verify)** | `fill_sync` cron now drives balance reconcile |
| `src/trading/cli.py` | **EDIT** | `fill-sync` subcommand drives balance reconcile; handle/deprecate `--start` |
| `src/trading/db/migrations/022_add_filled_at.sql` | **KEEP** | already applied — no change |
| `tests/kis/test_fills_balance_reconcile.py` | **NEW** | REQ-029-6/7/8 |
| `tests/kis/test_positions_mirror.py` | **NEW** (or fold into above) | REQ-029-8 |
| `tests/kis/test_account_balance_basis.py` | **NEW** | REQ-029-10 |
| `tests/data/test_ticker_names.py` | **NEW** | REQ-029-9 |
| `tests/alerts/test_trade_briefing_pct.py` | **NEW** | REQ-029-10 + name render |
| `tests/scheduler/test_fill_sync_cron.py` | **UPDATE** | cron → balance reconcile |
| `tests/cli/test_cli_fill_sync.py` | **UPDATE** | `--dry-run` zero-write on balance reconcile |
| `tests/kis/test_fills_inquiry.py` (v0.1.0) | **DELETE/REWRITE** | inquire-daily-ccld assumptions obsolete |
| `tests/kis/test_fills_order_transition.py` (v0.1.0) | **DELETE/REWRITE** | replaced by balance reconcile tests |
| `tests/db/test_positions_upsert.py` (v0.1.0) | **DELETE/REWRITE** | weighted-avg arithmetic removed |

## v0.2.0 Research note — inquire-psbl-rvsecncl (정정취소가능주문조회)

Context7 / GitHub `koreainvestment/open-trading-api` 조사 결과
(`examples_llm/domestic_stock/inquire_psbl_rvsecncl/`):

- Path: `/uapi/domestic-stock/v1/trading/inquire-psbl-rvsecncl`, tr_id `TTTC0084R`
  (live). 공식 예제에 **paper(V) tr_id 가 문서화되어 있지 않음**.
- Response fields 확인: `odno`, `orgn_odno`, `pdno`, `ord_qty`, `tot_ccld_qty`,
  `psbl_qty` (정정취소가능수량 = 사실상 미체결 잔량), `sll_buy_dvsn_cd`,
  `ord_dvsn_cd` 등 — 정밀 partial/cancel 구분에 충분한 필드를 가짐.
- **판단**: 이 엔드포인트는 inquire-daily-ccld 와 같은 "주문/체결 조회" 계열로,
  paper 환경에서 동일하게 빈 응답일 위험이 매우 높다. 사용자 선호(단순
  balance-only)에 따라 **현 시점 미도입 (deferred)**. 향후 미체결 stale order
  정리 / 정밀 체결가가 필요해질 때 별도 SPEC 에서 paper 가용성 검증 후 재고.
