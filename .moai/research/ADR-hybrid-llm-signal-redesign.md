# ADR — Hybrid LLM-Signal Redesign (LLM 직접 결정 → 신호/피처 생성기 + 결정적 룰 레이어)

| 항목 | 값 |
|---|---|
| ADR ID | ADR-HYBRID-LLM-SIGNAL-001 |
| Status | **Proposed** (운영자 의사결정 대기) |
| Date | 2026-06-14 |
| Author | onigunsow |
| Domain | TRADING |
| 분류 | 전략/방향 결정 문서 (NOT a SPEC, NOT code) |
| 관련 SPEC | SPEC-TRADING-042(broker-truth ledger), SPEC-TRADING-044(measurement infra), SPEC-TRADING-045(live execution safety) |
| 후속 트랙 | 본 ADR 채택 시 P1 = 결정적 사이징 SPEC 시리즈(미확정) |

> **이 문서의 정직성 선언 (먼저 읽으세요)**
> 하이브리드 전환은 **수익을 보장하지 않습니다.** 그것이 보장하는 것은 단 두 가지입니다 — (1) 전략의 핵심 레버를 **측정 가능(backtestable)** 하게 만들고, (2) 2026 학계 증거(FINSABER, KDD 2026)와 **정렬(evidence-aligned)** 시키는 것. 만약 룰 레이어의 OOS(out-of-sample) 원가보정 기대값이 음수로 나온다면, 그것은 **실패가 아니라 발견**입니다 — 실자본이 아니라 백테스트에서 그 사실을 배우는 것이 본 방향의 핵심 가치입니다.

---

## 1. Context & Problem (맥락과 문제)

### 1.1 진단의 출처
2026 심층 감사 + 학계 리서치(FINSABER, KDD 2026)가 이 시스템의 핵심 아키텍처 —
**LLM 페르소나가 매수/매도를 직접(DIRECTLY) 결정하는 구성** — 이 문헌상 **증거가 가장 약한 배치**임을 지적했습니다.

- **FINSABER**: LLM 트레이딩 에이전트가 **불장(bull market)에서 buy-and-hold에 패배**한다고 보고. 과거의 "좋았던" 결과는 좁은 타임프레임 / look-ahead / 생존자 편향으로 부풀려진 것.
- **현 국면이 정확히 그 위험 구간**: 2025 KOSPI +76%. LLM 에이전트가 가장 불리한 레짐.
- **알파 원천이 백테스트 불가**: 알파 = LLM 페르소나의 비결정적·메모리 의존 결정. SPEC-044 spec.md가 명시 — *"알파 원천(LLM 페르소나 결정)은 비결정적·기억편향이라 근본적으로 백테스트 불가"*. 즉 **자기 전략의 원가보정 기대값이 양수인지 검증할 능력이 현재 0**.

### 1.2 코드 레벨에서 확인한 현실 (read-only 감사)
문헌의 추상적 진단을 이 저장소의 실제 코드로 내려 확인했습니다:

| 레이어 | 파일 | 현재 동작 | 백테스트 가능성 |
|---|---|---|---|
| 후보 생성 | `personas/micro.py` | LLM이 매수/매도 후보 출력 | 불가 (비결정적) |
| **결정 + 사이징** | `personas/decision.py` | **LLM(Sonnet)이 `{ticker, side, qty, rationale, confidence}` 출력 — `qty`(포지션 사이즈)를 직접 선택** | **불가 (핵심 문제)** |
| 검증 | `personas/risk.py` | LLM이 APPROVE/HOLD/REJECT 판정 | 불가 |
| 사이징 조정(옵션) | `personas/portfolio.py` | LLM이 holdings≥5일 때 사이즈 조정 | 불가 |
| 주문 실행 | `personas/orchestrator.py` `_execute_signal` (L893+) | **`sig['qty']`을 그대로 주문에 사용** | — |
| 사후 상한 | `risk/limits.py` `check_pre_order` | 단건10%/종목20%/총80%/일일카운트/실현손실 — **사이징 엔진이 아니라 CAP** | 결정적 (이미) |
| 이벤트 게이트 | `strategy/car/filter.py` | event-CAR PASS/BLOCK — **유일한 기존 정량 게이트**, 단 Decision 이전 필터 | 결정적 (이미) |
| **출구(EXIT)** | `watchers/position_watchdog.py` + `strategy/volatility/thresholds.py` | **ATR 손절 전량 / 익절 절반 — 이미 룰 기반** | 결정적 (이미) |

**핵심 결론**:
- **출구는 이미 결정적**(워치독 + ATR 임계). 따라서 하이브리드 전환의 표면은 **진입(entry)과 사이징(sizing)** 에 집중됨.
- 그중에서도 **포지션 사이즈(`qty`)는 수익 변동성에 가장 크게 기여하는 레버인데, 현재 비결정적·메모리 편향 LLM이 "감(vibes)"으로 정하고 있음**. `risk/limits.py`는 이를 사후에 자르는 상한일 뿐, 사이즈를 *산출*하지 않음.

### 1.3 문헌이 권고하는 패턴
> LLM을 **신호/피처 생성기**(뉴스 감성, 레짐 분류, 관련성 스코어)로 쓰고, 그 출력을 **결정적 룰 레이어**(포지션 사이징·실행)에 먹인다.

이 패턴은 부수적으로 **파이프라인을 백테스트 가능하게** 만들며, 이는 **SPEC-044 측정 인프라(현재 구현 중)와 직접 시너지**를 냅니다 — 044는 "룰을 측정할 수 있는가"를 깔고, 본 ADR은 "그럼 어떤 룰인가"를 정의합니다.

---

## 2. Options Considered (검토한 선택지)

3개 옵션을 동일 축(장점 / 단점 / 백테스트 가능성 / 이주 비용 / 위험)으로 평가합니다.

### Option A — 현상유지 (LLM이 직접 결정)
LLM 페르소나가 `ticker + side + qty`를 모두 직접 정하는 현재 구조를 유지.

- **장점**: 이주 비용 0. LLM의 정성적·홀리스틱 판단을 100% 보존. 이미 배포·검증됨.
- **단점**: 알파 원천이 백테스트 불가(SPEC-044가 명시). FINSABER 증거상 현 불장에서 buy-and-hold에 패배 예상. 최고 변동성 레버(사이즈)를 비결정적·메모리 편향 프로세스가 설정. 실제 결정의 원가보정 기대값을 영원히 측정 불가.
- **백테스트 가능성**: ~0 (출구만 가능).
- **이주 비용**: 없음.
- **위험**: 검증 불가 상태로 실자본(SPEC-042 임박)에 진입. "측정 못 하는 것은 개선 못 한다."

### Option B — 완전 하이브리드 (LLM은 0–1 스코어만)
LLM은 **스칼라 신호만** 출력(감성 0–1, 레짐 0–1, 관련성/확신 0–1). 결정적 레이어가
후보 랭킹 → **vol-targeting / fractional-Kelly 사이징** → **룰 기반 진입 임계 + 룰 기반 출구**를 담당.

- **장점**: 진입+사이즈+출구 전 구간이 결정적 → **end-to-end 백테스트 가능**(LLM 스코어를 피처로 고정/리플레이). 문헌상 **최선 증거 배치**. SPEC-044 하니스가 출구뿐 아니라 **전략 전체**를 측정.
- **단점**: 대규모 재작성 — 4개 페르소나 프롬프트를 스코어 출력으로 전환, 랭킹·사이징·진입임계 모듈 신설, 재튜닝, 재검증. LLM의 홀리스틱 정성 사이징 판단 포기. **짧은 단일 레짐 KOSPI 이력에 룰 메타 과적합 위험**. 작동 중인 오케스트레이션 상당 부분 폐기.
- **백테스트 가능성**: 높음.
- **이주 비용**: 높음 (~다수 SPEC, 빅뱅에 가까움).
- **위험**: 라이브 직전(SPEC-042)에 빅뱅 재작성을 감행하는 타이밍 위험.

### Option C — 점진 하이브리드 (사이징만 먼저) ★권고
LLM은 **후보 제안을 유지**(ticker + side + conviction)하되, **사이징(`qty`)만 결정적 모듈로 이전**.
완전한 신호-전용 리팩터(B)는 **이후로 연기**.

- **최소 실행 변경**: LLM의 `qty`를 무시하고 `qty = deterministic_size(conviction, ATR-vol-target, risk caps)`로 계산. LLM은 "어떤 종목을 살지"의 정성 판단을 유지.
- **장점**: 표면적 작음. 기존 자산 재사용(ATR 캐시 `get_dynamic_thresholds`, `estimate_fee`, `check_pre_order` 캡). **최고 변동성 레버(사이즈)를 즉시 결정적으로 전환 → SPEC-044가 바로 측정 가능**. LLM 정성 진입 판단 보존. feature-flag로 가역(reversible).
- **단점**: 진입 **타이밍**은 여전히 LLM(여전히 부분적으로 백테스트 불가). 2단계 이주. conviction→size 매핑을 새로 정의해야 함.
- **백테스트 가능성**: 사이징+출구 = 중상(medium-high). 진입은 미해결.
- **이주 비용**: 중간 (모듈 1개 + 플래그 + 페르소나 출력 1필드 변경 = P1만으로 의미 있는 가치).
- **위험**: 낮음. 가역적이고 표면이 좁아 라이브 직전에도 안전.

---

## 3. Recommendation (권고) — Option C를 P1으로

**Option C(점진 하이브리드: 사이징만 먼저)를 채택하되, 명시적으로 Option B로 가는 1단계로 프레이밍합니다.**

### 3.1 운영자 현실에 묶은 근거
- **단독 운영자**: 빅뱅 재작성(B)을 한 번에 검증·운용할 여력이 제한적. C는 표면이 좁아 단독 운영자가 통제 가능.
- **실자본 임박 (SPEC-042)**: 라이브 직전에 빅뱅은 타이밍 위험. C는 가역적 feature-flag로 안전하게 시작.
- **CLI-cost-0 제약**: C는 LLM이 여전히 후보를 내되 한 필드(qty)만 덜 내므로 비용 구조를 깨지 않음. B는 스코어만 내 더 싸지만 전면 프롬프트 재작성 필요.
- **FINSABER 증거**: "사이즈를 감으로 정하는 것"이 가장 약한 고리. C는 **바로 그 가장 약한 고리 하나**를 결정적·측정가능으로 전환 — 증거 정렬의 최대 레버 대비 최소 위험.

### 3.2 인지 편향 점검 (왜 C가 틀릴 수 있는가)
- **앵커링**: "사이즈가 핵심 레버"라는 전제에 과도 고정됐을 수 있음 — 실제 알파 손실의 더 큰 원천이 *진입 타이밍*이라면 C는 미미한 개선에 그침.
- **확증 편향**: FINSABER가 우리 가설을 지지하므로 과대 채택했을 위험. FINSABER는 미국 시장 중심 — KOSPI 일반화는 미검증.
- **C가 실패하는 시나리오**: (1) conviction→size 매핑 자체를 짧은 KOSPI 이력에 과적합. (2) 진입이 여전히 LLM이라 사이징만 룰화해도 OOS 기대값이 여전히 음수 → "절반의 측정"이 잘못된 안도감을 줌. (3) 결정적 사이즈가 LLM의 (검증 안 됐지만 실재했을 수도 있는) 정성 사이징 알파를 제거.

→ 이 위험들은 §5 운영자 질문과 §4 P3 게이트(OOS 양수 확인 전 완전 전환 금지)로 흡수합니다.

---

## 4. Migration Path (마이그레이션 경로) — 미확정 미래 SPEC 시리즈 개요

> 아래는 **확정 SPEC이 아니라 방향 개요**입니다. 각 단계는 reproduction-first TDD(프로젝트 룰)로 별도 SPEC화합니다. look-ahead 부재는 테스트된 불변식.

### P1 — 결정적 사이징 모듈 (본 ADR 채택 시 첫 SPEC)
- **신설**: `strategy/sizing/` (예: `vol_target.py`) — `qty = f(conviction_score, ATR%, account_equity, vol_target, risk_caps)` 산출.
- **페르소나 출력**: `decision.py`가 `conviction`(0–1)을 명시 출력. `qty`는 플래그 뒤에서 **advisory/무시**.
- **플래그**: `sizing_mode: llm | deterministic` (가역, 점진 롤아웃).
- **연결점**: `orchestrator._execute_signal`이 `sizing_mode=deterministic`일 때 `sig['qty']` 대신 사이징 모듈 호출. `get_dynamic_thresholds`(ATR 캐시) / `estimate_fee` / `check_pre_order` 캡 재사용.
- **측정**: SPEC-044 walk-forward 하니스에 **사이징 룰 파라미터 세트**로 투입 → train/test 분리로 OOS 기대값 평가.

### P2 — 스칼라 스코어 출력 (피처화)
- `micro.py` / `macro.py`가 스칼라 스코어 추가 출력(레짐 0–1, 뉴스 관련성 0–1)을 **피처로 영속화**.
- 결정적 **후보 랭킹**이 이 스코어 사용. 단, 유니버스 선택은 여전히 LLM.

### P3 — 완전 B (진입 임계의 결정화)
- LLM의 진입 go/no-go를 LLM 스코어에 대한 **결정적 임계**로 대체 → LLM은 **순수 피처 생성기**.
- **[HARD 게이트]**: **SPEC-044가 룰 레이어의 OOS 원가보정 기대값이 양수임을 확인한 뒤에만** 진행. 음수면 P3 보류 — 그것이 정직한 정지 조건.

---

## 5. Risks & Open Questions (운영자 미해결 질문 5건)

본 ADR은 **결정 전 단계**입니다. 아래 5개 질문에 대한 운영자 답이 P1 SPEC의 범위를 확정합니다.

1. **정성 사이징 포기 의향**: 포지션 사이즈를 LLM의 홀리스틱 판단 대신 **공식**에 맡길 의향이 있는가? (이것이 본 결정의 핵심 철학적 거래 — LLM이 "이건 확신이 크니 크게"라고 했던 정성 신호를 conviction 스칼라로 좁히는 것.)
2. **메타 과적합 수용**: KOSPI 이력은 짧고 단일 불장 레짐. 여기에 사이징 룰을 튜닝하면 과적합 위험. **walk-forward OOS 양수를 채택 게이트로 수용**하는가? (in-sample 성과는 헤드라인으로 인정하지 않음.)
3. **event-CAR 필터 처리**: 기존 `strategy/car/filter.py`(이미 결정적·시너지)를 **그대로 유지**할지, 아니면 P2의 새 스코어링 레이어로 **흡수**할지?
4. **CLI-cost 페르소나 구성**: 스코어만 내면 더 싸지만 여전히 LLM 호출. **4개 페르소나 전부 유지** vs 일부 통합? (CLI-cost-0 제약과 직접 충돌하는 지점.)
5. **SPEC-042 라이브 컷오버 순서**: C(P1)를 실자본 투입 **전**에 넣을지 **후**에 넣을지? (전: 검증 인프라부터 갖추고 진입 / 후: 현 구조로 먼저 실거래 데이터 확보 후 전환.)

### 추가 정직성 메모
- 하이브리드는 측정가능성·증거정렬이지 수익 보장이 아님(§상단 재확인).
- 이주 비용 정직 산정: 전체 ~3개 SPEC. **P1 단독**으로도 의미 있음(모듈 1개 + 플래그 + 페르소나 출력 1필드 = 중간 규모). P2/P3는 P1의 측정 결과를 보고 진행.

---

## 6. Relationship to SPEC-044 & SPEC-045 (관계)

세 트랙은 **상호 보완**이며 의존 순서가 있습니다:

| 트랙 | 질문 | 역할 | 상태 |
|---|---|---|---|
| **SPEC-044** (measurement infra) | "룰을 **측정할 수 있는가**?" | walk-forward 하니스 + KOSPI buy-and-hold 벤치마크 + 원가보정 기대값 스코어카드. **본 ADR의 검증 전제조건.** | 구현 중 |
| **본 ADR** (hybrid redesign) | "그럼 **어떤 룰**인가?" | LLM을 신호 생성기로, 사이징/진입을 결정적 룰로. **044가 가능하게 만든 P1 구조 트랙.** | Proposed |
| **SPEC-045** (live execution safety) | "**안전하게 실행**되는가?" | 실거래 실행 안전. 본 ADR과 직교(orthogonal)하되 라이브 컷오버 순서(§5 Q5)에서 교차. | 작성됨(저장소 존재) |

**포지셔닝**: 본 ADR은 **P1 구조 트랙**입니다. SPEC-044가 "측정 가능성"을 깔아주기 때문에 본 ADR의 룰 레이어가 비로소 **검증 가능**해집니다. 044 없이는 C/B의 OOS 기대값을 확인할 수 없으므로, **044가 본 방향의 필요조건**입니다.

---

## 7. 정정 사항 (Corrections — 정직성)

- **SPEC-045**: 본 문서 작성 시점 기준 `.moai/specs/SPEC-TRADING-045-live-execution-safety/`로 **저장소에 존재**(확인됨). 실행 안전 트랙으로 §6에 반영.
- **세율 상수**: `config.py`의 `LIVE_FEE_SELL_*` 매도측 세율은 2026 개편(거래세 인하)을 반영해 **이미 갱신**되어 있고(`KOSPI_TX_TAX=0.0005` 등), **SPEC-044가 이를 단일 진실원천(single source of truth)으로 통합 중**입니다. 본 ADR은 이를 미해결 버그로 과장하지 않으며, 사이징/원가보정 계산은 044가 통합하는 그 단일 원천을 참조해야 합니다.

---

## Assumptions (명시적 가정)

1. SPEC-044 walk-forward 하니스가 임의 룰 파라미터 세트(사이징 포함)를 train/test 분리로 평가할 수 있다 — 044 spec.md REQ-044-A 기준 타당하나 구현 완료 전까지는 가정.
2. ATR 캐시(`get_dynamic_thresholds`)와 `check_pre_order` 캡은 사이징 모듈이 재사용 가능한 형태로 안정적이다 — 코드 확인됨(2026-06-14).
3. `decision.py`에 `conviction` 필드를 추가해도 CLI 라우팅/`persona_decisions` 영속화 구조를 깨지 않는다 — 추가 필드이므로 후방호환 가정.
4. FINSABER의 불장 패배 결론이 KOSPS에 일반화된다 — **미검증 가정**(§3.2 확증편향 메모). 본 ADR은 이 가정의 검증 자체를 044에 위임.

---

*This is a strategy/decision document for operator direction. It defines no requirements and changes no code. Implementation begins only after operator answers §5 and a P1 SPEC is created.*
