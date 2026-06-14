# Acceptance — SPEC-TRADING-046 Hybrid Deterministic Position Sizing

## Given-When-Then 시나리오

### AC-1 — 변동성 타기팅: 저변동 종목이 고변동 종목보다 크게 사이징 (REQ-046-A1)
- **Given** 동일 현금/캡 하에서 종목 X(ATR% 낮음)와 Y(ATR% 높음), 같은 reference price
- **When** `compute_qty`를 deterministic 파라미터로 각각 호출
- **Then** X의 notional > Y의 notional (변동성에 역비례), 둘 다 vol budget 타겟 일관, 결과는 두 번 호출해도 동일(결정적)

### AC-2 — confidence는 사이즈를 키우지 않는다 [HARD] (REQ-046-B1)
- **Given** 동일 후보·포트폴리오 상태에서 confidence ∈ {0.1, 0.5, 0.9}만 변경
- **When** `compute_qty` 호출
- **Then** 산출 qty가 confidence에 대해 **non-increasing** (절대 커지지 않음). damp OFF 기본에서는 세 값 모두 동일 qty. confidence=None도 동일 경로(REQ-046-B3)

### AC-3 — 캡은 절대 초과되지 않으며 이중 캡 없음 (REQ-046-D)
- **Given** vol-targeting 제안이 단건 10%/종목 20%/총 80% 캡을 넘는 큰 notional을 낼 상황
- **When** deterministic qty → `check_pre_order` 통과
- **Then** 최종 주문은 캡 내. **And** 제안이 이미 캡 내일 때 `check_pre_order`는 no-op(제안 qty가 그대로 바인딩) — 사이징과 limits가 캡을 중복 적용해 의도보다 작아지지 않음

### AC-4 — feature flag 기본 OFF = byte-for-byte 현 동작 (REQ-046-E1/E2)
- **Given** `sizing_mode = llm_direct` (default)
- **When** `_execute_signal`가 신호를 실행
- **Then** 현재와 동일하게 `sig['qty']` 사용, 사이징 모듈 미호출. SPEC-042 sell-guard·SPEC-026 overheat 경로 보존(회귀 0)

### AC-5 — deterministic 모드: advisory/deterministic 양쪽 기록 (REQ-046-E2/E3)
- **Given** `sizing_mode = deterministic`
- **When** BUY 신호 실행
- **Then** 주문 qty = 사이징 모듈 산출값(LLM `qty` 무시). **And** LLM-advisory qty + deterministic qty + sizing_reason이 영속(A/B·감사용)

### AC-6 — 파라미터 외부화 + 044 스윕 가능 (REQ-046-C/E4)
- **Given** `SizingParams`(vol budget, ATR lookback, fallback 분율)가 config 단일원천에 존재
- **When** SPEC-044 `run_walk_forward`에 사이징 파라미터 그리드를 train/test 분리로 투입
- **Then** OOS 기대값이 산출됨(look-ahead 부재는 테스트된 불변식). 코드 수정 없이 파라미터만 바꿔 재평가 가능

## Edge Cases

- ATR 부재(< MIN_DAYS_FOR_ATR=5): 보수 고정 분율 fallback, `sizing_reason="vol_unavailable"` (REQ-046-A3)
- notional이 1주 미만으로 반올림: `qty=0`, `sizing_reason="below_min_lot"`, 주문 스킵 (REQ-046-A4) — 1주 강제 금지
- confidence 필드 부재(None): 기본 경로 (REQ-046-B3)
- SELL 신호: 사이징은 BUY 위주, sell은 SPEC-042 clamp_sell_to_confirmed 우선(이중 사이징 금지)
- cash=0 / 캡 이미 소진: 제안 0 또는 check_pre_order 거부, 기존 거부 동작과 동일

## Quality Gate / Definition of Done (TRUST 5)

- [ ] **Tested**: 85%+ 커버리지. vol-targeting 수식(역비례·budget 스케일·반올림·fallback·zero-lot) 전부 단위 테스트. confidence non-increasing property 테스트. `llm_direct` 회귀 테스트(현 동작 보존). reproduction-first(프로젝트 룰).
- [ ] **Readable**: 한국어 주석(code_comments=ko), 사이징 수식에 근거 명시.
- [ ] **Unified**: ruff/포맷 통과, 기존 strategy 모듈 컨벤션 준수.
- [ ] **Secured**: 외부 입력(ATR/price) 검증, 0/음수/NaN 방어.
- [ ] **Trackable**: @MX 태그(pure sizing fn = ANCHOR 후보, seam = NOTE), conventional commit + SPEC-046 참조.
- [ ] **검증 게이트**: redeploy 후 `sizing_mode=llm_direct`에서 현 동작 무변화(1차) → paper에서 deterministic 토글 A/B(2차) → SPEC-044 OOS 기대값 관측(3차, P3 게이트 입력).
- [ ] **정직성**: 본 SPEC이 수익을 보장하지 않으며 사이징 측정가능성·anti-predictive confidence 제거만 달성함을 PR/리포트에 명시.

## 운영자 결정 대기 (DoD 전 해소 필요)
- 변동성 예산(연환산 vol budget) 값
- ATR lookback 윈도(14일 재사용 vs 별도)
- vol_unavailable fallback 보수 분율
- SPEC-042 라이브 컷오버 순서(전/후)
- confidence-damp 허용 여부(기본 무시)
