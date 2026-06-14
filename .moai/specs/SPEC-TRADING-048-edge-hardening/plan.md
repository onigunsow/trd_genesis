# SPEC-TRADING-048 구현 계획 (plan.md)

development_mode: **tdd** · brownfield delta · 시장 중립 코어 제약

## 기술 접근 (Technical Approach)

핵심 설계 원칙: **순수 코어 + 어댑터 분리**. 모든 수학/판정 로직은 시장 중립 순수 함수로 작성하고, 한국 시장 종속(호가·수수료·세금·벤치마크)은 호출자가 주입한다. 미국 확장 시 어댑터만 교체.

기존 자산 최대 재사용:
- M2 채점기는 **주입형 순수 함수**다(OQ-5 확정). backtest/engine.py를 재작성하지 않고, 거래단위 통계는 `edge/roundtrips.py`의 RoundTrip.net_pnl에서 계산, 포트폴리오 지표는 BacktestResult(cagr/mdd/sharpe/equity_curve/daily_returns)에서, IS/OOS는 `backtest/walk_forward.py`에서 각각 주입받는다.
- M3는 `edge/roundtrips.py` build_roundtrips() FIFO와 `edge/benchmark.py` alpha_pct를 재사용한다.
- M1은 `strategy/sizing/vol_target.py` compute_qty()의 출력 위에 Kelly 상한·heat 가드를 얹는다.

## 마일스톤 (우선순위 기반, 시간 추정 없음)

### M1 — 사이징 가드 [MODIFY] (Priority: High)

선행: 없음 (기존 vol_target 위에 얹음). REQ-048-M1-1~8, REQ-048-CORE-1/2 충족.

작업 단위:
1. [NEW] 시장 중립 Kelly 코어 순수 함수 모듈 작성 (예: `src/trading/strategy/sizing/kelly.py`).
   - `kelly_fraction(win_rate, payoff_ratio) -> float` : W - (1-W)/R. W=0 또는 R<=0이면 <=0 반환(REQ-048-M1-2).
   - `half_kelly_cap(kelly_pct, equity, price, *, lot_size, tick_size, round_fn) -> int` : half-Kelly 상한 수량, 주입된 호가/통화 규칙 적용.
   - `portfolio_heat(open_positions, *, heat_cap) -> float` : 각 포지션 위험금액(진입가-손절가 거리 × 수량, 손절 부재 시 명목가치 fallback)/자기자본 합산 (OQ-4 확정, 순수 함수, 상수 인자 주입).
   - heat 축소 함수: 상한 내로 축소, 최소주문수량으로도 초과면 0 (REQ-048-M1-4 분기).
2. [MODIFY] `orchestrator.py` _execute_signal() 사이징 호출부(L916-943)에 Kelly 상한·heat 가드·M2 게이트 hook.
   - vol-targeting qty와 half-Kelly cap 중 min 채택 (REQ-048-M1-3).
   - kelly_pct <= 0 이면 qty=0 거래 금지 (REQ-048-M1-2).
   - heat 상한 초과 시 축소/0 (REQ-048-M1-4).
   - **M2 PASS 전 kelly_pct 강제 0 게이트** (REQ-048-M1-8, OQ-2 확정): 채점기 PASS 상태를 확인하기 전까지 실측 kelly_pct를 0으로 덮어쓴다.
3. [MODIFY] `config.py` SizingParams(L183-209)에 heat_cap(기본 0.08), half_kelly 파라미터 추가. SIZING_MODE(L214) 기본값 **변경 없음**.
4. confidence 비증폭 불변 회귀 테스트 (REQ-048-M1-5).

위험: 없음(heat 정의·게이트 정책 OQ-4/OQ-2로 확정). 손절가 부재 포지션은 명목가치 fallback으로 확정.

### M2 — 검증 게이트 [NEW] (Priority: High)

선행: 없음 (기존 backtest 엔진 read-only 재사용). REQ-048-M2-1~5, REQ-048-CORE-1/2 충족.

**설계 확정 (OQ-5)**: backtest/engine.py의 BacktestResult는 cagr/mdd/sharpe/trades/final_equity/equity_curve/daily_returns만 제공하고 거래단위 avg_win/avg_loss/profit_factor/win_rate는 **없음**(코드 검증 완료). 따라서 엔진을 재작성하지 않고 채점기를 **주입형 순수 함수**로 설계한다.

작업 단위:
1. [NEW] 거래단위 통계 계산 순수 함수 (예: `src/trading/edge/trade_stats.py` 또는 evaluate_backtest 내부).
   - 입력: roundtrips의 RoundTrip.net_pnl 목록.
   - 출력: win_rate, avg_win, avg_loss, profit_factor, expectancy, 표본수.
2. [NEW] 시장 중립 5차원 채점기 순수 함수 모듈 (예: `src/trading/edge/evaluate_backtest.py`).
   - `score_backtest(trade_stats, portfolio_metrics, is_oos, *, scoring_params) -> ScoreCard(score, verdict, dimension_scores, warnings)`.
   - trade_stats(1번 출력, net 기준) + portfolio_metrics(BacktestResult: mdd 등) + is_oos(walk_forward) 를 **주입**받는다.
   - **배점 (N2 확정, 각 0~20점·총 100점)**: expectancy(<=0→0, EXP_FULL 도달 20 선형) / profit_factor(<1.0→0, 1.0~1.5 선형, >=1.5→20) / 표본수(30미만→0, 100→15, 200+→20 보간) / MDD-risk(>=50%→0 파이어월, 20*(1-|MDD|/0.5)) / robustness(5년미만→0, OOS<IS*0.5→0, 파라미터 7초과 1개당 -3).
   - **판정 컷오프**: PASS = 합계>=70 AND 모든 차원 0점 아님 AND expectancy>0; REVISE = 50~69; REJECT = <50 OR 임의 차원 0점 OR expectancy<=0.
   - 거래단위 통계는 모두 net(수수료+거래세 0.18% 차감) 기준이어야 함.
3. [NEW] walk-forward 수용 판정: OOS < IS*0.5 이면 robustness 실패 (REQ-048-M2-2). `walk_forward.py` 출력 주입.
4. [NEW] 과적합 사전 체크리스트 함수 (REQ-048-M2-3): 룰 조건 수·임계 소수점 자릿수·연간 기회 수 → 경고 목록 부착.
5. [NEW] 인플레 함정 회피 전처리 (REQ-048-M2-4): equity_curve/daily_returns에서 선행 0-weight 일자 제거 후 active 기간 기반 Sharpe/CAGR 재계산, 미청산 포지션 승률 경고. **채점기 측 전처리**(엔진 미변경).
6. [NEW] 게이트 진입점: 채점이 PASS 미만이면 사이징 A/B·실거래 확대 차단 + M1-8 kelly 0 게이트 연동 (REQ-048-M2-5). 현재 입력은 REJECT 검증.

위험: 없음(OQ-5 코드 검증으로 주입형 설계 확정, 엔진 재작성 불필요).

### M3 — 자기개선 루프 [NEW] (Priority: Medium)

선행: M1/M2 코어 패턴 확립 후 권장. REQ-048-M3-1~6, REQ-048-CORE-1/3 충족.

작업 단위:
1. [NEW] 시장 중립 postmortem 분류 순수 함수 (예: `src/trading/edge/postmortem.py`). **결정(decision) 단위 분류** (N1 확정).
   - `classify_decision_outcome(decision, roundtrip_or_none, relative_5d, relative_20d, regime, *, thresholds) -> Label`:
     - 진입 경로(roundtrip 존재): realized_return·relative로 TRUE_POSITIVE/FALSE_POSITIVE/REGIME_MISMATCH (우선순위 REGIME_MISMATCH > FALSE_POSITIVE > TRUE_POSITIVE).
     - 미진입 경로(roundtrip 없음, hold 또는 risk_reviews REJECT/HOLD): relative_20d>0 이면 MISSED.
   - 데이터 출처: persona_decisions(전체) + risk_reviews(verdict) + roundtrips(LEFT JOIN). 사후 분석이므로 새 신호 생성 없음.
   - 트리거: "결정 결과 평가 사이클"(일일 postmortem 배치)에서 호출 (라운드트립 종료 hook 아님).
   - `attribute_to_persona(decision, decision_record) -> persona` (REQ-048-M3-2).
   - `propose_persona_weights(per_persona_stats, *, min_sample=20) -> proposals` 제안만, 자동적용 없음 (REQ-048-M3-3).
   - 입력은 persona_decisions/risk_reviews/build_roundtrips()/benchmark.py 재사용.
2. [NEW] confidence 시나리오 확률 **스키마+저장 경로만** + 마이그레이션 **033** (REQ-048-M3-4, OQ-3 확정). persona_decisions에 prob_bull/prob_base/prob_bear **nullable** 컬럼 추가. 합 검증 |sum-1|<=1e-6(세 값 존재 시). **프롬프트 변경은 후속 SPEC** — 컬럼은 NULL 허용.
3. [NEW] COOL_DOWN 리스크 상태 (REQ-048-M3-5, D5/OQ-1 확정): 규칙위반 누적 3회 또는 드로다운 -5% → review-only(매수 사이징 0/매수 금지). circuit_breaker.py/limits.py 위 증거태그 레이어. **해제는 수동 /resume만**(SPEC-032 자동재개 제외, daily_loss류 취급).
4. [MODIFY] 대시보드: `dashboard/queries.py`에 postmortem/calibration 읽기전용 쿼리 추가 (REQ-048-M3-6).

위험: 없음(OQ-1/OQ-3 확정). COOL_DOWN은 daily_loss류로 자동재개 제외, confidence 분해는 스키마-only.

## DB 마이그레이션

- 본 SPEC 신규 마이그레이션 = **033** (현재 최신 032). 내용: persona_decisions에 prob_bull/prob_base/prob_bear nullable 컬럼 + COOL_DOWN 상태 컬럼/테이블.
- conftest.py fake_cursor/fake_conn/patch_db_connection 호환 필수 (REQ-048-NFR-3).

## 테스트 전략 (TDD)

- 각 코어 순수 함수는 외부 의존 0으로 단위 테스트 (REQ-048-NFR-2). 시장 중립성 증명: 한국 파라미터 세트와 가상 미국 파라미터 세트 두 가지로 동일 코어 호출하여 결과가 입력에 의해서만 결정됨을 검증 (REQ-048-CORE 인수 기준).
- 회귀: 기존 1420 테스트 0 회귀 (REQ-048-NFR-1).
- 게이트 검증: 현재 마이너스 기대값/소표본 입력 → REJECT (REQ-048-M2-5).
- `tests/` 미러 구조, test_<module>.py.

## 위험 요약 (OQ 확정 후 잔여 위험)

| 위험 | 영향 | 완화 |
|------|------|------|
| RoundTrip.net_pnl이 거래세(매도 0.18%)까지 차감한 net인지 확인 필요 | expectancy·profit_factor 왜곡(세금 미차감 시 엣지 과대평가) | **run 단계 선행 검증 항목**: RoundTrip.net_pnl 정의에서 매수/매도 수수료+거래세 0.18% 포함 여부 확인. 미포함이면 채점기 입력 전 net 보정 적용 |
| M2 PASS 게이트가 모든 진입 경로(워치독 등 포함)에서 일관 적용되는지 | kelly 0 게이트 우회 가능성 | _execute_signal 단일 seam에 게이트 집중, 우회 경로 회귀 테스트 |
| 미진입 결정(MISSED) 평가에 risk_reviews verdict 조인 정확성 | MISSED 오분류 | run 단계에서 persona_decisions↔risk_reviews↔roundtrips 조인 키 검증 |
| 기존 1420 테스트 회귀 | NFR-1 위반 | 각 마일스톤 후 전체 스위트 실행 |

확정 완료(위험 해소): heat 정의(OQ-4 손절거리 위험금액+명목 fallback), 엔진 재작성(OQ-5 주입형), confidence 프롬프트(OQ-3 스키마-only), COOL_DOWN 자동재개(OQ-1 수동 /resume), Kelly 활성화(OQ-2 M2 PASS 전 강제 0).

## 비범위 (plan 차원 재확인)

새 매매 신호 없음 · 외부 저장소 import 없음 · 미국 어댑터 구현 없음 · 페르소나 weight 자동적용 없음 · sizing_mode 기본값 변경 없음.
