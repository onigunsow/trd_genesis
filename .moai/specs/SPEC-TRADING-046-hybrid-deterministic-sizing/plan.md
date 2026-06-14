# Plan — SPEC-TRADING-046 Hybrid Deterministic Position Sizing

## 기술 접근 (Technical Approach)

LLM은 후보(ticker, side, conviction)를 계속 제안하고, **사이징만** 신설 결정적 모듈 `strategy/sizing/`로 이전한다. orchestrator `_execute_signal`(seam ~L893)이 `sizing_mode=deterministic`일 때 `sig['qty']` 대신 사이징 모듈을 호출한다. 사이징은 **변동성 타기팅**(ATR% 역비례)으로 notional을 정하고, 기존 `RISK_*` 캡을 읽어 자체 경계를 두되, 최종 거부 판정은 기존 `check_pre_order`에 그대로 위임한다(이중 캡 금지). 파라미터는 `config.py`에 단일 외부 진실원천으로 두어 SPEC-044 walk-forward가 스윕하고 향후 피드백 루프가 코드 수정 없이 튜닝한다.

### 확인된 코드 seam (2026-06-14 read-only 감사)

- `src/trading/personas/orchestrator.py` `_execute_signal` (~L893): `qty = int(sig.get("qty", 0) or 0)` — 여기가 사이징 plug-in 지점. SPEC-042 sell-side guard / SPEC-026 overheat policy와 공존해야 하므로, **사이징은 qty 결정 직후·overheat/sell-guard 진입 전**에 끼운다.
- `src/trading/personas/decision.py` (L163, L175-177): `{ticker, side, qty, rationale, confidence}` 영속화. `confidence`는 이미 nullable 저장. `qty`는 advisory로 보존, deterministic qty를 별도 기록.
- `src/trading/risk/limits.py` `check_pre_order` (L124): `RISK_SINGLE_ORDER_MAX(0.10)` / `RISK_PER_TICKER_MAX_POSITION(0.20)` / `RISK_TOTAL_INVESTED_MAX(0.80)` — 하드 천장. 사이징은 읽기만.
- `src/trading/strategy/volatility/thresholds.py` `get_dynamic_thresholds` (L41) + `atr.py` `compute_atr` (L23, `MIN_DAYS_FOR_ATR=5`, `ATR_PERIOD=14`): 변동성 타기팅 입력 재사용.
- `src/trading/config.py` L40-42 (`RISK_*` 캡), L132-159 (cost 단일원천, SPEC-044 통합 중): 사이징 파라미터를 같은 파일에 단일원천으로 추가, cost 상수는 참조만.
- `src/trading/backtest/walk_forward.py` `run_walk_forward` (L119) + `exit_sweep.py` (`ExitParams`/`run_sweep` param-grid 패턴, L40/L190): `SizingParams` 스윕을 동일 패턴으로 추가.

## Milestones (우선순위 · 시간 추정 없음)

### M1 (Priority High) — SizingParams 단일원천 + vol-targeting 코어
- `config.py`에 `SizingParams`(연환산 vol budget, ATR lookback, fallback 분율, confidence-damp OFF 기본) 단일원천 추가(env-overridable).
- `strategy/sizing/vol_target.py`: pure function `compute_qty(candidate, portfolio_state, params) -> (qty, sizing_reason)`. ATR% 역비례, vol budget 타겟, `estimate_fee` cost-aware, 캡 읽어 경계.
- ATR 부재 fallback(REQ-046-A3), below_min_lot zero(REQ-046-A4).
- **단위 테스트**: vol-targeting 수식(역비례·budget 스케일·반올림), fallback, zero-lot — 수학 전부 검증(TRUST 5 Tested).

### M2 (Priority High) — confidence anti-predictive 가드 [HARD]
- REQ-046-B1: confidence가 qty를 키우지 않음을 테스트로 고정(monotonic non-increasing in confidence).
- confidence=None 경로 == 기본 경로(REQ-046-B3).
- damp는 OFF 기본·하향-전용 옵션(REQ-046-B2).

### M3 (Priority High) — orchestrator seam + feature flag
- `sizing_mode`(`llm_direct`|`deterministic`, default `llm_direct`) config.
- `_execute_signal`에 분기 삽입: deterministic이면 사이징 모듈 호출, 아니면 byte-for-byte 현 경로.
- deterministic qty → `check_pre_order` 통과(REQ-046-D), LLM-advisory qty + deterministic qty + sizing_reason 영속(REQ-046-E3).
- **회귀 테스트**: `llm_direct` 경로가 현재와 동일함을 확정(SPEC-042/026 sell-guard·overheat 공존 보존).

### M4 (Priority Medium) — SPEC-044 하니스 연결
- `walk_forward.run_walk_forward`가 `SizingParams` 그리드를 train/test 분리로 평가하도록 확장(exit_sweep param-grid 패턴 재사용).
- look-ahead 부재를 테스트된 불변식으로(프로젝트 룰).
- A/B 비교 스캐폴드: deterministic vs llm_direct OOS 기대값.

## Risks

- **이중 캡 버그**: 사이징과 limits가 둘 다 캡을 적용하면 의도보다 작아짐 → REQ-046-D로 limits를 단일 판정자로 고정, 사이징은 읽기만.
- **seam 충돌**: SPEC-042 sell-side reconcile/clamp, SPEC-026 overheat size-cap과 순서 충돌 가능 → 사이징은 BUY 위주, sell은 기존 clamp 우선. M3 회귀 테스트로 보호.
- **과적합**: vol budget을 짧은 단일레짐에 맞추면 과적합 → SPEC-044 OOS 양수를 채택 게이트로(in-sample 헤드라인 금지).
- **절반의 측정 안도감**: 진입이 여전히 LLM이라 사이징만 룰화해도 OOS 음수 가능 → ADR P3 HARD 게이트로 흡수, 본 SPEC은 측정 가능성만 주장(수익 보장 아님).

## 검증/배포
- TDD reproduction-first(프로젝트 룰): vol-targeting 수식 단위테스트 선행, flag 기본 OFF라 배포 시 무동작(byte-for-byte) → paper에서 deterministic 토글 A/B.
- 마이그레이션: 신규 영속 컬럼(advisory/deterministic qty 기록) 필요 시 mig 예약(번호는 run 단계에서 현재 최신 확인 후 배정 — 028까지 적용 확인됨, 029+ 예약).
- 헬스체크/스모크: redeploy 후 `sizing_mode=llm_direct`에서 현 동작 무변화 확인이 1차 게이트.
