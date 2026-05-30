# SPEC-TRADING-038 — Research (codebase analysis)

> Live-readiness gate fixes (2건). 본 문서는 SPEC 작성 전 코드베이스 실측 결과다.
> 출처는 모두 현 HEAD(`ffa1736` 계열, 브랜치 `fix/SPEC-TRADING-026-overheating-softening`) 실파일.
> 과대 주장 금지(no-lies): 아래는 "지금 코드가 이렇다" 의 기록이며, 권고 수치는 run/사용자 확정 대상.

---

## 0. 배경 — 어디서 나왔나

엣지 검증 감사(edge-validation audit, SPEC-TRADING-037 컨텍스트)에서 **실거래(live) 전환 전에 반드시
닫아야 할 게이트**들이 표면화됐다. 그중 본 SPEC 은 **코드로 고칠 수 있는 2건**만 다룬다:

1. 일일 손실 회로차단 임계가 **−1.0%** 로 과도하게 빡빡함(REQ-038-1).
2. 익절(take-profit) 1일 1회 가드가 **인메모리 전용** → 컨테이너 재시작 시 리셋 → **이중 익절(반매도
   2회)** 위험(REQ-038-2).

세 번째 게이트(자격증명 회전/credential rotation)는 **운영자 수동 작업**이므로 본 SPEC 의 코드 범위
밖이다 → Non-Goals 에 "operator 가 별도 처리" 로 명시.

---

## 1. REQ-038-1 — 일일 손실 회로차단 임계

### 1-1. 현재 상수 (`src/trading/config.py` L27–L34)

```python
# REQ-RISK-05-1 — Five hard limits, expressed as fractions of capital.
# These are NOT modified by any persona output. The circuit breaker
# (src/trading/risk/limits.py, M5) enforces them before every order.
RISK_DAILY_MAX_LOSS: Final[float] = -0.01           # -1.0%
RISK_PER_TICKER_MAX_POSITION: Final[float] = 0.20    # 20.0%
RISK_TOTAL_INVESTED_MAX: Final[float] = 0.80         # 80.0%
RISK_SINGLE_ORDER_MAX: Final[float] = 0.10           # 10.0%
RISK_DAILY_ORDER_COUNT_MAX: Final[int] = 10
```

- 5개 하드 리밋은 **페르소나 출력으로 수정되지 않는** 상수다(회로차단이 매 주문 전 독립 강제).
- `RISK_DAILY_MAX_LOSS = -0.01` 은 **−1.0%**. 이것이 너무 빡빡하다는 게 본 요구의 핵심.

### 1-2. 환경변수 스타일 실측 (중요 — REQ-038-1 의 "configurable" 근거)

- config.py 의 **risk 상수 5개는 `Final` 모듈 상수**이며 **현재 env-var 로 오버라이드되지 않는다**.
- config.py 의 env-var 패턴은 `pydantic_settings.BaseSettings` 의 `Field(alias="...")` 형태로,
  **secret/계정/API 키**에만 쓰인다(`KIS_PAPER_APP_KEY`, `POSTGRES_USER`, `TRADING_MODE` 등 — L115~L180).
- 즉 "다른 리스크 상수처럼 env 로 구성 가능" 이라는 작업지시의 전제는 **현 코드에는 risk 상수 env-var
  선례가 없다**. → SPEC 에서 두 옵션 제시:
  - (A) `Final` 상수를 `float(os.getenv("RISK_DAILY_MAX_LOSS", "-0.025"))` 형태로 env 폴백(가장 가벼움),
  - (B) BaseSettings 필드로 승격(더 무거움, 페르소나-불변 하드리밋이라 과함).
  - **권고: (A)** — 하드 리밋은 BaseSettings(런타임 가변)보다 모듈 상수+env 폴백이 의미상 맞다. run 확정(Q).

### 1-3. 강제 지점 (`src/trading/risk/limits.py` L118–L121)

```python
# 3. daily loss (only blocks NEW orders, not the current loss-recovery sell)
pnl_pct = daily_pnl_pct(total_assets)
if pnl_pct <= RISK_DAILY_MAX_LOSS:
    chk.breaches.append(f"daily_loss: 오늘 손익 {pnl_pct * 100:.2f}% ≤ 한도 {RISK_DAILY_MAX_LOSS * 100:.2f}%")
```

- breach 메시지는 **`"daily_loss: ..."` 접두사**로 시작한다. 이 접두사가 하류(auto_resume)에서 키로 쓰인다(중요).
- 주석이 명시하듯 **daily_loss 는 NEW order 만 차단**하고 손실복구 매도(loss-recovery sell)는 막지 않는다.

### 1-4. ★ 진단 정정 (작업지시의 nuance 확인)

- limits.py 에는 `RISK_DAILY_ORDER_COUNT_MAX`(L113–116, **count**)와 `RISK_DAILY_MAX_LOSS`(L118–121,
  **loss**)가 **별개**다.
- 메모리·최근 halt 이력상 **최근 정지는 daily-order-COUNT(10건) 트립** 때문이지 daily-LOSS 트립이 아니다.
- 따라서 REQ-038-1 은 **"최근 거래 0건/정지의 원인 수정" 이 아니다**. 실제 P&L 스윙이 발생할 때를 위한
  **하드닝(hardening)** 이다. SPEC 에서 긴급성 과장 금지(no-lies).
- 추가 정합성: SPEC-037 이 **포지션별 스톱 플로어 −10%(또는 도출값)** 를 도입했으므로, 다종목 정상 스윙을
  포트폴리오 일일 한도가 수용해야 한다 → −1% 는 단일 포지션 스톱 한 번에도 트립될 수 있어 부정합.

### 1-5. 비자동재개 불변(SPEC-032) — REQ-038-1 acceptance 의 근거

`src/trading/risk/auto_resume.py`:

```python
# Prefix of a real-loss breach string (limits.py: "daily_loss: ..."). A loss is a
# capital-preservation event and is never auto-resumed (REQ-032-3b).
_DAILY_LOSS_PREFIX = "daily_loss"
...
return (False, "daily_loss", "; ".join(str(b) for b in breaches))   # 자동재개 거부
```

- 자동재개 판정은 **breach 문자열의 `"daily_loss"` 접두사**로 키잉된다. **임계 *값* 과 무관**.
- ⇒ `RISK_DAILY_MAX_LOSS` 를 −1% → −2.5% 로 **값만 바꿔도** daily_loss 트립은 여전히 `_DAILY_LOSS_PREFIX`
  로 인식되어 **자동재개되지 않는다**. 즉 "실손실은 여전히 수동 /resume 필요" 불변이 **공짜로 보존**된다.
- REQ-038-1 의 acceptance("daily-loss halt 는 NON-auto-resumable 유지")는 회귀 테스트로 이 불변을 잠그면 됨.

### 1-6. 영향 파일 요약 (REQ-038-1)

| 파일 | 변경 |
|---|---|
| `src/trading/config.py` | `RISK_DAILY_MAX_LOSS` 값 −0.01 → 도출값(권고 −0.025), env 폴백 추가 |
| `src/trading/risk/limits.py` | (값 참조만, 로직 무변경 — breach 접두사 유지) |
| `tests/risk/test_limits*.py`(기존) | 새 임계에서 P&L breach 트립 + daily_loss 비자동재개 회귀 |

---

## 2. REQ-038-2 — 익절 마커 DB 영속화

### 2-1. 현재 인메모리 가드 (`src/trading/watchers/position_watchdog.py` L44–L69)

```python
# In-memory per-ticker take-profit guard: ticker -> KST date the ticker was last
# taken. A new KST day naturally clears the guard (stored date != today).
# Single-scheduler process makes this sufficient (A-3); a container restart
# resets it, an accepted same-day limitation (SPEC-024 TickerThrottle precedent).
_TOOK_PROFIT: dict[str, date] = {}

def _took_profit_today(ticker: str) -> bool:
    marked = _TOOK_PROFIT.get(ticker)
    return marked is not None and marked == _today_kst()

def _mark_took_profit(ticker: str) -> None:
    _TOOK_PROFIT[ticker] = _today_kst()

def _reset_took_profit() -> None:
    _TOOK_PROFIT.clear()
```

- 가드는 **모듈 전역 dict** `_TOOK_PROFIT` 에만 존재 → **프로세스 재시작 시 소멸**.
- 주석 자체가 한계를 인정: "a container restart resets it, an accepted same-day limitation".
  본 SPEC 은 그 **"accepted limitation" 을 실거래 전에 닫는** 작업이다.

### 2-2. 가드가 쓰이는 결정 지점 (L100–L123)

```python
def classify_holding(pnl_pct, eff_stop, eff_take, took_profit_today, qty) -> tuple[str, int]:
    if eff_stop is None or eff_take is None:
        return ("skip", 0)
    if pnl_pct <= eff_stop:
        return ("stop", qty)
    if pnl_pct >= eff_take and not took_profit_today:   # ← 가드가 여기서 익절 반복 차단
        return ("take", max(1, qty // 2))                # ← 반(half) 매도
    return ("skip", 0)
```

- `took_profit_today` 가 False 면 **익절 반매도(qty//2)** 실행. 재시작으로 가드가 리셋되면:
  - 14:00 익절 반매도 → 재시작 → 14:30 `took_profit_today=False` → **또 반매도** = **이중 익절**.

### 2-3. 호출 흐름 (L154–L208, `poll_position_watchdog`)

- `*/5` 폴마다 holdings 순회 → `_took_profit_today(ticker)` 조회 → `classify_holding` → take 시
  `kis_sell` 직접 호출(halt/카운트 게이트 우회, SPEC-033) → `_mark_took_profit(ticker)`.
- 따라서 **`_took_profit_today` / `_mark_took_profit` 두 함수의 백킹 스토어만 DB 로 바꾸면** 호출부
  변경 최소(서명 유지). `classify_holding` 은 순수 함수라 무변경.

### 2-4. 마이그레이션 번호 — ★ 확정

- 디스크 실측: 가장 높은 마이그레이션은 **`026_edge_validation.sql`**. `027*.sql` **디스크에 없음**.
- SPEC-037 은 `027_exit_rule_sweep.sql` 을 **선택(optional)** 으로 **예약**만 함(아직 생성 안 됨, "필요 시").
- ⇒ 충돌 회피를 위해 본 SPEC 의 take-profit 마커 테이블은 **`028_position_action_markers.sql`** 로 한다.
  (027 은 SPEC-037 의 선택 마이그레이션 몫으로 남겨둠. 만약 SPEC-037 이 027 을 끝내 만들지 않으면 028 은
  비연속이 되지만, 번호 충돌보다 안전. run 에서 027 미사용 확정 시 027 로 당겨도 됨 — Q-3.)

### 2-5. 마이그레이션 하우스 스타일 (`026_edge_validation.sql` 모범)

```sql
CREATE TABLE IF NOT EXISTS daily_equity_snapshot ( ... );
CREATE INDEX IF NOT EXISTS daily_equity_snapshot_day_idx ON daily_equity_snapshot (trading_day);
COMMENT ON TABLE daily_equity_snapshot IS '...';
INSERT INTO schema_migrations (version) VALUES ('026_edge_validation') ON CONFLICT DO NOTHING;
INSERT INTO audit_log (event_type, actor, details)
VALUES ('SCHEMA_MIGRATED', 'init', '{"migration":"026_edge_validation"}'::JSONB);
```

규칙: 멱등(`CREATE TABLE IF NOT EXISTS` + `schema_migrations ON CONFLICT DO NOTHING`), raw SQL,
순차 번호, `migrate.py` 자동 발견, 재배포 후 `docker exec trading-app trading migrate` **수동 실행**
(자동 boot 미적용 — 하우스 스타일/메모리 확인).

### 2-6. 테스트 더블 패턴 (`tests/kis/test_fills_balance_reconcile.py`)

```python
class ScriptedCursor:
    # fetchone_queue / fetchall_queue 를 순서대로 소비. execute() 는 no-op 기록.
    def __init__(self, fetchone_queue=None, fetchall_queue=None): ...
    def execute(self, sql, params=None) -> None: ...
    def fetchone(self): ...      # fetchone_queue pop
    def fetchall(self): ...      # fetchall_queue pop
    def __enter__(self): ...
# _conn_factory(cursor), _audit_events(cursor) 헬퍼로 DB 호출을 결정적으로 모킹.
```

- REQ-038-2 의 재현 테스트는 이 `ScriptedCursor` 패턴으로 **DB-backed 마커 조회/기입을 결정적으로 모킹**한다.
  "재시작" 은 인메모리 dict 를 비우거나(`_reset_took_profit`) 새 모듈 상태로 두고, DB 마커가 살아있음을
  ScriptedCursor 의 `fetchone_queue` 로 흉내내어 `_took_profit_today` 가 True 를 유지함을 증명.

### 2-7. 멱등성 키 후보

- `position_action_markers(trading_day DATE, ticker TEXT, action TEXT, created_at TIMESTAMPTZ)` +
  **`UNIQUE (trading_day, ticker, action)`** → 같은 날 같은 종목 같은 액션의 중복 INSERT 를 DB 가 거부.
- `_mark_took_profit` = `INSERT ... ON CONFLICT (trading_day,ticker,action) DO NOTHING`.
- `_took_profit_today` = `SELECT 1 FROM position_action_markers WHERE trading_day=? AND ticker=? AND action='take_profit'`.
- 대안(작업지시): audit_log 재활용 쿼리. **권고: 전용 테이블**(명시적 UNIQUE 제약 = 이중 익절 DB 레벨 방지).

### 2-8. 영향 파일 요약 (REQ-038-2)

| 파일 | 변경 |
|---|---|
| `src/trading/db/migrations/028_position_action_markers.sql`(신규) | 전용 테이블 + UNIQUE 제약, 멱등 |
| `src/trading/watchers/position_watchdog.py` | `_took_profit_today`/`_mark_took_profit` 백킹을 DB 로(서명 유지). 인메모리 dict 는 제거 또는 캐시로 강등 |
| `tests/watchers/test_take_profit_persistence.py`(신규) | 재시작 시뮬레이션 재현 테스트(ScriptedCursor) |

---

## 3. 공통 제약 (양 요구 공통)

- **reproduction-first(RED→GREEN)** — money/risk 로직, CLAUDE.md HARD Rule 4.
- `.venv/bin/python -m pytest`(docker 이미지에 pytest 없음). 베이스라인 **950 passed**(메모리). 신규 회귀 0.
- 신규 코드 85%+ 커버리지. ruff(BLE001 → `# noqa: BLE001` 금지, 평범한 `except Exception:` 사용),
  타입힌트, bare except 금지, print 아닌 logging.
- paper only, live 잠금 유지(SPEC-002). 브랜치 `fix/SPEC-TRADING-026-overheating-softening`(신규 브랜치 금지).
- 커밋/배포는 오케스트레이터. 자격증명 회전은 **운영자 수동** — 코드 범위 밖(Non-Goals).

---

## 4. 미해결 질문(run/사용자 확정)

- **Q-1**: `RISK_DAILY_MAX_LOSS` 신규 값 — **−2.0% / −2.5% / −3.0%** 중. 리스크 오너 확정. 기본 권고 −2.5%.
- **Q-2**: env 구성 방식 — (A) 모듈 상수 + `os.getenv` 폴백(권고) vs (B) BaseSettings 필드 승격.
- **Q-3**: 마이그레이션 번호 — **028**(권고, 027 은 SPEC-037 예약) vs 027(SPEC-037 미사용 확정 시).
- **Q-4**: 마커 저장소 — **전용 테이블 `position_action_markers`**(권고, UNIQUE 제약) vs audit_log 쿼리 재활용.
