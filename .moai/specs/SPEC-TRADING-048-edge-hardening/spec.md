---
id: SPEC-TRADING-048
version: 0.4.0
status: implemented
created: 2026-06-14
updated: 2026-06-14
author: oni
priority: high
issue_number: null
labels: ["trading", "sizing", "validation", "self-improvement"]
---

# SPEC-TRADING-048: 엣지 경화 — 검증 게이트·사이징 가드·자기개선 루프 (외부 퀀트 저장소 차용 종합)

## HISTORY

- 0.4.0 (2026-06-14): TDD 구현 완료(13태스크, 신규 155 테스트 GREEN, 전체 1566 passed / 회귀 0, pre-existing 6 제외). [운영자 결정] M1 Kelly/heat 가드를 **live 전용**으로 한정 — paper 는 기존 동작을 유지해 OOS 데이터 수집을 지속한다(orchestrator `_is_live_mode()` 게이트). 따라서 REQ-048-M1-6 "SIZING_MODE 무관 항상 활성"은 "live 모드에서 항상 활성"으로 좁혀짐. 구현 중 수정: edge/scorecard 이름충돌 회피(BacktestScoreCard), net-of-tax 보정(trade_stats sell_tax_rate 주입), kelly heat 부동소수점 epsilon, postmortem catch-all confidence-gated. M2 검증게이트는 paper/live 공통(백테스트 대상), M3 postmortem/COOL_DOWN/대시보드 뷰는 모드 무관 동작.
- 0.3.0 (2026-06-14): plan-auditor 2차 감사(FAIL 0.78, must-pass 전부 PASS) 반영 iteration 3. N1(M3-1 MISSED 분류 모순) 해결 — 분류 단위를 "라운드트립"→"결정(decision)"으로 재정의, classify_decision_outcome()로 재명명, 진입경로(TP/FP/REGIME_MISMATCH)+미진입경로(MISSED) 이원화. N2(M2-1 컷오프·배점 미정의) 해결 — 5차원 각 20점·총 100점 배점 매핑·PASS>=70(파이어월 must-pass)/REVISE 50~69/REJECT<50 컷오프 명문화. 거래세(매도 0.18%) net 손익 명시. D8/D9 EARS 라벨-서술 정합.
- 0.2.0 (2026-06-14): plan-auditor 1차 감사(FAIL 0.52) 반영 개정. 운영자가 OQ 5건 전부 확정 → 미해결 질문을 "확정된 결정(Resolved Decisions)"으로 전환. 감사 결함 D1~D13 수정: frontmatter labels 추가, M2 채점기를 주입형 순수함수(roundtrips에서 거래통계 계산)로 재설계, COOL_DOWN·4분류·heat 구체 수치화, M2 PASS 전 kelly 강제 0 게이트 명문화, confidence 시나리오 확률 스키마-only(프롬프트 변경 Exclusions), EARS 라벨 정합.
- 0.1.0 (2026-06-14): 최초 초안. SPEC-044(측정 인프라)가 산출한 첫 비용보정 기대값(거래당 -14,840원, KOSPI 대비 알파 -11.03%p, confidence-수익 Spearman -0.455)에 대한 규율/검증 레이어 대응. 외부 퀀트 저장소(tradermonty/claude-trading-skills, quant-sentiment-ai)에서 검증·사이징·자기개선 *방법론*만 결정적 파이썬으로 이식. 새 매매 신호 추가 없음. brownfield delta.

---

## 배경 (WHY)

SPEC-044가 측정 인프라를 배포한 뒤 처음으로 비용보정 기대값을 계산했고, 그 결과가 명확한 마이너스 엣지를 드러냈다:

- 거래당 순기대값: **-14,840원**
- KOSPI 대비 누적 알파: **-11.03%p**
- confidence ↔ 실현 P&L Spearman 상관: **-0.455** (LLM이 자신있다고 평가한 거래일수록 더 손실 — 반예측적)

표본은 페이퍼 8건으로 통계적으로 빈약하지만 방향은 외부 연구(FINSABER, Wiecki R²<0.025)와 일치한다. 실거래 확대는 OOS(out-of-sample) 양수 엣지 확인 시점까지 보류 중이다.

이 SPEC은 **새 매매 신호를 추가하지 않는다.** 외부 퀀트 저장소에서 검증·사이징·자기개선 *방법론과 공식*만 결정적(deterministic) 파이썬으로 이식하여, 규율(discipline)/검증(validation) 레이어를 먼저 짓는 것이 목표다. 즉 "더 많이 베팅하는 법"이 아니라 "엣지가 음수일 때 베팅하지 않는 법"과 "엣지가 양수임을 증명하는 법", "어느 페르소나가 틀렸는지 사후 귀인하는 법"을 코드로 굳힌다.

### 차용 출처 (방법론만)

- **tradermonty / claude-trading-skills**: backtest-expert 5차원 채점기, signal-postmortem 분류·귀인, trade-performance-coach COOL_DOWN 평결 — 방법론·공식만.
- **quant-sentiment-ai**: 정성 confidence를 시나리오 확률로 분해하는 스캐폴드 — 스키마 아이디어만.
- KIS 공식 백테스터(문서): 지표 인플레 함정(웜업 idle Sharpe 부풀림, CAGR 분모 오류, 미청산 포지션 승률 경고) — 함정 회피 규칙만.

[HARD] 어떤 외부 저장소도 통째로 fork/import 하지 않는다. 공식과 판정 기준만 자체 코드로 재구현한다.

---

## 핵심 아키텍처 제약 (HARD)

운영자는 한국 증시에서 엣지를 검증한 뒤 미국 증시로 확장할 계획이다. 따라서:

[HARD] M1~M3의 핵심 로직(Kelly 수학, 백테스트 채점기, postmortem 분류·귀인)은 **시장 중립(market-neutral) 순수 함수**로 작성한다.

- 순수 함수: 외부 I/O·전역 상태·시각(now)·DB 접근 없이, 입력 인자만으로 출력이 결정되는 함수.
- KRX/KIS 종속 항목(호가단위·최소주문수량·매수/매도 수수료·거래세·KOSPI 벤치마크 수익률·통화 반올림)은 모두 **주입 가능한 파라미터 또는 어댑터**로 분리한다.
- 미국 단계에서는 동일 코어 함수에 미국 시장 어댑터(센트 호가·SEC 수수료·SPY 벤치마크 등)만 갈아끼우면 재사용 가능해야 한다.

이 제약은 REQ-048-CORE로 명문화하며, 인수 기준에 시장 중립 코어 재사용성 검증을 포함한다.

---

## 기존 시스템 컨텍스트 (BROWNFIELD)

### [EXISTING] 그대로 재사용하는 자산

| 영역 | 위치 | 역할 |
|------|------|------|
| 벡터화 백테스트 엔진 | `src/trading/backtest/engine.py` run() | numpy/pandas 백테스트 (한국 수수료 0.015%/세금 0.18%/슬리피지 0.05%) |
| Walk-forward | `src/trading/backtest/walk_forward.py` | IS/OOS 분할 실행 |
| Exit sweep | `src/trading/backtest/exit_sweep.py` | 출구 파라미터 스윕 |
| Roundtrip 빌더 | `src/trading/edge/roundtrips.py` build_roundtrips() FIFO (L127-200), RoundTrip (L26-77) | FIFO 라운드트립 생성 |
| Confidence 분석 | `src/trading/edge/confidence.py` analyze() (L106), Spearman (L100-103) | confidence-P&L 상관 |
| 실현손익 집계 | `src/trading/edge/realized_pnl.py` aggregate_realized_pnl_cum() | 누적 실현손익 |
| 벤치마크 알파 | `src/trading/edge/benchmark.py` alpha_pct | KOSPI 대비 알파 |
| Vol-targeting 사이징 | `src/trading/strategy/sizing/vol_target.py` compute_qty() (L35-156) | 변동성 타겟 수량 산출 |
| 사이징 설정 | `src/trading/config.py` SIZING_MODE (L214), SizingParams (L183-209) | sizing_mode 기본 OFF(llm_direct) |
| 오케스트레이터 seam | `src/trading/personas/orchestrator.py` _execute_signal() (L898-1025), 사이징 호출 (L916-943) | 신호 실행 진입점 |
| 회로차단기 | `src/trading/risk/circuit_breaker.py` is_halted()/trip()/reset() | halt 상태 관리 |
| 사전주문 한도 | `src/trading/risk/limits.py` check_pre_order() (L124-200), daily_pnl_pct() (L70) | 5대 한도 |
| 포지션 워치독 | `src/trading/watchers/position_watchdog.py` | */5 폴, ATR 손절/익절 |
| 대시보드 쿼리 | `src/trading/dashboard/queries.py`, fetch_scorecard() (L161) | 읽기전용 대시보드 (dashboard_ro role, mig 032) |
| 페르소나/결정 | `src/trading/personas/{macro,micro,decision,risk}.py`, base.py call_persona() (L222+) | DB: persona_decisions(confidence NUMERIC(4,2)), risk_reviews(verdict) |

[EXISTING] 마이그레이션 최신 = 032. 다음 신규 = **033**.

### 현재 격차 (이 SPEC이 메우는 것)

- per-persona weighting 메커니즘이 코드에 **전혀 없음**.
- 백테스트 결과를 PASS/REVISE/REJECT로 자동 판정하는 채점 게이트가 **없음** — 마이너스 엣지/소표본이 게이트 없이 통과됨.
- 종료 거래를 사후 분류(TRUE_POSITIVE/FALSE_POSITIVE/...)하고 페르소나에 귀인하는 루프가 **없음**.
- confidence를 시나리오 확률로 분해하는 스키마가 **없음** (calibration 원재료 부재).
- 반복 규칙 위반/드로다운 시 review-only로 강제하는 COOL_DOWN 리스크 상태가 **없음**.

---

## 요구사항 (EARS)

요구사항 모듈 5개: CORE(아키텍처 제약) + M1(사이징 가드) + M2(검증 게이트) + M3(자기개선 루프) + NFR(비기능).

### REQ-048-CORE: 시장 중립 코어 (Ubiquitous)

- **REQ-048-CORE-1** (Ubiquitous): Kelly 수학, 백테스트 채점, postmortem 분류·귀인 로직은 외부 I/O·전역 상태·시각·DB 접근이 없는 시장 중립 순수 함수로 구현되어야 한다(shall).
- **REQ-048-CORE-2** (Ubiquitous): KRX/KIS 종속 항목(호가단위·최소주문·수수료·거래세·KOSPI 벤치마크·통화 반올림)은 호출자가 주입하는 파라미터 또는 어댑터로 코어 함수에 전달되어야 한다(shall). 코어 함수 본문에 한국 시장 상수를 하드코딩해서는 안 된다(shall not).
- **REQ-048-CORE-3** (Optional): 미국 시장 어댑터가 제공되는 경우, 동일 코어 함수가 코드 수정 없이 미국 시장 파라미터로 동작할 수 있어야 한다(shall).

### REQ-048-M1: 사이징 가드 [MODIFY] (SPEC-046 확장)

- **REQ-048-M1-1** (Event-Driven): 측정된 승률 W와 손익비 R이 코어 함수에 입력되면(WHEN), 시스템은 kelly_pct = W - (1-W)/R 로 Kelly 비율을 계산하고 half-Kelly(0.5 * kelly_pct)만 사용해야 한다(shall). W·R의 출처는 edge/roundtrips.py 실측 라운드트립의 net_pnl 목록에서 산출한 승률·손익비다 (OQ-2 확정).
- **REQ-048-M1-2** (Unwanted): 만약 kelly_pct <= 0 이면(IF), 시스템은 해당 거래 수량을 0으로 설정하여 거래를 금지해야 한다(then shall). (negative-Kelly 바닥 규칙). W=0 또는 R<=0(분모 0/음수)인 경우도 동일하게 kelly_pct <= 0으로 간주하여 거래를 금지한다.
- **REQ-048-M1-3** (Event-Driven): 신호 실행 시(WHEN), 시스템은 기존 vol-targeting compute_qty() 산출 수량과 half-Kelly 상한 수량 중 작은 값을 채택해야 한다(shall). (하드캡)
- **REQ-048-M1-4** (State-Driven): 포트폴리오 총 heat가 설정 상한(기본 0.08, 주입 파라미터)을 초과하려 하면(WHILE), 시스템은 신규 진입 수량을 조정해야 한다(shall). 여기서 heat 정의는 (OQ-4 확정) 각 미결제 포지션의 "진입가-손절가 거리 × 수량"(위험금액)을 자기자본으로 나눈 값의 합산이며, 손절가가 없는 포지션은 명목가치(가격 × 수량 / 자기자본)를 fallback으로 사용한다. 분기 규칙: (a) 신규 수량을 상한 내로 들어오도록 축소하고, (b) 축소 후에도 주입된 최소주문수량으로조차 상한을 초과하면 수량을 0으로 만든다.
- **REQ-048-M1-5** (Unwanted): 수량 확정 시(when finalizing quantity), 시스템은 confidence 값을 이용해 거래 수량을 증가시켜서는 안 된다(shall not). (D8: Unwanted 라벨 유지, 서술을 금지문으로 정합. SPEC-046 REQ-046-B1 불변 유지)
- **REQ-048-M1-6** (Ubiquitous): Kelly 바닥 규칙과 heat 가드는 SIZING_MODE 플래그 값과 무관하게 항상 활성(거래 허용 여부 게이트로) 동작해야 한다(shall). vol-targeting 산출 자체는 SIZING_MODE 기본 OFF(llm_direct)를 유지한다.
- **REQ-048-M1-7** (Event-Driven): 수량 확정 시(WHEN), 시스템은 주입된 KRX 호가단위·최소주문수량·통화 반올림 규칙을 적용해야 한다(shall). 기존 규칙이 존재하면 재사용한다.
- **REQ-048-M1-8** (State-Driven): M2 검증 게이트가 해당 전략에 대해 PASS 판정을 산출하지 않은 동안(WHILE), 시스템은 런타임에서 kelly_pct를 강제로 0으로 설정하여 실질적으로 거래를 금지해야 한다(shall). (OQ-2 확정: 코어 Kelly 함수는 완전 구현하되, 양의 엣지가 채점기로 입증되기 전까지는 활성화 게이트가 0으로 묶는다.) M2 PASS 이후에만 REQ-048-M1-1의 실측 kelly_pct가 유효해진다.

### REQ-048-M2: 검증 게이트 [NEW]

- **REQ-048-M2-1** (Event-Driven): 채점기에 입력이 주어지면(WHEN), 시스템은 5차원을 각 20점 만점·총 100점으로 채점하여 합산 점수와 PASS/REVISE/REJECT 판정을 산출해야 한다(shall). (N2 확정)
  - **[설계 확정 — OQ-5]** 채점기는 **주입형 순수 함수**다. 기존 backtest/engine.py를 재작성하지 않는다. backtest/engine.py의 BacktestResult는 cagr/mdd/sharpe/trades/final_equity/equity_curve/daily_returns만 제공하고 거래단위 통계는 제공하지 않으므로(코드 검증 완료), 입력은 다음 3출처에서 주입받는다:
    - 거래단위 통계(expectancy, profit_factor, win_rate, avg_win, avg_loss, 표본수): `edge/roundtrips.py`의 RoundTrip.net_pnl 목록에서 계산.
    - 포트폴리오 지표(MDD, Sharpe, CAGR, equity_curve): `backtest/engine.py`의 BacktestResult에서.
    - IS/OOS 분할 성과: `backtest/walk_forward.py`에서.
  - **[거래세 명시]** expectancy·profit_factor·avg_win·avg_loss는 매수/매도 수수료 및 거래세(매도측 0.18%)를 **모두 차감한 net 손익**(RoundTrip.net_pnl)을 사용한다. 그로스(gross) 손익으로 채점해서는 안 된다.
  - **차원별 배점 매핑 (각 0~20점):**
    - **expectancy (20)**: expectancy <= 0 → 0점; expectancy > 0 → 상한값(EXP_FULL, 주입 파라미터, 기본=자기자본 0.5% 상당) 도달 시 20점까지 선형 비례. min(20, 20 * expectancy / EXP_FULL).
    - **profit_factor (20)**: PF < 1.0 → 0점; 1.0 <= PF < 1.5 → 부분점(선형, 1.0=0점→1.5=20점, 즉 40*(PF-1.0)); PF >= 1.5 → 20점.
    - **표본수 (20)**: 거래 30 미만 → 0점; 100건 → 15점; 200건 이상 → 20점. 구간 선형 보간(30~100: 0→15, 100~200: 15→20).
    - **MDD-risk (20)**: MDD >= 50% → 0점(파이어월); 그 외 |MDD|에 반비례, 20 * (1 - |MDD| / 0.5). MDD 0% → 20점, 25% → 10점.
    - **robustness (20)**: 테스트 기간 5년 미만 → 0점; OOS < IS*0.5 → 0점(실패, REQ-048-M2-2); 파라미터 7개 초과 → 초과 1개당 -3점 페널티(하한 0). 기본 만점에서 차감.
  - **판정 컷오프 (N2 확정):**
    - **PASS** = 합계 >= 70 **그리고** 어떤 차원도 0점이 아님(must-pass 파이어월) **그리고** expectancy > 0.
    - **REVISE** = 합계 50~69 (그리고 PASS 조건 미충족).
    - **REJECT** = 합계 < 50 **또는** 임의 차원이 0점 **또는** expectancy <= 0.
- **REQ-048-M2-2** (State-Driven): walk-forward 결과를 평가할 때(WHILE), OOS 성과가 IS 성과의 50% 미만이면 경고를 기록하고 robustness 차원을 실패 처리해야 한다(shall). IS/OOS 성과 값은 walk_forward.py에서 주입받는다.
- **REQ-048-M2-3** (Event-Driven): 채점 전 사전 점검 시(WHEN), 시스템은 과적합 체크리스트(룰 조건 10+개 = 경고, 임계값에 소수점 자릿수 과다 = 커브핏 탐지, 연간 기회 10회 미만 = 통계적 무의미 경고)를 적용하고 경고 목록을 ScoreCard에 부착해야 한다(shall).
- **REQ-048-M2-4** (Ubiquitous): 시스템의 모든 지표 계산은 항상 KIS 백테스터가 문서화한 인플레 함정을 회피해야 한다(shall) (D9: 상시 불변 조건으로 서술하여 Ubiquitous 라벨 정합): (a) Sharpe/수익률은 웜업 idle 일수를 제외한 active 기간만으로 계산, (b) CAGR 분모는 active 기간 사용, (c) 미청산 포지션을 포함한 승률은 경고 플래그 부착. **active 기간 트리밍은 채점기 측에서 처리한다**: equity_curve/daily_returns에서 선행 0-weight(거래·포지션 변동 없는) 일자를 제거한 뒤 Sharpe/CAGR을 재계산한다(엔진 출력은 그대로 두고 채점기 입력 전처리로 수행).
- **REQ-048-M2-5** (Unwanted): 채점 점수가 PASS 기준에 미달하면(IF), 시스템은 해당 전략/파라미터에 대한 사이징 A/B 활성화 및 실거래 확대를 허용해서는 안 된다(then shall not). (자동 게이트). 현재의 마이너스 기대값·소표본 입력은 의도적으로 REJECT 되어야 한다. 이 게이트는 REQ-048-M1-8(M2 PASS 전 kelly 강제 0)과 연동된다.

### REQ-048-M3: 자기개선 루프 [NEW]

- **REQ-048-M3-1** (Event-Driven): 결정 결과 평가 사이클(예: 일일 postmortem 배치)이 실행되면(WHEN), 시스템은 **결정(decision) 단위**로 분류해야 한다(shall) (N1 확정 — 분류 단위를 "라운드트립"에서 "결정"으로 재정의. 함수명도 classify_decision_outcome()로 명명). 분류는 두 경로로 나뉜다:
  - **(경로 1) 진입·종료된 결정** (해당 결정에 대응하는 roundtrip이 존재): roundtrip 실현손익(realized_return, net 기준)과 5일/20일 KOSPI 상대수익(relative_5d, relative_20d)으로 분류.
    - **TRUE_POSITIVE**: realized_return > 0 AND (relative_5d > 0 OR relative_20d > 0). (수익 실현 + 시장 대비 우위)
    - **FALSE_POSITIVE**: 매수 결정의 진입 confidence가 임계(기본 0.6) 이상이었으나 relative_20d < 0. (자신했으나 시장 대비 열위)
    - **REGIME_MISMATCH**: 신호 방향이 결정 시점 regime(system_state.macro_regime)과 불일치. (예: bearish regime에서 신규 매수)
  - **(경로 2) 미진입 결정** (hold, 또는 risk_reviews verdict이 REJECT/HOLD여서 roundtrip이 생성되지 않은 결정): 결정 시점 이후 5/20일 KOSPI 상대수익으로 사후 평가하여, 진입했다면 이익이었을 경우 **MISSED**로 분류. (relative_20d > 0 이면 MISSED)
  - **데이터 출처**: persona_decisions(전체 결정) + risk_reviews(verdict) + roundtrips(진입·종료분만 LEFT JOIN). 기존 기록의 사후 분석이므로 **새 매매 신호를 생성하지 않는다**("새 신호 금지" 제약 준수).
  - **우선순위**: 진입 경로에서 경계가 동시 충족되면 REGIME_MISMATCH > FALSE_POSITIVE > TRUE_POSITIVE. MISSED는 미진입 경로 전용이므로 진입 경로 라벨과 상호배타적이다. 임계(confidence 0.6, relative 0)는 주입 파라미터로 운영 중 보정 가능.
- **REQ-048-M3-2** (Event-Driven): 결정이 분류되면(WHEN), 시스템은 그 결정을 발신 페르소나(macro/micro/portfolio/decision)에 귀인하여 페르소나별 적중/오발 통계를 집계해야 한다(shall).
- **REQ-048-M3-3** (State-Driven): 어느 페르소나의 표본이 20건 이상일 때만(WHILE), 시스템은 해당 페르소나의 confidence-수익 역상관을 국소화하고 weight 조정 *제안*을 산출해야 한다(shall). 제안은 자동 적용해서는 안 된다(shall not). (제안까지만)
- **REQ-048-M3-4** (Ubiquitous): 시스템은 각 결정의 confidence를 강세/기준/약세 시나리오 확률(prob_bull/prob_base/prob_bear)로 분해하여 저장하는 **DB 스키마(nullable 컬럼)와 저장 경로만** 제공해야 한다(shall). (D4/OQ-3 확정) 세 확률이 모두 존재할 때 |prob_bull + prob_base + prob_bear - 1| <= 1e-6 (D13 확정)을 만족해야 하며, 이를 Brier/calibration 점수 산출 원재료로 사용한다. **이 확률을 생성하는 페르소나 프롬프트 변경(강세/기준/약세 확률 출력)은 이 SPEC의 범위가 아니며 후속 SPEC으로 분리한다** — 컬럼은 nullable로 두어 프롬프트가 채우기 전까지 NULL을 허용한다.
- **REQ-048-M3-5** (Unwanted): 증거태그 기반 규칙 위반이 누적 3회 도달하거나 자기자본 드로다운이 임계(기본 -5%, 주입 파라미터) 이하로 떨어지면(IF), 시스템은 COOL_DOWN 리스크 상태로 전환하여 review-only(신규 매수 사이징 0 / 신규 매수 금지)를 강제해야 한다(then shall). (D5 확정) COOL_DOWN은 기존 halt_state/일일한도 위에 증거태그 기반 레이어로 hook되며, **해제는 운영자 수동 /resume으로만 가능하다** (OQ-1 확정: SPEC-032 장전 자동 재개 대상에서 제외, daily_loss류와 동일 취급). 구체 임계(3회, -5%)는 본 SPEC에 고정하되 운영 중 config로 보정 가능.
- **REQ-048-M3-6** (Optional): SPEC-047 대시보드가 활성인 경우, 시스템은 postmortem 분류 결과와 calibration 점수를 읽기 전용 뷰로 노출할 수 있어야 한다(shall).

### REQ-048-NFR: 비기능 요구사항

- **REQ-048-NFR-1** (Ubiquitous): 이 SPEC 구현은 기존 1420개 테스트의 회귀를 0으로 유지해야 한다(shall). (pre-existing 6건 제외)
- **REQ-048-NFR-2** (Ubiquitous): 모든 신규 모듈은 TDD로 개발하며, 코어 순수 함수는 외부 의존 없이 단위 테스트 가능해야 한다(shall).
- **REQ-048-NFR-3** (Ubiquitous): DB 스키마 변경은 본 SPEC 신규 마이그레이션 **033**으로 추가하며(현재 최신 032), conftest.py의 fake_cursor/fake_conn/patch_db_connection 픽스처와 호환되어야 한다(shall).

---

## Exclusions (What NOT to Build) — 범위 제외

이 섹션은 [HARD] 필수이며 범위 폭주를 막는다.

1. **외부 저장소 통째 차용 금지**: tradermonty/claude-trading-skills, quant-sentiment-ai 등 어떤 저장소도 fork/import 하지 않는다. 공식·판정 기준·스키마 아이디어만 자체 재구현.
2. **미국 데이터/브로커 종속 제외**: SEC/Form4/옵션 플로우/Alpaca/13F/미국 배당세 관련 코드는 전부 이 SPEC 범위 밖. (CORE는 재사용 가능하도록 설계만 하되, 미국 어댑터 구현은 후속 SPEC)
3. **dartlab 펀더멘털/Flow 사전필터, KIS 공식 Lean 백테스터 본격 통합 제외**: 후속 SPEC으로 분리.
4. **새 매매 신호/전략 추가 금지**: 이 SPEC은 규율·검증 레이어만. 알파 생성 로직 변경 없음.
5. **페르소나 weight 자동 적용 금지**: 이번엔 weight 조정 *제안* 산출까지만. 자동 반영은 후속 SPEC + 충분한 표본 확보 후.
6. **sizing_mode 기본값 변경 금지**: SIZING_MODE 기본 OFF(llm_direct) 유지. (Kelly 바닥/heat 가드는 mode와 무관하게 게이트로 작동하지만, vol-targeting 산출 자체의 기본 활성화는 변경하지 않는다.)
7. **confidence 시나리오 확률 생성 프롬프트 변경 제외** (D4/OQ-3): 페르소나가 강세/기준/약세 확률을 출력하도록 LLM 프롬프트를 변경하는 작업은 후속 SPEC. 본 SPEC은 nullable 컬럼·저장 경로(DB 스키마)만 만든다.
8. **backtest/engine.py 재작성 금지** (OQ-5): BacktestResult 출력 스키마를 확장하지 않는다. 거래단위 통계는 roundtrips에서 계산하여 채점기에 주입한다.

---

## 확정된 결정 (Resolved Decisions) — 구 Open Questions

운영자가 2026-06-14 OQ 5건을 모두 확정했다. 아래 결정은 위 요구사항에 반영되었다.

- **RD-1 (구 OQ-1, COOL_DOWN)**: 발동 = 증거태그 규칙 위반 누적 3회 또는 자기자본 드로다운 -5% 이하. 해제 = 운영자 수동 /resume만 (SPEC-032 장전 자동 재개 대상 제외, daily_loss류와 동일 취급). 구체 임계는 본 SPEC에 고정, 운영 중 config 보정 가능. → REQ-048-M3-5 반영.
- **RD-2 (구 OQ-2, Kelly 활성화)**: 코어 Kelly 함수는 완전 구현하되, M2 채점기가 양의 엣지로 PASS하기 전까지 런타임에서 kelly_pct를 강제 0으로 유지(실질 거래 금지 게이트). W·R 출처는 edge/roundtrips.py 실측 라운드트립. → REQ-048-M1-1, REQ-048-M1-8 반영.
- **RD-3 (구 OQ-3, confidence 시나리오 확률)**: 본 SPEC은 DB 스키마(nullable 컬럼)+저장 경로만. 페르소나 프롬프트 변경은 후속 SPEC. → REQ-048-M3-4, Exclusions #7 반영.
- **RD-4 (구 OQ-4, heat 정의)**: 손절가까지 거리 기반 위험금액 합산. 손절가 부재 포지션은 명목가치 fallback. → REQ-048-M1-4 반영.
- **RD-5 (구 OQ-5, M2 실현가능성)**: 코드 검증 완료 — BacktestResult는 cagr/mdd/sharpe/trades/final_equity/equity_curve/daily_returns만 제공, 거래단위 통계 없음. 따라서 채점기는 주입형 순수 함수로 설계: 거래단위는 roundtrips, 포트폴리오 지표는 BacktestResult, IS/OOS는 walk_forward에서 주입. active 기간 트리밍은 채점기 측 전처리. 엔진 재작성 없음. → REQ-048-M2-1, REQ-048-M2-4, Exclusions #8 반영.
