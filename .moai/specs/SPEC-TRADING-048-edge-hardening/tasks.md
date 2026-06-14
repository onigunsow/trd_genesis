# SPEC-TRADING-048 원자 작업 분해 (tasks.md)

development_mode: **tdd** · brownfield delta · 시장 중립 코어 제약 · 신규 마이그레이션 **033** (현재 최신 032)

각 작업 = 1 TDD 사이클(RED→GREEN→REFACTOR). 델타 마커 [NEW]/[MODIFY]. 모든 코어 함수는 외부 I/O 0(AC-CORE-2). 시장 상수 하드코딩 금지(AC-CORE-1) — KRX/KIS 종속은 인자 주입.

> 코드 검증 결과(run 선행 확정):
> - `RoundTrip.net_pnl = gross_pnl - fees` 이고 `fees`=수수료(매수+매도)만 포함. **거래세 0.18% 미차감**. → T-001이 주입형 `sell_tax_rate`로 net 보정해야 함(plan 위험 line 95 해소).
> - `edge/scorecard.py` 에 이미 `Scorecard`/`decide()`/`render()` 존재. **이름 충돌 회피**: M2 채점기는 새 모듈 `evaluate_backtest.py` + 새 클래스명 `BacktestScoreCard` 사용.
> - `_execute_signal()` L916-943 의 Kelly/heat/M1-8 가드는 `SIZING_MODE`와 무관하게 BUY qty에 항상 적용되어야 함(AC-M1-5) → 기존 `if SIZING_MODE=="deterministic"` 블록 *밖*, `if not ticker or qty<=0` *앞*에 배치.

---

## M2 — 검증 게이트 [NEW] (Priority: High) — 먼저 착수(M1-8 게이트의 PASS 상태 공급원)

### T-001 — [NEW] 거래단위 통계 순수 함수 (net-of-tax 보정 포함)
- 설명: roundtrip net_pnl 목록에서 win_rate/avg_win/avg_loss/profit_factor/expectancy/표본수 계산. **주입형 `sell_tax_rate`(예 0.0018)로 net 보정** — RoundTrip.net_pnl이 거래세 미차감이므로 청산 proceeds에 세금 차감 후 집계. gross 채점 금지.
- REQ: REQ-048-M2-1(net), REQ-048-CORE-1/2
- 의존: 없음
- 파일: **CREATE** `src/trading/edge/trade_stats.py`; **CREATE** `tests/edge/test_trade_stats.py`
- AC: AC-M2-1(net 입력 일부), AC-CORE-2 · Edge: 표본 0건→0점·division-by-zero 없음

### T-002 — [NEW] 5차원 채점기 순수 함수 + 컷오프
- 설명: `score_backtest(trade_stats, portfolio_metrics, is_oos, *, scoring_params) -> BacktestScoreCard(score, verdict, dimension_scores, warnings)`. 배점 expectancy/profit_factor/표본수/MDD-risk/robustness 각 0~20. 컷오프 PASS>=70 AND 모든차원≠0 AND expectancy>0 / REVISE 50~69 / REJECT. **엔진 import/호출 금지**(주입형). 클래스명 `BacktestScoreCard`(기존 Scorecard 충돌 회피).
- REQ: REQ-048-M2-1, REQ-048-CORE-1/2
- 의존: T-001 (TradeStats 형상)
- 파일: **CREATE** `src/trading/edge/evaluate_backtest.py`; **CREATE** `tests/edge/test_evaluate_backtest.py`
- AC: AC-M2-1(현재값→REJECT 파이어월), AC-M2-1b(정상값→PASS ~92점), AC-M2-6(주입형·엔진 0)

### T-003 — [NEW] 채점기 보조 점검: robustness·과적합 체크리스트·인플레 전처리
- 설명: (a) walk_forward 주입 OOS<IS*0.5 → robustness 실패+경고. (b) 과적합 사전 체크리스트(룰 10+/임계 소수점 과다/연 10회 미만)→warnings. (c) 인플레 함정 회피 전처리: equity_curve/daily_returns 선행 0-weight 제거 후 active기간 Sharpe·CAGR 재계산, 미청산 포지션 승률 경고. **채점기 측 전처리**(엔진 미변경).
- REQ: REQ-048-M2-2, REQ-048-M2-3, REQ-048-M2-4
- 의존: T-002 (동일 모듈 함수족)
- 파일: **MODIFY** `src/trading/edge/evaluate_backtest.py`; **MODIFY** `tests/edge/test_evaluate_backtest.py`
- AC: AC-M2-3, AC-M2-4, AC-M2-5

### T-004 — [NEW] 게이트 진입점 + M1-8 PASS 상태 공급
- 설명: 채점 verdict가 PASS 미만이면 사이징 A/B·실거래 확대 차단(차단 사유=미달 차원 반환). M1-8 게이트가 소비할 **PASS 상태 read API**(예: `is_validation_passed() -> bool`, 기본 False) 노출. 최소 상태(단일 플래그/상태행) — 검증 이력 테이블 신설 금지(과설계 회피).
- REQ: REQ-048-M2-5, REQ-048-M1-8(공급측)
- 의존: T-002
- 파일: **CREATE** `src/trading/edge/validation_gate.py`; **CREATE** `tests/edge/test_validation_gate.py`
- AC: AC-M2-2

---

## M1 — 사이징 가드 [MODIFY] (Priority: High)

### T-005 — [NEW] 시장 중립 Kelly/heat 코어 순수 함수
- 설명: `kelly_fraction(win_rate, payoff_ratio)` = W-(1-W)/R (W=0 또는 R<=0 → <=0 반환). `half_kelly_cap(kelly_pct, equity, price, *, lot_size, tick_size, round_fn)` half-Kelly만, 주입된 호가/통화 규칙 적용·최소주문 미만 0. `portfolio_heat(open_positions, *, heat_cap)` = Σ(진입가-손절가거리×수량)/자기자본, **손절 부재 시 명목가치 fallback**. heat 축소 함수: 상한 내 축소, 최소주문으로도 초과면 0. 시장 상수 하드코딩 금지.
- REQ: REQ-048-M1-1/2/4/6/7, REQ-048-CORE-1/2
- 의존: 없음
- 파일: **CREATE** `src/trading/strategy/sizing/kelly.py`; **CREATE** `tests/strategy/sizing/test_kelly.py`
- AC: AC-M1-2(cap), AC-M1-4(heat), AC-M1-6(호가/최소/반올림), AC-CORE-1/2 · Edge: W=0/R<=0·손절부재 fallback 예외 없음

### T-006 — [MODIFY] SizingParams 가드 파라미터 추가
- 설명: `SizingParams`에 `heat_cap`(기본 0.08), half-Kelly 파라미터 추가(env 재정의 가능). **SIZING_MODE(L214) 기본값 변경 없음**(불변).
- REQ: REQ-048-M1-4, REQ-048-NFR
- 의존: 없음 (T-005·T-007 공통 선행)
- 파일: **MODIFY** `src/trading/config.py`; **MODIFY** `tests/test_config.py`(또는 기존 config 테스트)
- AC: AC-M1-4(상한 출처), SIZING_MODE 불변 회귀

### T-007 — [MODIFY] _execute_signal 사이징 가드·M1-8 게이트 배선
- 설명: BUY qty에 (1) vol-targeting qty vs half-Kelly cap **min** 적용, (2) **negative-Kelly 파이어월**(kelly_pct<=0→qty0), (3) **heat 가드**(축소/0), (4) **M1-8 게이트**: T-004 `is_validation_passed()` False면 실측 kelly_pct를 0으로 강제. **SIZING_MODE 무관 항상 활성**(기존 deterministic 블록 밖, `if not ticker or qty<=0` 앞). confidence 비증폭 불변 회귀.
- REQ: REQ-048-M1-1/2/3/4/5/8
- 의존: T-005, T-006, **T-004(빌드 의존: PASS 상태 read API)** — 단 PASS=true 자체는 런타임 조건(현재 항상 REJECT)
- 파일: **MODIFY** `src/trading/personas/orchestrator.py`; **MODIFY** `tests/personas/test_orchestrator*.py`(사이징 seam)
- AC: AC-M1-1, AC-M1-2, AC-M1-3, AC-M1-4, AC-M1-5, AC-M1-7

---

## 마이그레이션 033 [NEW] (Priority: High) — M3 저장 작업의 빌드 선행

### T-008 — [NEW] 마이그레이션 033 (prob_* nullable + COOL_DOWN 상태)
- 설명: `persona_decisions`에 `prob_bull/prob_base/prob_bear` **nullable** 컬럼 추가. COOL_DOWN 상태 컬럼/테이블 추가(증거태그 누적·해제 마커). conftest fake_cursor/fake_conn/patch_db_connection 호환. 롤백 확인.
- REQ: REQ-048-M3-4(스키마), REQ-048-M3-5(스키마), REQ-048-NFR-3
- 의존: 없음 (T-010·T-011·T-012의 빌드 선행)
- 파일: **CREATE** `src/trading/db/migrations/033_edge_hardening.sql`; **CREATE/MODIFY** 마이그 적용 테스트
- AC: AC-NFR-2, AC-M3-3(스키마측), AC-M3-4(스키마측)

---

## M3 — 자기개선 루프 [NEW] (Priority: Medium)

### T-009 — [NEW] 결정 단위 postmortem 분류 코어 순수 함수
- 설명: `classify_decision_outcome(decision, roundtrip_or_none, relative_5d, relative_20d, regime, *, thresholds) -> Label`. 진입경로(roundtrip 존재): TP/FP/REGIME_MISMATCH(우선순위 REGIME>FP>TP). 미진입경로(roundtrip 없음, hold/REJECT/HOLD): rel_20d>0→MISSED. `attribute_to_persona()`, `propose_persona_weights(*, min_sample=20)` 제안만(자동적용 금지). 임계(0.6/0)는 주입. 시장 상수 하드코딩 금지.
- REQ: REQ-048-M3-1/2/3, REQ-048-CORE-1/2/3
- 의존: 없음(데이터는 호출자 조립: persona_decisions+risk_reviews+roundtrips LEFT JOIN)
- 파일: **CREATE** `src/trading/edge/postmortem.py`; **CREATE** `tests/edge/test_postmortem.py`
- AC: AC-M3-1(4분류·귀인), AC-M3-2(20표본), AC-CORE-1/2 · Edge: 미진입 roundtrip 부재 MISSED 정상

### T-010 — [NEW] confidence 시나리오 확률 저장 경로 (스키마-only)
- 설명: prob_bull/base/bear 저장 경로 + 합 검증 `|sum-1|<=1e-6`(세 값 존재 시). 세 값 NULL 허용 저장. **페르소나 프롬프트 변경 금지**(OQ-3, 후속 SPEC). Brier 등 calibration 계산은 NULL 행 제외.
- REQ: REQ-048-M3-4
- 의존: T-008
- 파일: **MODIFY** `src/trading/personas/`(저장 경로, 프롬프트 미변경) 또는 `src/trading/edge/`; **CREATE** 저장/검증 테스트
- AC: AC-M3-3 · Edge: 세 컬럼 NULL 정상 저장

### T-011 — [NEW] COOL_DOWN 리스크 상태 (수동 해제 전용)
- 설명: 규칙위반 누적 3회 또는 드로다운<=-5% → review-only(매수 사이징 0/매수 금지). halt_state/일일한도와 **독립 레이어**(circuit_breaker.py/limits.py 위 증거태그). **해제는 수동 /resume만** — SPEC-032 `auto_resume.classify_halt()`가 COOL_DOWN을 daily_loss류 비양성으로 취급해 자동재개 제외. 신규 상태머신 신설 최소화(기존 halt/limit 인프라 재사용).
- REQ: REQ-048-M3-5, REQ-048-CORE-3
- 의존: T-008, (매수 사이징 0 연동은 T-007 seam 재사용)
- 파일: **MODIFY** `src/trading/risk/limits.py` 또는 **CREATE** `src/trading/risk/cool_down.py`; **MODIFY** `src/trading/risk/auto_resume.py`(제외 분류); **CREATE/MODIFY** 테스트
- AC: AC-M3-4(3회/-5% 발동·2회 미발동·수동 해제 전용·독립 레이어)

### T-012 — [MODIFY] 대시보드 postmortem/calibration 읽기전용 쿼리
- 설명: `dashboard/queries.py`에 분류 분포·calibration 점수 읽기전용 쿼리 추가(기존 `ro_connection`/`dashboard_ro` 패턴 따름, 쓰기/제어 액션 없음).
- REQ: REQ-048-M3-6
- 의존: T-008(컬럼), T-009/T-010(데이터)
- 파일: **MODIFY** `src/trading/dashboard/queries.py`; **MODIFY** `tests/dashboard/test_queries.py`
- AC: AC-M3-5

---

## NFR — 회귀·시장 중립 증명 (Priority: High) — 최종 게이트

### T-013 — [NEW] 회귀 스위트 + 시장 중립 dual-param 증명
- 설명: (a) 기존 1420 테스트 0 회귀(pre-existing 6 제외). (b) 코어(kelly.py/evaluate_backtest.py/postmortem.py/trade_stats.py)를 한국 파라미터 세트와 가상 미국 파라미터 세트로 각각 호출해 출력이 입력·주입 파라미터에만 의존함을 검증 + 코어 본문 한국 상수 grep 0건. (c) SIZING_MODE OFF 불변·confidence 비증폭 불변 회귀.
- REQ: REQ-048-NFR-1/2, REQ-048-CORE-1
- 의존: T-001~T-012
- 파일: **CREATE** `tests/edge/test_core_market_neutral.py`; 전체 스위트 실행
- AC: AC-CORE-1, AC-NFR-1

---

## 의존 그래프 (요약)

```
T-001 → T-002 → T-003
              → T-004 ┐
T-006 ─┐               │ (빌드 의존)
T-005 ─┼──────────────→ T-007
        T-004 ─────────┘
T-008 → T-010
T-008 → T-011
T-008,T-009,T-010 → T-012
T-009 (독립)
(T-001..T-012) → T-013
```

권장 착수 순서: T-001 → T-002 → T-003 → T-004 → T-005 → T-006 → T-007 → T-008 → T-009 → T-010 → T-011 → T-012 → T-013.
(M2를 M1보다 먼저: T-007의 M1-8 게이트가 T-004 PASS 상태 API에 빌드 의존. mig 033(T-008)은 M3 저장(T-010/T-011/T-012)의 빌드 선행.)

## AC 커버리지 매트릭스
- CORE: AC-CORE-1=T-001/002/005/009/013 · AC-CORE-2=각 코어 테스트
- M1: AC-M1-1=T-007 · M1-2=T-005/007 · M1-3=T-007 · M1-4=T-005/006/007 · M1-5=T-007 · M1-6=T-005 · M1-7=T-007(←T-004)
- M2: AC-M2-1=T-002 · M2-1b=T-002 · M2-2=T-004 · M2-3=T-003 · M2-4=T-003 · M2-5=T-003 · M2-6=T-002
- M3: AC-M3-1=T-009 · M3-2=T-009 · M3-3=T-008+T-010 · M3-4=T-008+T-011 · M3-5=T-012
- NFR: AC-NFR-1=T-013 · AC-NFR-2=T-008
