# SPEC-TRADING-048 인수 기준 (acceptance.md)

Given-When-Then 시나리오. 각 마일스톤 2개 이상. 모든 기준은 관측 가능(테스트 출력·DB 행·판정값).

---

## CORE: 시장 중립 코어 재사용성

### AC-CORE-1 — 코어 함수는 시장 상수를 하드코딩하지 않는다
- **Given** Kelly/채점/postmortem 코어 순수 함수가 구현되어 있고
- **When** 한국 파라미터 세트(KRX 호가·수수료 0.015%·세금 0.18%·KOSPI 벤치마크)와 가상 미국 파라미터 세트(센트 호가·SEC 수수료·SPY 벤치마크)를 각각 주입하여 동일 입력으로 호출하면
- **Then** 두 호출 모두 코드 수정 없이 실행되고, 출력은 오직 주입된 파라미터와 입력 인자에 의해서만 결정된다 (코어 본문에 한국 상수 grep 결과 0건).

### AC-CORE-2 — 코어는 외부 I/O 없이 단위 테스트된다
- **Given** 코어 모듈(kelly.py, evaluate_backtest.py, postmortem.py)이 있고
- **When** DB·네트워크·시각 패치 없이 단위 테스트를 실행하면
- **Then** 모든 코어 함수 테스트가 통과한다 (fake_cursor/fake_conn 불필요).

---

## M1: 사이징 가드

### AC-M1-1 — negative-Kelly 바닥은 거래를 금지한다
- **Given** 측정 승률 W·손익비 R로 계산한 kelly_pct <= 0 인 신호가 있고
- **When** _execute_signal()이 사이징을 수행하면
- **Then** 산출 수량은 0이고 주문이 제출되지 않는다 (테스트에서 qty == 0, no order submitted).

### AC-M1-2 — Kelly 상한과 vol-targeting 중 작은 값 채택
- **Given** vol-targeting compute_qty()가 100주를 산출하고 half-Kelly cap이 40주인 상황에서
- **When** 사이징 가드가 적용되면
- **Then** 최종 수량은 40주(min)이고, 반대로 cap이 150주면 100주가 채택된다.

### AC-M1-3 — confidence는 수량을 키우지 않는다 (불변)
- **Given** 동일 신호를 confidence 0.5와 0.95로 두 번 사이징하면
- **When** 두 경우의 최종 수량을 비교하면
- **Then** confidence 0.95 케이스의 수량이 0.5 케이스보다 크지 않다 (REQ-046-B1 회귀 보존).

### AC-M1-4 — heat 상한 초과 시 축소, 최소주문으로도 초과면 0
- **Given** 미결제 포지션 합산 heat가 0.07이고 상한이 0.08, 신규 진입이 heat를 0.09로 만들 때 (heat = Σ(진입가-손절가 거리 × 수량)/자기자본, 손절 부재 시 명목가치 fallback)
- **When** heat 가드가 적용되면
- **Then** 신규 수량이 heat <= 0.08 이 되도록 축소되고, 주입된 최소주문수량으로조차 0.08을 초과하면 수량은 0이 된다.

### AC-M1-5 — Kelly/heat 가드는 SIZING_MODE와 무관하게 작동
- **Given** SIZING_MODE 기본값 OFF(llm_direct)인 상태에서
- **When** kelly_pct <= 0 인 신호가 들어오면
- **Then** mode와 무관하게 거래가 금지된다 (게이트 항상 활성).

### AC-M1-6 — KRX 호가단위·최소주문·반올림 적용 (REQ-048-M1-7)
- **Given** half-Kelly cap이 자기자본 기준 43.7주에 해당하고 주입된 최소주문수량 1주·정수 반올림 규칙이 있을 때
- **When** half_kelly_cap()이 수량을 확정하면
- **Then** 산출 수량은 주입된 규칙에 따라 정수(예: 43주)로 반올림되고 최소주문수량 미만이면 0이 된다 (코어 본문에 KRX 상수 하드코딩 없음).

### AC-M1-7 — M2 PASS 전에는 kelly_pct가 강제 0이다 (REQ-048-M1-8, OQ-2)
- **Given** 실측 라운드트립으로 계산한 kelly_pct가 0.12(양수)이지만 해당 전략에 대한 M2 채점기 PASS 판정이 아직 없을 때
- **When** _execute_signal()이 사이징을 수행하면
- **Then** 런타임 게이트가 kelly_pct를 0으로 덮어써서 산출 수량이 0이 되고 거래가 금지된다. M2 PASS 상태가 되면 동일 신호에서 실측 kelly_pct(0.12)가 유효해진다.

---

## M2: 검증 게이트

### AC-M2-1 — 현재 마이너스 엣지는 REJECT 된다 (파이어월 자동, 수용 기준 검증)
- **Given** 현재 측정값(net 거래당 -14,840원, 표본 8건, 알파 -11.03%p)을 채점기에 입력하면 (expectancy·profit_factor는 수수료+거래세 0.18% 차감 net 기준)
- **When** score_backtest()를 실행하면
- **Then** 표본수 차원 0점(거래 30 미만, 파이어월) **그리고** expectancy <= 0 → N2 컷오프 규칙(임의 차원 0점 또는 expectancy<=0 → REJECT)에 의해 합계와 무관하게 판정은 **REJECT** 이다.

### AC-M2-1b — 정상 입력은 PASS가 산출된다 (양성 시나리오, N2)
- **Given** 표본 200+건·profit_factor 1.8·expectancy>0(EXP_FULL 도달)·MDD 20%·테스트 5년+·OOS>=IS*0.5·파라미터 5개인 net 백테스트를 입력하면
- **When** score_backtest()를 실행하면
- **Then** 차원 배점은 expectancy 20 + profit_factor 20 + 표본수 20 + MDD-risk 약 12(=20*(1-0.2/0.5)) + robustness 20 = 약 92점이고, 모든 차원 0점 아님 + expectancy>0 → 판정은 **PASS**(합계>=70) 이다. (PASS 산출 가능성 입증)

### AC-M2-2 — PASS 미달 시 사이징 A/B·실거래 확대 차단
- **Given** 채점 판정이 REJECT 또는 REVISE 인 전략에 대해
- **When** 사이징 A/B 활성화 또는 실거래 확대를 시도하면
- **Then** 게이트가 차단하고 차단 사유(미달 차원)를 반환한다.

### AC-M2-3 — walk-forward OOS < IS*0.5 는 robustness 실패
- **Given** IS expectancy 100, OOS expectancy 40 인 walk-forward 결과가 있고
- **When** 채점기가 robustness 차원을 평가하면
- **Then** robustness가 실패 처리되고 경고가 기록된다 (OOS/IS = 0.4 < 0.5).

### AC-M2-4 — 지표 인플레 함정 회피
- **Given** 웜업 idle 20일 + active 80일인 백테스트(equity_curve/daily_returns 제공)와 미청산 포지션 2건이 있고
- **When** 채점기가 선행 0-weight 일자를 제거한 뒤 Sharpe·CAGR·승률을 계산하면
- **Then** Sharpe/CAGR는 active 80일 기준으로 계산되고, 승률에는 미청산 포지션 포함 경고 플래그가 부착된다 (엔진 출력은 변경되지 않고 채점기 전처리로 수행).

### AC-M2-5 — 과적합 사전 체크리스트 경고 부착 (REQ-048-M2-3)
- **Given** 룰 조건 12개·임계값에 소수점 4자리·연간 기회 8회인 전략 입력이 있고
- **When** 채점기가 사전 점검을 실행하면
- **Then** ScoreCard.warnings에 세 경고(룰 조건 10+개, 커브핏 의심 소수점 과다, 연 10회 미만 통계 무의미)가 모두 부착된다. 조건이 모두 임계 이하면 경고는 비어 있다.

### AC-M2-6 — 채점기는 주입형 순수 함수다 (OQ-5)
- **Given** roundtrips에서 계산한 trade_stats, BacktestResult의 portfolio_metrics, walk_forward의 is_oos를 인자로 주입하면
- **When** score_backtest()를 호출하면
- **Then** backtest/engine.py를 import/호출하지 않고도 ScoreCard가 산출된다 (엔진 재작성 0, 거래단위 통계는 roundtrips net_pnl에서 계산).

---

## M3: 자기개선 루프

### AC-M3-1 — 결정 결과 평가 사이클이 결정 단위로 4분류·페르소나 귀인한다 (N1 확정)
- **Given** persona_decisions·risk_reviews·roundtrips 조인 결과로 다음 4개 결정이 있고 (두 경로 — 진입/미진입 — 모두 실제 산출 가능한 입력):
  - **케이스 A (진입경로, roundtrip 존재)**: realized_return=+5%, relative_20d=+2% → 기대 TRUE_POSITIVE
  - **케이스 B (진입경로, roundtrip 존재)**: 매수 entry_confidence=0.8, relative_20d=-3% → 기대 FALSE_POSITIVE
  - **케이스 C (미진입경로, roundtrip 없음, risk_reviews verdict=HOLD)**: 결정 시점 이후 relative_20d=+4% → 기대 MISSED (roundtrip이 없어도 persona_decisions/risk_reviews 행으로 입력 산출 가능 — N1 모순 해소)
  - **케이스 D (진입경로, roundtrip 존재)**: signal_dir=buy, regime=bearish → 기대 REGIME_MISMATCH (진입경로 우선순위 최상위)
- **When** classify_decision_outcome() + attribute_to_persona()를 결정 결과 평가 사이클(일일 postmortem 배치)에서 실행하면
- **Then** 각 케이스가 기대 라벨로 분류되고(진입경로 충돌 시 REGIME_MISMATCH > FALSE_POSITIVE > TRUE_POSITIVE, MISSED는 미진입경로 전용), 발신 페르소나에 귀속되어 페르소나별 통계가 갱신된다. 임계(confidence 0.6, relative 0)는 주입 파라미터다.

### AC-M3-2 — weight 조정은 20표본 미만이면 제안하지 않는다
- **Given** 특정 페르소나의 분류 표본이 19건인 상황에서
- **When** propose_persona_weights()를 실행하면
- **Then** 해당 페르소나에 대한 weight 제안은 산출되지 않는다 (min_sample=20 미충족). 표본 20건이면 제안이 산출되나 자동 적용은 되지 않는다.

### AC-M3-3 — confidence 시나리오 확률 스키마(nullable) 기록 및 합 검증 (D13/OQ-3)
- **Given** 마이그레이션 033 적용 후, prob_bull/prob_base/prob_bear 컬럼이 nullable로 존재하고
- **When** (a) 세 값이 모두 NULL인 결정과 (b) 세 값(0.3/0.5/0.2)을 가진 결정을 각각 저장하면
- **Then** (a)는 NULL 허용으로 정상 저장되고, (b)는 정상 저장되며 |0.3+0.5+0.2 - 1| <= 1e-6 을 만족한다. 페르소나 프롬프트 변경 없이도 스키마/저장 경로가 동작한다(프롬프트는 후속 SPEC).

### AC-M3-4 — COOL_DOWN은 구체 임계로 발동하고 수동 해제만 가능하다 (D5/OQ-1)
- **Given** 증거태그 규칙 위반이 누적 2회인 상태에서
- **When** 3회째 위반이 기록되면 (또는 자기자본 드로다운이 -5% 이하로 떨어지면)
- **Then** COOL_DOWN 상태로 전환되어 신규 매수 사이징이 0이 되고 매수가 금지되며, 기존 halt_state/일일한도와 독립된 레이어로 동작한다. **이 상태는 SPEC-032 장전 자동 재개로 해제되지 않고 운영자 수동 /resume으로만 해제된다** (누적 2회 시점에는 발동하지 않음).

### AC-M3-5 — postmortem/calibration 대시보드 읽기전용 노출 (REQ-048-M3-6, D12)
- **Given** SPEC-047 대시보드(dashboard_ro role)가 활성이고 postmortem 분류·calibration 데이터가 DB에 있을 때
- **When** dashboard/queries.py의 신규 postmortem/calibration 쿼리를 실행하면
- **Then** 분류 분포와 calibration 점수가 읽기전용으로 반환되고, 어떤 쓰기/제어 액션도 수행하지 않는다.

---

## NFR: 비기능

### AC-NFR-1 — 기존 테스트 0 회귀
- **Given** 본 SPEC 구현 완료 후
- **When** 전체 테스트 스위트를 실행하면
- **Then** 기존 1420 passed 유지, 신규 회귀 0 (pre-existing 6건 제외).

### AC-NFR-2 — 마이그레이션 033 적용 가능
- **Given** 마이그레이션 033이 작성되어 있고
- **When** 마이그레이션을 적용하면
- **Then** 스키마가 정상 적용되고 conftest.py 픽스처와 호환된다.

---

## Edge Cases

- W=0 또는 R<=0 (Kelly 분모 0/음수) → kelly_pct<=0 간주, 거래 금지(qty 0), 예외 미발생.
- 표본 0건 백테스트 → 채점 표본수 0점, REJECT, division-by-zero 없음.
- 손절가 부재 포지션의 heat 계산 → 명목가치 fallback(OQ-4 확정) 적용, 예외 미발생.
- 미진입 결정에 대응 roundtrip이 없음 → MISSED 경로(persona_decisions/risk_reviews 행)로 정상 분류, 예외 미발생.
- confidence 시나리오 확률 세 컬럼 NULL → 정상 저장(프롬프트 미구현 단계), Brier 계산은 NULL 행 제외.
- roundtrips net_pnl이 거래세 미차감으로 판명 → 채점기 입력 전 net 보정(run 선행 검증), gross 채점 금지.

## 품질 게이트 / Definition of Done

- [ ] REQ-048-CORE/M1(1~8)/M2/M3/NFR 전 요구사항에 대응 테스트 존재 (TDD, RED→GREEN 증거).
- [ ] 코어 함수 한국/미국 두 파라미터 세트 재사용성 테스트 통과 (AC-CORE-1).
- [ ] 현재 측정값 입력 시 채점기 REJECT(파이어월) + 정상 입력 PASS 확인 (AC-M2-1, AC-M2-1b).
- [ ] 5차원 배점·PASS>=70/REVISE 50~69/REJECT 컷오프 확인 (AC-M2-1b).
- [ ] expectancy·profit_factor net(거래세 0.18% 차감) 기준 확인.
- [ ] 결정 단위 4분류(MISSED 미진입경로 포함) 산출 확인 (AC-M3-1).
- [ ] M2 PASS 전 kelly_pct 강제 0 게이트 확인 (AC-M1-7).
- [ ] 채점기 주입형 순수함수·엔진 재작성 0 확인 (AC-M2-6).
- [ ] COOL_DOWN 3회/-5% 발동 + 수동 /resume 전용 해제 확인 (AC-M3-4).
- [ ] 기존 1420 테스트 0 회귀 (AC-NFR-1).
- [ ] 마이그레이션 033 적용·롤백 확인 (prob_* nullable 컬럼 포함).
- [ ] SIZING_MODE 기본값 OFF 불변, confidence 비증폭 불변 회귀 통과.
- [ ] 페르소나 weight 자동 적용 없음(제안만), confidence 프롬프트 미변경(스키마-only) 확인.
