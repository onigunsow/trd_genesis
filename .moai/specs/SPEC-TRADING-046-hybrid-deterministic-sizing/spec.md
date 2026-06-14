---
id: SPEC-TRADING-046
version: 0.1.0
status: draft
created: 2026-06-14
updated: 2026-06-14
author: onigunsow
priority: high
issue_number: null
---

# SPEC-TRADING-046 — Hybrid Deterministic Position Sizing (LLM 후보 유지 · 사이징만 결정화)

## HISTORY

- v0.1.0 (2026-06-14): 초안. ADR-HYBRID-LLM-SIGNAL-001 Option C(점진 하이브리드: 사이징만 먼저)를 P1 SPEC으로 구체화. SPEC-044 측정 인프라가 검증 전제조건. SPEC-044 cost scorecard(2026-06-14, 8 paper round-trips)에서 LLM confidence ↔ P&L Spearman −0.455(반상관) 발견을 [HARD] 설계 제약으로 반영.

---

## 1. Background (배경 · WHY)

### 1.1 진단

ADR-HYBRID-LLM-SIGNAL-001(2026-06-14, Proposed)이 이 시스템의 가장 약한 아키텍처를 지목했다: **LLM 페르소나가 포지션 사이즈(`qty`)를 직접 "감"으로 정하는 구조**. 포지션 사이즈는 수익 변동성에 가장 크게 기여하는 레버인데, 현재는 비결정적·메모리 편향 LLM이 산출하고 `risk/limits.py`는 이를 사후에 자르는 상한(CAP)일 뿐 사이즈를 *산출*하지 않는다.

### 1.2 결정적 증거 (2026-06-14)

오늘 SPEC-044 원가보정 스코어카드를 라이브 거래 이력(paper round-trip 8건)에 처음 돌린 결과:

- net expectancy = **−14,840원/trade**
- alpha = **−11.03%p vs KOSPI buy-and-hold**
- **LLM confidence ↔ 실현 P&L 상관 = Spearman −0.455 (반상관)**

표본은 8건(<30, 통계적으로 유의하지 않음)이지만, **부호가 FINSABER(KDD 2026) 증거와 일치**한다 — LLM의 확신(conviction)은 수익을 예측하지 못하며, 오히려 역방향이다.

### 1.3 본 SPEC의 위치

본 SPEC은 ADR의 **Option C = P1**이다. LLM은 **후보 제안(ticker + side)을 유지**하되, **사이징만 결정적 모듈로 이전**한다. 완전 신호-전용 리팩터(Option B = P2/P3)는 이후로 연기하며, P3는 SPEC-044가 룰 레이어의 OOS 원가보정 기대값이 양수임을 확인한 뒤에만 진행한다(ADR §4 P3 HARD 게이트).

### 1.4 정직성 선언

**하이브리드 사이징은 수익을 보장하지 않는다.** 보장하는 것은 단 하나 — 전략에서 가장 큰 비백테스트 레버(포지션 사이즈)를 **측정 가능(backtestable)** 하게 만들고, **반예측적(anti-predictive) confidence 신호에 대한 의존을 제거**하는 것이다. 룰 레이어의 OOS 기대값이 음수로 나온다면 그것은 실패가 아니라 발견이며, 실자본이 아니라 백테스트에서 그것을 배우는 것이 본 SPEC의 핵심 가치다.

---

## 2. Goal (목표 · WHAT)

후보(ticker, side, signal)와 포트폴리오 상태(현금, 보유, ATR/변동성)를 입력받아 **변동성 타기팅(volatility targeting)** 으로 `qty`를 산출하고 기존 risk caps로 경계 짓는 **결정적 사이징 모듈**(`strategy/sizing/`)을 신설한다. `sizing_mode` feature flag로 가역적으로 토글하며, 사이징 파라미터를 **단일 외부 진실원천**으로 외부화하여 향후 인간 승인 피드백 루프가 코드 수정 없이 튜닝할 수 있게 한다. 사이징 규칙은 SPEC-044 walk-forward 하니스로 OOS 백테스트 가능해야 한다.

---

## 3. Requirements (EARS)

### REQ-046-A — 결정적 사이징 모듈 (변동성 타기팅)

- **REQ-046-A1 (Ubiquitous)**: The sizing module **shall** compute `qty` from a candidate (ticker, side, signal) and portfolio state (cash, holdings, per-instrument ATR/volatility) using **volatility targeting** — position notional sized **inversely** to instrument volatility toward a configured annualized volatility budget.
- **REQ-046-A2 (Ubiquitous)**: The sizing module **shall** be a **pure function** of its explicit inputs and externally-configured parameters (REQ-046-C) — no hidden global state, no LLM call, no network I/O — so the SPEC-044 walk-forward harness can replay it deterministically.
- **REQ-046-A3 (State-Driven)**: **While** per-instrument ATR/volatility is **unavailable** (fewer than `MIN_DAYS_FOR_ATR` days, REQ-VOL-04-6), the sizing module **shall** fall back to a configured conservative fixed notional fraction (never larger than the volatility-targeted size would be at the budget vol) and surface a `sizing_reason = "vol_unavailable"`.
- **REQ-046-A4 (Event-Driven)**: **When** the computed pre-cap `qty` rounds to **0 shares** (notional below one share at the reference price), the module **shall** return `qty = 0` and `sizing_reason = "below_min_lot"` so the orchestrator skips the order rather than forcing a 1-share floor.
- **REQ-046-A5 (Ubiquitous)**: The sizing module **shall** reuse existing assets — `get_dynamic_thresholds` / `compute_atr` (ATR cache) for volatility, `estimate_fee` for cost-aware notional, the configured `RISK_*` caps as the ceiling (REQ-046-D) — and **shall not** duplicate or re-implement them.

### REQ-046-B — confidence는 사이즈를 키우지 않는다 [HARD]

- **REQ-046-B1 (Unwanted)**: The sizing module **shall not** increase position size as a function of the LLM's `confidence` / `conviction` field. (Evidence: SPEC-044 scorecard 2026-06-14, confidence ↔ P&L Spearman −0.455 — anti-predictive.)
- **REQ-046-B2 (Optional · default OFF)**: **Where** a future evidence review justifies it, confidence **may** be used **only** as a downward-only conservative damp (size can shrink, never grow), gated behind an explicitly-named config flag that defaults to **ignore confidence entirely**.
- **REQ-046-B3 (Ubiquitous)**: The sizing module **shall** treat `confidence == None` (field absent) identically to the default path — confidence is never required for sizing.

### REQ-046-C — 단일 외부 파라미터 진실원천

- **REQ-046-C1 (Ubiquitous)**: All sizing parameters — annualized volatility budget, ATR lookback window, fallback fixed-fraction, optional confidence-damp settings — **shall** live in a **single external source of truth** (config constants / env-overridable, mirroring the SPEC-044 cost single-source pattern in `config.py`), not hard-coded inside the sizing logic.
- **REQ-046-C2 (Ubiquitous)**: The parameter set **shall** be expressible as a single typed structure (e.g. `SizingParams`) that the SPEC-044 walk-forward harness can sweep over (REQ-046-E), and that a future human-approved feedback loop can propose/tune **without code edits**.

### REQ-046-D — risk/limits.py 통합 (사이징은 제안, limits는 하드 천장)

- **REQ-046-D1 (State-Driven)**: **While** `sizing_mode = deterministic`, the orchestrator **shall** size via the sizing module **and then** pass the proposed `qty` through `check_pre_order` unchanged — the sizing module **proposes**, `risk/limits.py` remains the hard ceiling.
- **REQ-046-D2 (Unwanted)**: The sizing module **shall not** re-implement, relax, or double-apply the single-order (10%) / per-ticker (20%) / total-invested (80%) caps; it **may** read them to bound its own proposal, but `check_pre_order` is the single authority that rejects an over-cap order (no double-capping bug).
- **REQ-046-D3 (Ubiquitous)**: When the sizing module's own proposal already respects the caps, passing through `check_pre_order` **shall** be a no-op (the proposal is the binding qty); when it does not, `check_pre_order` rejection behavior is unchanged from today.

### REQ-046-E — feature flag · orchestrator seam · 측정 연결

- **REQ-046-E1 (Ubiquitous)**: A `sizing_mode` config (`llm_direct` | `deterministic`) **shall** gate the behavior; the default **shall** remain `llm_direct` until the deterministic path is validated.
- **REQ-046-E2 (State-Driven)**: **While** `sizing_mode = deterministic`, `_execute_signal` (orchestrator, ~L893) **shall** compute `qty` via the sizing module instead of consuming `sig['qty']` directly; **while** `sizing_mode = llm_direct`, behavior **shall** be byte-for-byte the current path (LLM `qty` used).
- **REQ-046-E3 (Event-Driven)**: **When** `sizing_mode = deterministic` overrides the LLM `qty`, the orchestrator **shall** persist both the LLM-advisory `qty` and the deterministic `qty` (+ `sizing_reason`) for A/B comparison and audit.
- **REQ-046-E4 (Ubiquitous)**: The `SizingParams` structure **shall** be wired so the SPEC-044 `walk_forward.run_walk_forward` harness can evaluate sizing-parameter grids with train/test split (OOS), exactly as `exit_sweep` evaluates exit-parameter grids today.

---

## 4. Exclusions (What NOT to Build)

- 진입 **타이밍**의 결정화: LLM이 "언제/무엇을 살지"를 계속 판단한다. 본 SPEC은 사이징만 다룬다(ADR P2/P3는 별도, SPEC-044 OOS 양수 게이트 통과 후).
- LLM 페르소나 프롬프트의 스코어-전용 전환(Option B): 본 SPEC 범위 밖.
- `config.py`의 cost 상수(`*_FEE_*`, `*_TX_TAX`) 변경: SPEC-044가 단일 진실원천으로 통합 중이므로 본 SPEC은 이를 **참조만** 하고 손대지 않는다.
- `backtest/`, `edge/`, `kis/` 모듈 변경: 사이징 통합에 strictly 필요한 최소 연결 외에는 손대지 않는다(walk_forward는 새 param 구조를 *받는* 쪽으로만 확장, exit_sweep 의미론 보존).
- `risk/limits.py`의 캡 로직 변경: 사이징은 캡을 *읽기*만 하고, 캡 판정/거부는 기존 `check_pre_order`에 둔다.
- fractional-Kelly의 기본 채택: 옵션으로만 고려(§5 ADR), 기본은 vol-targeting.
- 출구(EXIT) 로직 변경: 이미 결정적(워치독 + ATR 임계). 본 SPEC 범위 밖.
- confidence를 사이즈 확대에 사용: [HARD] 금지(REQ-046-B1).

---

## 5. ADR — 사이징 방법 선택 (진짜 분기점)

| 방법 | 장점 | 단점 | 판정 |
|---|---|---|---|
| **변동성 타기팅 (vol-targeting)** ★권고 | 단순·강건. 입력=ATR%만. 짧은 KOSPI 단일레짐 이력에서도 과적합 표면 작음. SPEC-044가 바로 스윕 가능. | 기대수익(μ)을 무시 — 순수 리스크 패리티. 변동성 예산은 운영자 결정 필요(§6 OQ). | **채택** |
| fractional-Kelly | 이론적 최적 성장. μ를 활용. | full Kelly는 파라미터 불확실성하에서 과도 공격적. μ 추정이 곧 비백테스트 LLM 알파에 의존 → 측정가능성 훼손. fraction 자체가 또 하나의 과적합 손잡이. | 옵션으로만(REQ-046-C 파라미터에 자리 마련), 기본 미사용 |
| conviction-weighted (confidence로 사이징) | LLM 정성 신호 보존. | **증거가 직접 배제**: confidence ↔ P&L Spearman −0.455(반예측적). 키우면 수익 악화 방향. | **배제 (REQ-046-B1)** |

**권고 = 변동성 타기팅.** 근거: (1) confidence가 반예측적이라 conviction-weighting은 증거상 배제. (2) full Kelly는 파라미터 불확실성하 과공격적이고 μ 추정이 비백테스트 LLM 알파에 의존해 본 SPEC의 측정가능성 목표를 훼손. (3) vol-targeting은 입력이 ATR% 하나라 손잡이가 적어 짧은 단일레짐 이력에서 과적합 위험이 가장 낮고, SPEC-044 하니스가 즉시 OOS 평가 가능.

**필요조건이지 충분조건 아님**: 본 SPEC은 사이징을 측정 가능하게 만들 뿐, 진입 타이밍은 SPEC-044가 룰 레이어 OOS 기대값 양수를 확인하기 전까지 LLM에 남는다. "절반의 측정"이 잘못된 안도감을 줄 위험(ADR §3.2)은 §6 OQ와 P3 게이트로 흡수한다.

---

## 6. Open Questions (운영자 결정 필요)

1. **변동성 예산(annualized vol budget)**: 포지션당 목표 연환산 변동성을 얼마로? (예: 포지션당 15~25%, 또는 포트폴리오 합산 타겟.) — 사이즈 절대크기를 직접 결정. **운영자 결정 필요.**
2. **ATR lookback window**: 사이징용 변동성 추정에 기존 14일 ATR(REQ-VOL-04-2)을 그대로 쓸지, 별도 윈도를 둘지. **운영자 결정 필요.**
3. **vol_unavailable fallback 보수 분율**: ATR 부재 시 고정 분율을 얼마로(REQ-046-A3)? caps보다 보수적이어야 함.
4. **SPEC-042 라이브 컷오버 순서**: deterministic 사이징을 실자본 투입 **전** 검증 인프라부터 켤지, **후** 현 구조로 실데이터 확보 후 켤지(ADR §5 Q5). 본 SPEC 기본은 flag default `llm_direct` 유지 → paper에서 A/B 후 결정.
5. **confidence-damp 허용 여부**: REQ-046-B2의 하향-전용 damp를 향후 열지, 영구히 confidence를 무시할지. 기본은 **무시**.

## 7. Assumptions (명시적 가정)

1. SPEC-044 walk-forward 하니스가 임의 파라미터 세트(사이징 포함)를 train/test 분리로 평가 가능 — ADR Assumption 1과 동일, 044 구현 완료 전까지 가정.
2. ATR 캐시(`get_dynamic_thresholds` / `compute_atr`)와 `check_pre_order` 캡이 사이징 모듈이 재사용 가능한 안정 형태 — 코드 확인됨(2026-06-14).
3. `decision.py`에 `conviction`/`confidence` advisory 보존은 후방호환(추가/유지 필드).
4. FINSABER 불장 패배 결론이 KOSPI에 일반화 — **미검증 가정**, 검증은 SPEC-044에 위임.
