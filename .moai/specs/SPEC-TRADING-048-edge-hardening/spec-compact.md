# SPEC-TRADING-048 (compact) — 엣지 경화: 검증 게이트·사이징 가드·자기개선 루프

status: draft v0.3.0 · mode: tdd · brownfield delta · 신규 마이그: 033 (현재 최신 032) · labels: trading/sizing/validation/self-improvement

## WHY
SPEC-044 첫 비용보정 측정 = 마이너스 엣지: 거래당 -14,840원, KOSPI 알파 -11.03%p, confidence-P&L Spearman -0.455(반예측적). 실거래 확대는 OOS 양수 확인까지 보류. 외부 퀀트 저장소(tradermonty/claude-trading-skills, quant-sentiment-ai, KIS 백테스터 문서)에서 검증·사이징·자기개선 *방법론·공식만* 결정적 파이썬으로 이식. **새 매매 신호 없음** — 규율/검증 레이어 우선.

## HARD 제약
- **CORE**: Kelly 수학·백테스트 채점·postmortem 분류/귀인 = 시장 중립 순수 함수. KRX/KIS 종속(호가·최소주문·수수료·세금·KOSPI 벤치마크·통화반올림)은 주입 파라미터/어댑터. 미국 확장 시 어댑터만 교체.
- 외부 저장소 import 금지(공식만 재구현). sizing_mode 기본 OFF 불변. confidence 비증폭 불변(SPEC-046 B1). 1420 테스트 0 회귀.

## 요구사항 (5 모듈)
- **CORE** (Ubiquitous): 순수함수·상수 하드코딩 금지·미국 어댑터 재사용 가능.
- **M1 사이징가드 [MODIFY]** (SPEC-046 확장, vol_target.py 위):
  - kelly_pct = W-(1-W)/R (W·R 출처=roundtrips 실측), half-Kelly만. kelly_pct<=0(W=0/R<=0 포함) → qty=0 거래금지.
  - vol-targeting qty vs half-Kelly cap → min. heat 상한 0.08(=Σ(진입가-손절가거리×수량)/자기자본, 손절부재 시 명목가치 fallback); 상한 내 축소, 최소주문으로도 초과면 0.
  - confidence로 qty 증폭 금지. Kelly/heat 가드는 SIZING_MODE 무관 항상 활성(게이트). KRX 호가/최소주문/반올림 주입 적용.
  - **M1-8: M2 PASS 전 런타임 kelly_pct 강제 0**(OQ-2 확정) → 양의 엣지 입증 전 실질 거래금지.
- **M2 검증게이트 [NEW]** (주입형 순수함수, 엔진 재작성 금지 — OQ-5 확정):
  - BacktestResult=cagr/mdd/sharpe/trades/final_equity/equity_curve/daily_returns만 제공(거래단위 통계 없음, 코드검증완료). → 거래단위(expectancy/profit_factor/win_rate/avg_win/avg_loss/표본수)는 roundtrips net_pnl에서 계산, 포트폴리오 지표는 BacktestResult, IS/OOS는 walk_forward에서 **주입**.
  - **거래세 명시**: expectancy/profit_factor/avg_win/avg_loss는 수수료+거래세(매도 0.18%) 차감 **net**(RoundTrip.net_pnl). gross 금지.
  - **N2 배점 (각 0~20·총 100)**: expectancy(<=0→0, EXP_FULL 도달 20 선형) / profit_factor(<1.0→0, 1.0~1.5 선형, >=1.5→20) / 표본수(30미만→0/100→15/200+→20 보간) / MDD-risk(>=50%→0 파이어월, 20*(1-|MDD|/0.5)) / robustness(5년미만→0, OOS<IS*0.5→0, 파라미터7초과 1개당-3).
  - **N2 컷오프**: PASS=합계>=70 AND 모든차원≠0 AND expectancy>0; REVISE=50~69; REJECT=<50 OR 임의차원0 OR expectancy<=0.
  - walk-forward OOS<IS*50% → robustness 0. 과적합 체크리스트(룰10+/소수점임계/연10회미만)→warnings. 인플레함정 회피: 채점기 전처리로 선행 0-weight 제거 후 active기간 Sharpe·CAGR, 미청산 승률 경고.
  - PASS미달 → 사이징A/B·실거래확대 차단 + M1-8 게이트 연동. **현재 마이너스/소표본 = REJECT(파이어월). 정상입력(200+/PF1.8/exp>0/MDD20%/5년+)=PASS 입증.**
- **M3 자기개선 [NEW]**:
  - **N1: 결정(decision) 단위 분류** (라운드트립 단위 아님 — MISSED 모순 해소). 함수 classify_decision_outcome(), 트리거=일일 postmortem 배치. 두 경로:
    - 진입경로(roundtrip 존재): TRUE_POSITIVE(realized>0 & rel>0)/FALSE_POSITIVE(buy conf>=0.6 & rel_20d<0)/REGIME_MISMATCH(신호방향≠regime). 우선순위 REGIME>FALSE_POS>TRUE_POS.
    - 미진입경로(roundtrip 없음, hold/risk_reviews REJECT/HOLD): rel_20d>0 → MISSED.
    - 데이터=persona_decisions+risk_reviews+roundtrips(LEFT JOIN). 사후분석=새 신호 없음. 페르소나(macro/micro/portfolio/decision) 귀인.
  - 페르소나별 confidence 역상관 국소화 → weight 조정 *제안*(20표본+ 만, 자동적용 금지).
  - confidence → prob_bull/prob_base/prob_bear **nullable 컬럼+저장경로만**(mig 033). |sum-1|<=1e-6. **프롬프트 변경은 후속 SPEC**(OQ-3 확정).
  - COOL_DOWN: 규칙위반 누적3회 또는 드로다운<=-5% → review-only(매수 사이징0/매수금지). halt_state/일일한도 위 증거태그 레이어. **해제=수동 /resume만**(SPEC-032 자동재개 제외, OQ-1 확정).
  - SPEC-047 대시보드에 postmortem/calibration 읽기전용 뷰.
- **NFR**: 1420 테스트 0회귀 · TDD 코어 외부의존0 · mig 033 conftest 호환.

## Exclusions
외부저장소 통째 import 금지 · 미국 데이터/브로커(SEC/Form4/옵션/Alpaca/13F) 제외 · dartlab·KIS Lean 본격통합 후속 · 새 신호/전략 금지 · 페르소나 weight 자동적용 금지(제안만) · sizing_mode 기본값 변경 금지 · **confidence 시나리오 확률 생성 프롬프트 변경 제외(후속)** · **backtest/engine.py 재작성 금지**.

## 통합 맵 (핵심)
사이징 vol_target.py compute_qty(L35-156)/config.py SIZING_MODE(L214) SizingParams(L183-209)/orchestrator _execute_signal(L898-1025, 사이징 L916-943). 측정 edge/roundtrips.py build_roundtrips(L127-200) RoundTrip.net_pnl·confidence.py analyze(L106)·benchmark.py alpha_pct. 백테스트 backtest/engine.py(BacktestResult read-only)·walk_forward.py·exit_sweep.py. 리스크 circuit_breaker.py·limits.py check_pre_order(L124-200). 대시보드 queries.py(dashboard_ro, mig032). DB persona_decisions(confidence NUMERIC(4,2)), risk_reviews(verdict). per-persona weighting 코드 부재.

## Resolved Decisions (운영자 확정, 구 Open Questions)
RD-1(OQ-1) COOL_DOWN=위반3회/드로다운-5%, 해제=수동/resume(SPEC-032 제외) · RD-2(OQ-2) Kelly 코어 완전구현+M2 PASS전 강제0, W·R=roundtrips · RD-3(OQ-3) confidence 확률=스키마/저장만, 프롬프트 후속 · RD-4(OQ-4) heat=손절거리 위험금액+명목 fallback · RD-5(OQ-5) 채점기 주입형 순수함수, 엔진 미변경.
