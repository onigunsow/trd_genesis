# SPEC-TRADING-037 — Research (코드베이스 + 엣지 검증 진단)

> 매도(exit) 경로 복구 + 10년 KOSPI200 백테스트 기반 손절/익절 파라미터 도출 구현 전 사전 조사.
> 모든 라인 번호는 2026-05-30 기준 `fix/SPEC-TRADING-026-overheating-softening` 브랜치(HEAD 7144194).
> 엣지 검증 분석 결과(95%+ 신뢰): 26일 paper trading 동안 **BUY 21건 / SELL 0건 → 청산된 라운드트립
> 0건 → 수익성 측정 불가**. 근본 원인은 버그가 아니라 **설계 문제**.

---

## 1. 진단된 근본 원인 (DESIGN problem, not a bug)

### 1.1 출구(exit) 임계가 너무 넓다 — 한국 주식 변동성과 불일치

- `position_watchdog` 가 사용하는 `effective_stop = -STOP_ATR_MULTIPLIER × atr_pct`,
  `STOP_ATR_MULTIPLIER = 2.0`(env, 기본값). 파일: `src/trading/strategy/volatility/thresholds.py`
  라인 24~25(멀티플라이어 상수), 75~81(동적 레벨 계산 + 가드레일).
- 한국 주식의 high/extreme 변동성(ATR% **3.4~7.3**)에서 `2.0 × ATR%` 손절은 **−7%~−15%**,
  익절(`3.0 × ATR%`)은 **+10%~+22%** 가 된다. 가드레일 상한도 `MAX_STOP_LOSS_PCT=15.0`,
  `MAX_TAKE_PROFIT_PCT=30.0` 로 매우 넓다(라인 28~29).
- 보유 포지션이 실제로는 **+1.8% ~ −5.5%** 에 머물러 손절/익절 어느 쪽에도 도달하지 못한다.
  → SELL 시그널이 워치독에서 발생하지 않는다.
- 관련: `atr.py`(ATR 계산), `regime.py`(변동성 레짐 분류 — macro regime 과 별개).

### 1.2 Decision 페르소나가 HOLD 편향 — sell 시그널 자체가 희소

- 관측된 persona_decisions: **hold 348 / buy 129 / sell 2**. sell 시그널이 2건뿐이고,
  그 2건마저 아래 1.3 의 halt 게이트에서 차단됐다.
- `decision.jinja`(`src/trading/personas/prompts/decision.jinja`)는 손절 룰이 **이중 표기**다:
  - 라인 14: "보유 종목 평가손실 **−7% 도달 시** 매도 시그널 발생" (정적 −7% 플랫)
  - 라인 170/174: "각 종목에 대해 get_dynamic_thresholds 도구를 호출하여 **`effective_stop`** 값을
    사용하세요 / source="fixed_fallback" 반환 시 −7% 손절 사용" (동적 임계)
  - → 정적 −7% 와 동적 `effective_stop` 가 충돌. 페르소나 sell 룰과 워치독 exit 가 불일치.
  - 비목표 주의: 본 SPEC 은 entry/LLM 판단 로직을 바꾸지 않는다. sell-rule 프롬프트 정렬만 한다.

### 1.3 halt 게이트가 위험 축소 매도까지 차단

- 일일 주문수 회로차단(BUY 가 10건/일 한도에 도달)이 트립하면, orchestrator 가 **사이클 전체를 스킵**한다.
  파일: `src/trading/personas/orchestrator.py`
  - pre_market 게이트: 라인 ~892~903 (`if state["halt_state"]: ... return res`)
  - intraday 게이트: 라인 ~1369~1380 (동일 패턴, "same gate pattern as pre_market")
  - 두 게이트 모두 SPEC-031 쿨다운 알림 + `return res` 로 **risk/execute 진입 전에 사이클 종료** →
    sell 시그널도 함께 억제된다.
- 대조: `position_watchdog`(SPEC-033)는 **direct `kis_sell` bypass** 로 halt/일일주문수 게이트를
  우회해 직접 매도한다(위험 축소 exit 는 buy gate 미통과). 즉 워치독은 이미 우회하지만,
  **페르소나 sell 경로는 우회하지 않는다** — 본 SPEC 이 이 비대칭을 정리한다.

### 1.4 잠복 버그 — ATR 불가 시 effective_stop=None → 영구 skip

- `thresholds.py` 라인 58~67: `compute_atr` 가 `None` 을 반환하면 fallback 으로
  `DynamicThresholds(ticker=ticker, source="fixed_fallback")` 만 반환한다.
- `src/trading/strategy/volatility/models.py` 라인 11~29 의 `DynamicThresholds`:
  - 라인 24~25: `fixed_fallback_stop: float = -7.0`, `fixed_fallback_take: str = "RSI>85"` 가
    **이미 정의돼 있으나**,
  - 라인 26~27: `effective_stop: float | None = None`, `effective_take: float | None = None` 로
    **None 인 채로 남는다**. fallback 경로가 `fixed_fallback_*` 를 `effective_*` 로 **연결하지 않는다**.
- `position_watchdog.classify_holding`(`src/trading/watchers/position_watchdog.py` 라인 ~116~118):
  `if eff_stop is None or eff_take is None: return ("skip", 0)`.
  → ATR/ohlcv 불가 종목은 `effective_stop=None` → **영구히 skip** → 그 포지션은 **절대 자동 매도되지
  않는다**(자본 보전에 치명적). 이미 코드 주석에도 "None thresholds ... classify as skip" 으로 명시돼
  있어 의도적 방어처럼 보이지만, 실제로는 fallback 의 −7.0/RSI>85 가 버려지는 게 문제다.

---

## 2. 백테스트 인프라 (Phase A 재사용 대상)

### 2.1 ✓ 백테스트 엔진 — 이미 존재(벡터화)

- `src/trading/backtest/engine.py`: `run(prices, weights, *, initial_capital, fee_rate, tax_rate,
  slippage) -> BacktestResult`. **weights 기반 벡터화** 엔진으로 CAGR/MDD/Sharpe/trades/final_equity/
  equity_curve/daily_returns 를 계산한다.
- 한국 시장 비용 내장: `DEFAULT_FEE_RATE=0.00015`(수수료), `DEFAULT_TAX_RATE=0.0018`(거래세, 매도 시),
  `DEFAULT_SLIPPAGE=0.0005`(슬리피지). look-ahead 회피를 위해 **전일 weights × 당일 수익률** 사용.
- 한계: 현재 엔진은 **연속 weights** 모델이라 "진입 후 손절/익절 룰에 따른 이산 청산" 을 직접 표현하지
  않는다. → Phase A 는 엔진 위에 **exit-rule 시뮬레이터**(per-position 진입→ATR 멀티/플로어/익절 룰 적용
  →청산) 를 얹어야 한다(엔진의 metrics 계산 유틸은 재사용).

### 2.2 ✓ 결과 영속화 — benchmark_runs

- `benchmark_runs` 테이블: `003_market_data.sql` 라인 49(`CREATE TABLE`), index 라인 65.
- `src/trading/scripts/backtest_run.py`: 백테스트 실행 → `benchmark_runs` INSERT(라인 51), run_id 출력.
- → 파라미터 스윕 결과(파라미터셋별 승률/기대값/MDD/평균보유)도 `benchmark_runs`(strategy 라벨 구분)
  또는 신규 테이블에 영속화 가능.

### 2.3 ✓ OHLCV 데이터 파이프라인 — pykrx + cache

- `src/trading/data/pykrx_adapter.py`:
  - `fetch_ohlcv(symbol, start, end)` (라인 22) — `stock.get_market_ohlcv_by_date` → `upsert_ohlcv`.
  - `fetch_incremental(symbol, default_start)` (라인 45), `fetch_flows` (라인 92).
- `src/trading/data/cache.py`: `upsert_ohlcv`(라인 18, `ohlcv` 테이블 INSERT), `cached_ohlcv`(라인 62,
  SELECT), MIN/MAX ts 조회(라인 82). → **10년치 적재는 기존 `fetch_ohlcv` + `upsert_ohlcv` 재사용**.
- KOSPI 지수: `stock.get_index_ohlcv` 코드 `1001`(KOSPI). KOSPI200 구성종목:
  `src/trading/data/universe.py` 라인 80 `stock.get_index_portfolio_deposit_file('1028')`
  (`KOSPI200_INDEX_CODE='1028'`, 라인 40). top-N 헬퍼 `_read_kospi200_top50`(라인 83, 현재 50종 제한).
  → Phase A 는 top-50 제한을 푼 **전체 KOSPI200 구성종목** 대상 적재가 필요.

### 2.4 ✓ 엣지 측정 도구 — edge/* + edge-report CLI

- `src/trading/edge/`: `roundtrips.py`(라운드트립 추출), `report.py`, `scorecard.py`, `analytics.py`,
  `confidence.py`, `benchmark.py`, `snapshot.py`.
- CLI: `trading edge-report [--days N] [--telegram] [--include-unrealized]`
  (`src/trading/cli.py` 라인 185~186 dispatch, `_cmd_edge_report` 라인 295). 도움말 라인 401:
  "paper 성적 → go/no-go 판정".
- 마이그레이션 `026_edge_validation.sql`: `daily_equity_snapshot` 테이블(라인 13).
- → 배포 후 첫 청산 라운드트립이 생기면 `edge-report` 로 **실현 성과 측정 재개**(Phase C).

---

## 3. 마이그레이션 번호

- 적용된 최신: `026_edge_validation.sql`(SPEC 엣지 검증). 024=regime(SPEC-035), 025=late_cycle(SPEC-036).
- → 본 SPEC 신규 마이그레이션(파라미터 스윕 결과 영속화용, 필요 시)은 **`027_*.sql`**.

---

## 4. 핵심 제약 / 하우스 스타일 (계승)

- **테스트**: `.venv/bin/python -m pytest`(docker 이미지에 pytest 없음). 베이스라인 **950 passed**
  (SPEC-036 배포 시점). 신규 회귀 0, 신규 코드 85%+(TRUST 5). **money/risk 로직 → reproduction-first
  필수**(CLAUDE.md HARD Rule 4).
- **lint**: ruff 가 BLE001 select 안 함 → `# noqa: BLE001` 금지. 평범한 `except Exception:` 사용
  (graceful fetcher 포함).
- **마이그레이션**: raw SQL, 순차(`027_`), 멱등(information_schema/IF NOT EXISTS 가드), `migrate.py`
  자동 발견. 재배포 후 `docker exec trading-app trading migrate` **수동 실행**(자동 boot 미적용).
- **CLI 경로 강제**: 페르소나 호출은 `is_cli_mode_active() → call_persona_via_cli`. bare `call_persona`
  금지(cli_only_mode 에서 유료/크래시). 단, 본 SPEC 은 페르소나 판단 로직을 바꾸지 않음.
- **브랜치**: 작업 브랜치는 이미 `fix/SPEC-TRADING-026-overheating-softening`(HEAD 7144194).
  신규 브랜치 생성 금지, 커밋은 오케스트레이터가 배포 처리.
- **거래 모드**: paper only. live 잠금 유지. 공격적 파라미터/실매도 활성화는 본 SPEC 범위 밖.

---

## 5. 결정적 한계 (CRITICAL — SPEC scope 에 반드시 명시)

- **백테스트는 결정적(deterministic) 출구 룰만 검증한다**. ATR 멀티플라이어·하드 스톱 플로어·익절 설정의
  견고성은 10년 데이터로 검증 가능하다(룰이 결정적이므로).
- **백테스트는 LLM 진입(entry) 엣지를 검증하지 못하며, 할 수도 없다** — look-ahead bias. 과거 데이터로
  "그때 LLM 이 무엇을 샀을지" 를 재현하면 미래 정보가 새어든다. 진입 엣지는 **forward paper 검증**으로만
  확인된다.
- → SPEC 은 백테스트가 **전체 수익성을 증명한다고 함의해서는 안 된다**. 백테스트의 산출물은 "출구 룰의
  견고한 파라미터셋" 이지 "전략이 돈을 번다는 증거" 가 아니다.
