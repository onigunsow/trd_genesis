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
