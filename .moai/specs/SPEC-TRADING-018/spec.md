---
id: SPEC-TRADING-018
version: 0.1.0
status: draft
created: 2026-05-11
updated: 2026-05-11
author: onigunsow
priority: critical
issue_number: 0
domain: TRADING
title: "Micro persona blocked-ticker awareness + dynamic watchlist"
related_specs:
  - SPEC-TRADING-016
  - SPEC-TRADING-014
  - SPEC-TRADING-013
  - SPEC-TRADING-009
---

# SPEC-TRADING-018 -- Micro persona blocked-ticker awareness + dynamic watchlist

## HISTORY

| Date | Version | Changes | Author |
|---|---|---|---|
| 2026-05-11 | 0.1.0 | Initial draft -- 5 EARS requirements, SPEC-016 Phase 1 live verification 중 발견된 zero-trade 핫픽스 | onigunsow |

---

## Scope Summary

본 SPEC 은 **SPEC-016 Phase 1 (commit `9aeebb7`, 2026-05-10 21:38 KST redeploy)** 의 라이브 검증일(2026-05-11)에 발견된 **zero-trade 결함**을 해결한다. Phase 1 의 인프라/CLI/Jinja 정합성 패치는 모두 정상 작동했으나, 5/11 09:30 및 11:00 두 차례의 intraday cycle 이 모두 `signals: []` 를 반환하며 거래가 발생하지 않았다.

근본 원인은 **regime awareness 아키텍처 (SPEC-016 Phase 2)** 와는 독립된 **persona context wiring 결함**으로, 별도 hotfix SPEC 으로 분리한다.

### 근본 원인 (Verified Evidence)

micro persona 가 하드코딩된 5종목 `DEFAULT_WATCHLIST` 에 사실상 잠겨 있으며, 오늘(2026-05-11) 해당 5종목이 거래소 `단기과열` 매매제한 리스트와 **완전히 동일**하다. 따라서 모든 decision cycle 이 빈 후보 리스트로 종료된다.

**파일 레벨 증거**:

- `src/trading/personas/context.py:20` — `DEFAULT_WATCHLIST = ["005930", "000660", "035420", "035720", "373220"]` (삼성전자, SK하이닉스, NAVER, 카카오, LG에너지솔루션 하드코딩)
- `data/blocked_tickers.json` — 위 5개 티커가 정확히 `stat_cls=55` (단기과열) 로 2026-05-11 일자에 등재됨
- `src/trading/personas/orchestrator.py:154-166` — `_build_micro_input` 이 DEFAULT_WATCHLIST + screened_tickers 를 병합하지만 **blocked_tickers 를 필터링하지 않으며**, micro persona 에 blocked_tickers 를 전달하지도 않음
- `src/trading/personas/context.py:272-332` — `assemble_micro_input` 의 반환 dict 에 `blocked_tickers` 필드 자체가 **존재하지 않음**
- `src/trading/personas/prompts/micro.jinja` — 86줄 전체에 "blocked" 키워드 grep 결과 0건. micro persona 가 구조적으로 매매제한을 인식할 수 없음. JSON 예시가 `"005930", "삼성전자"` 로 시작하여 모델 응답이 DEFAULT_WATCHLIST 로 편향됨
- `data/screened_tickers.json` — 06:35 daily_screen 잡이 산출한 20개 신규 후보 (조선·원전·방산·전력·금융 섹터) 가 존재하지만 micro persona 가 DEFAULT_WATCHLIST 우선 편향으로 무시함
- DB 증거 (`persona_runs` 테이블 id 101/102/103) — 오늘의 3회 decision 페르소나 실행 모두 `signals: []` 반환, rationale 에 "마이크로 페르소나 후보 5종목 전부 단기과열 매매제한" 명시

### 재현 쿼리

```sql
SELECT id, ts, persona_name, response_json->>'summary'
FROM persona_runs
WHERE ts::date = CURRENT_DATE
ORDER BY id DESC;
```

오늘 일자의 모든 cycle 에서 동일한 blocked-tickers rationale 이 일관되게 출력됨.

### 본 SPEC 의 위치

- **SPEC-016 Phase 1 (완료)**: redeploy 가드 + Jinja 정합성 + CLI 모드 강제 → 거래 파이프라인 자체는 안정화됨
- **SPEC-016 Phase 2 (미시작)**: regime/risk_appetite 의 DB 구조화 — 본 SPEC 과 독립적인 별도 아키텍처 변경
- **SPEC-016 Phase 3 (미시작)**: 불장 모드 + 후기 사이클 방어
- **SPEC-017 (미시작)**: 실거래 전환 토글
- **SPEC-018 (본 SPEC, P0 Critical)**: micro persona 의 blocked-ticker 인식 + 동적 watchlist — Phase 1 위에 얹는 hotfix, Phase 2 와는 독립

본 SPEC 은 SPEC-016 Phase 2 와 **완전히 직교**한다: regime 아키텍처 변경 없이도 즉시 적용 가능하며, Phase 2 도 본 SPEC 없이 진행 가능하다. 다만 우선순위는 본 SPEC 이 **P0** 으로 즉시 처리 대상이며, Phase 2 는 P1 로 본 SPEC 완료 후 재개한다.

### 비즈니스 임팩트

- 5/11 09:30, 11:00 두 cycle 손실 (paper trading 이므로 실제 손실은 0 이나, 알고리즘 검증 기회 손실)
- Phase 1 게이트 통과의 조건은 "에러 없는 1 cycle 완주" 였고 그 조건은 충족되었으나, **거래 자체가 발생하지 않는 상태는 사용자가 의도한 시스템 동작이 아님**
- 본 SPEC 완료 후에는 단기과열 종목과 무관하게 매일 ≥ 1건의 후보 진입이 가능한 상태로 복귀

---

## Environment

- 기존 SPEC-001 ~ SPEC-017 인프라 (Docker compose, Postgres 16-alpine, Telegram, KIS API)
- 기존 5-persona 시스템 (Macro/Micro/Decision/Risk/Portfolio)
- SPEC-016 Phase 1 의 안정화 패치가 commit `9aeebb7` 로 배포 완료된 상태
- `data/blocked_tickers.json` 은 exchange feed 가 일 단위로 자동 생성 (단기과열 매매제한 종목)
- `data/screened_tickers.json` 은 06:35 daily_screen 잡이 일 단위로 자동 생성 (20개 신규 후보)
- `src/trading/risk/blocked_cache.py` 의 `get_blocked_tickers()` 함수는 `orchestrator.py:39` 에서 이미 import 되어 있음 (직접 호출 가능)
- 신규 코드 없음 — 기존 3개 파일 수정만으로 해결 가능 (orchestrator.py / context.py / micro.jinja)
- 신규 테스트 1개 파일 추가 (`tests/test_micro_persona_blocked.py`)

## Assumptions

- A-1: `data/blocked_tickers.json` 은 exchange feed 가 안정적으로 갱신하며, `blocked_cache.get_blocked_tickers()` 의 반환값은 `List[str]` 형식의 ticker code 리스트이다 (`["005930", "000660", ...]`).
- A-2: `data/screened_tickers.json` 은 06:35 daily_screen 이 매일 ≥ 1건의 후보를 산출한다. 산출이 0건인 날(주말/공휴일 제외)에는 별도 알람 처리 — 본 SPEC 의 fallback 보다 상위 보호 장치이며 본 SPEC 범위 외.
- A-3: micro.jinja 의 `[매매제한 종목]` 블록 추가가 LLM 응답 품질을 저해하지 않는다 (block 길이가 짧고 명확한 instruction 형태로 SPEC-015 단일 턴 패턴 위에서 안전).
- A-4: DEFAULT_WATCHLIST 의 5종목은 향후 다른 날에는 단기과열 리스트에 포함되지 않을 수 있으며, 필터링 로직은 **이전 동작에 영향을 주지 않는다** (blocked_tickers 가 비어 있으면 동작 변경 없음 — backward compatible).
- A-5: 본 SPEC 의 변경은 SPEC-016 Phase 2 (regime DB 구조화) 의 변경과 충돌하지 않으며, Phase 2 가 추후 도입될 때 `_build_micro_input` 시그니처는 호환 가능한 형태로 유지된다.

---

## Goals

- **G-1 (Defense in depth)**: 후보 universe 가 거래 가능한 종목으로만 구성되도록 **3개 레이어**에서 동시 보호 (orchestrator 필터링 + context wiring + prompt awareness).
- **G-2 (Zero behavioral regression)**: blocked_tickers 가 비어 있는 정상 거래일에는 기존 시스템 동작과 **완전히 동일**.
- **G-3 (Reproducibility)**: 본 SPEC 완료 후, 오늘의 시나리오 (DEFAULT_WATCHLIST 5종 전체 차단 + screened 20종 가용) 를 단위 테스트로 영구 재현 가능.
- **G-4 (Backward compatibility for SPEC-016 Phase 2)**: 본 SPEC 으로 변경된 함수 시그니처는 Phase 2 의 regime 캐싱 도입 시 호환 가능.

---

## Requirements

### REQ-018-1: orchestrator 레이어의 blocked ticker 필터링 (Event-Driven)

**When** `_build_micro_input` 이 호출되면, **then** 시스템은 `blocked_tickers` 를 조회하여 `expanded_watchlist` 에서 **이를 제외**해야 한다.

세부:

- (a) **(Event-Driven)** **When** intraday/pre_market cycle 이 `_build_micro_input(today, macro_summary)` 를 호출하면, 함수는 `blocked_cache.get_blocked_tickers()` 를 호출하여 현재 매매제한 리스트를 가져온다.
- (b) **(Ubiquitous)** 시스템은 `DEFAULT_WATCHLIST + screened[:15]` 의 병합 결과에서 `blocked_tickers` 에 포함된 종목을 **모두 제거**해야 한다.
- (c) **(Unwanted)** 시스템은 blocked_tickers 가 비어 있는 정상 거래일의 동작을 변경해서는 **안 된다** (backward compatibility).
- (d) **(Event-Driven)** **When** 필터링 후 `expanded_watchlist` 가 빈 리스트가 되면 (REQ-018-4 의 fallback 트리거), 시스템은 screened_tickers 의 상위 N개를 보강해야 한다.
- (e) blocked_tickers 는 후속 단계 (REQ-018-2, REQ-018-3) 에서도 사용되도록 `assemble_micro_input` 으로 전달된다.

**Files affected**:

- `src/trading/personas/orchestrator.py` — `_build_micro_input` 수정 (라인 154-166 추가 로직)

**Dependencies**: 없음 (독립). REQ-018-2 와 함께 검증된다.

---

### REQ-018-2: context 레이어의 blocked_tickers 데이터 와이어링 (Ubiquitous)

`assemble_micro_input` 함수는 **반환 dict 에 `blocked_tickers` 필드를 포함**해야 한다.

세부:

- (a) **(Ubiquitous)** `assemble_micro_input` 의 시그니처에 `blocked_tickers: list[str]` 키워드 인자가 추가되며, 디폴트 값은 빈 리스트 `[]` 이다.
- (b) **(Ubiquitous)** 함수의 반환 dict 는 기존 7개 키 (`today, macro_summary, universe_snapshot, recent_disclosures, user_watchlist, static_context, static_news, memory`) 에 더해 **`blocked_tickers` 키를 항상 포함**해야 한다.
- (c) **(Unwanted)** blocked_tickers 가 빈 리스트일 때도 키는 존재해야 하며, `None` 으로 처리되어서는 **안 된다**.
- (d) **(Event-Driven)** **When** Jinja 렌더링이 호출되면, `blocked_tickers` 변수가 항상 정의된 상태로 전달되어 `is defined` 가드 없이도 안전하게 사용 가능하다.

**Files affected**:

- `src/trading/personas/context.py` — `assemble_micro_input` 시그니처 변경 (라인 272-332), 반환 dict 에 `blocked_tickers` 키 추가

**Dependencies**: REQ-018-1 가 본 REQ 의 호출 지점을 갱신한다.

---

### REQ-018-3: prompt 레이어의 매매제한 awareness 블록 (State-Driven)

**While** `blocked_tickers` 가 비어 있지 않은 동안, **micro persona 프롬프트는 매매제한 종목 목록과 명시적 제외 지시를 렌더링**해야 한다.

세부:

- (a) **(State-Driven)** **While** `blocked_tickers | length > 0` 이면, `micro.jinja` 는 `[매매제한 종목]` 블록을 렌더링한다.
- (b) **(Ubiquitous)** 블록 내용은 다음 형식을 따른다:
  ```
  ## [매매제한 종목]
  다음 종목은 거래소 단기과열/매매제한 대상이므로 후보에서 반드시 제외할 것:
  - 005930
  - 000660
  - 035420
  ...
  ```
- (c) **(State-Driven)** **While** `blocked_tickers` 가 비어 있으면, 블록 자체가 렌더링되지 않아 프롬프트 토큰을 낭비하지 않는다 (`{% if blocked_tickers %}` 가드).
- (d) **(Unwanted)** JSON 출력 예시는 변경하지 않는다 (DEFAULT_WATCHLIST 의 첫 종목인 `005930` 이 예시에 남아 있어도, 매매제한 블록의 명시적 지시가 우선 적용됨을 LLM 이 따른다고 가정).
- (e) **(Ubiquitous)** 블록은 micro.jinja 의 기존 컨텍스트 (`[유니버스 스냅샷]`, `[공시]` 등) 와 시각적으로 분리되어 단독 섹션으로 배치된다.

**Files affected**:

- `src/trading/personas/prompts/micro.jinja` — `{% if blocked_tickers %} ... {% endif %}` 블록 추가

**Dependencies**: REQ-018-2 의 blocked_tickers 키 전달 선행.

---

### REQ-018-4 (Optional, P1): 빈 universe edge case 의 screened fallback (State-Driven)

**While** DEFAULT_WATCHLIST 의 전 종목이 blocked 이고 `screened_tickers` 가 비어 있지 않을 때, **watchlist 는 상위 screened 종목으로 fallback** 되어야 한다.

세부:

- (a) **(State-Driven)** **While** `expanded_watchlist` (DEFAULT_WATCHLIST + screened[:15] 에서 blocked 제외) 의 길이가 0 이고 `screened_tickers` 가 비어 있지 않으면, 시스템은 `screened_tickers[:10]` 중 blocked 이 아닌 종목을 watchlist 로 채택한다.
- (b) **(Unwanted)** fallback 이 적용된 경우에도 `blocked_tickers` 자체는 여전히 micro persona 에 전달되어, 모델이 일관된 컨텍스트를 받는다.
- (c) **(Ubiquitous)** fallback 활성화는 로그에 `[micro_fallback] DEFAULT_WATCHLIST fully blocked, falling back to screened_tickers[:N]` 형식으로 기록된다.
- (d) **(Unwanted)** screened_tickers 도 비어 있는 극한 케이스에서는 빈 watchlist 를 그대로 전달하고 micro persona 가 `signals: []` 를 반환하도록 둔다 (별도 알람은 본 SPEC 범위 외, A-2 참조).

**Files affected**:

- `src/trading/personas/orchestrator.py` — `_build_micro_input` 의 fallback 분기 추가

**Dependencies**: REQ-018-1 위에 얹는 보강 로직. 본 REQ 는 P1 (선택) 이며, P0 (REQ-018-1 ~ 3) 만으로도 오늘의 시나리오는 해결된다. 단, 본 REQ 를 함께 적용해두면 동일 사고의 재발 방지에 도움.

---

### REQ-018-5: 재현 가능한 acceptance test (Ubiquitous)

시스템은 **오늘(2026-05-11)의 시나리오를 단위 테스트로 영구 재현 가능**해야 한다.

세부:

- (a) **(Ubiquitous)** `tests/test_micro_persona_blocked.py` 를 신규 작성, 다음 3개 테스트 케이스 포함:
  1. `test_build_micro_input_filters_blocked`: DEFAULT_WATCHLIST 의 전 종목을 blocked 으로 fixture 주입 → `expanded_watchlist` 가 5종을 모두 제외하고 screened 종목으로만 구성됨을 assert.
  2. `test_assemble_micro_input_returns_blocked_key`: `assemble_micro_input` 의 반환 dict 에 `blocked_tickers` 키가 존재하고 입력값과 일치함을 assert.
  3. `test_micro_jinja_renders_blocked_block`: blocked_tickers 가 비어 있지 않을 때 렌더링된 프롬프트에 `[매매제한 종목]` 블록과 각 ticker code 가 포함됨을 assert. 비어 있을 때는 블록이 부재함을 assert.
- (b) **(Ubiquitous)** 기존 테스트 (62/62 통과 baseline) 는 본 SPEC 의 변경 후에도 **모두 통과**해야 한다.
- (c) **(Ubiquitous)** 신규 테스트는 mock fixture 를 사용하여 실제 KIS API 나 외부 데이터에 의존하지 않는다.
- (d) **(Ubiquitous)** `.moai/config/sections/quality.yaml` 의 coverage 임계 (85%) 를 유지해야 한다. 본 SPEC 의 변경은 라인 수가 적어 coverage 영향이 무시할 수준이지만, 신규 테스트 3건이 추가되므로 coverage 는 오히려 상승할 것으로 예상.

**Files affected**:

- `tests/test_micro_persona_blocked.py` (신규)
- 가능 시 `tests/test_orchestrator.py` — 기존에 `_build_micro_input` 의 shape 을 assert 하는 케이스가 있다면 갱신 (확인 필요, 영향 최소화)

**Dependencies**: REQ-018-1 ~ 3 의 구현 완료 후 GREEN 단계에서 검증.

---

## Specifications

### S-1: `_build_micro_input` 의 변경 의도 (의사 코드 수준의 가이드, 실 코드는 manager-tdd 가 결정)

기존 구조 (`src/trading/personas/orchestrator.py:154-166`) 는 다음과 같이 확장되는 방향이다:

- 진입 시: `blocked = blocked_cache.get_blocked_tickers()` 호출하여 현재 매매제한 리스트 확보
- DEFAULT_WATCHLIST + screened[:15] 병합 후, `blocked` 에 포함된 종목 필터링
- 필터링 결과가 비어 있고 screened 가 가용하면 (REQ-018-4) screened 상위 N 종으로 보강
- `assemble_micro_input` 호출 시 `blocked_tickers=blocked` 키워드 인자 전달

### S-2: `assemble_micro_input` 의 반환 dict 형식 변경

기존 반환 dict:

```python
{
    "today": ...,
    "macro_summary": ...,
    "universe_snapshot": ...,
    "recent_disclosures": ...,
    "user_watchlist": ...,
    "static_context": ...,
    "static_news": ...,
    "memory": ...,
}
```

변경 후 반환 dict (1개 키 추가):

```python
{
    "today": ...,
    "macro_summary": ...,
    "universe_snapshot": ...,
    "recent_disclosures": ...,
    "user_watchlist": ...,
    "static_context": ...,
    "static_news": ...,
    "memory": ...,
    "blocked_tickers": [],  # 항상 존재, 빈 리스트가 default
}
```

### S-3: `micro.jinja` 의 매매제한 블록 형식

```jinja
{% if blocked_tickers %}
## [매매제한 종목]
다음 종목은 거래소 단기과열/매매제한 대상이므로 후보에서 반드시 제외할 것:
{% for ticker in blocked_tickers %}
- {{ ticker }}
{% endfor %}
{% endif %}
```

### S-4: Acceptance Criteria (Given/When/Then)

**시나리오 1 — 오늘의 사고 재현**:

- **Given** `data/blocked_tickers.json` 이 DEFAULT_WATCHLIST 의 5종을 모두 포함하고, `data/screened_tickers.json` 이 20개의 비-blocked 후보를 포함한다,
- **When** `_build_micro_input(today, macro_summary)` 가 호출된다,
- **Then**:
  - 반환된 `expanded_watchlist` 에 DEFAULT_WATCHLIST 의 5종 중 어느 것도 포함되지 않는다.
  - `expanded_watchlist` 의 길이가 ≥ 10 이며, 모두 screened_tickers 출신이다.
  - `assemble_micro_input` 의 반환 dict 에 `blocked_tickers` 키가 존재하고 값이 5종의 차단 리스트와 일치한다.

**시나리오 2 — 정상 거래일 (zero behavioral regression)**:

- **Given** `data/blocked_tickers.json` 이 비어 있다,
- **When** `_build_micro_input(today, macro_summary)` 가 호출된다,
- **Then**:
  - 반환된 `expanded_watchlist` 는 SPEC-018 이전 동작과 동일 (DEFAULT_WATCHLIST 5종 + screened[:15] 중 중복 제거).
  - `blocked_tickers` 키는 존재하지만 빈 리스트.
  - micro.jinja 렌더링 결과에 `[매매제한 종목]` 블록이 부재.

**시나리오 3 — prompt awareness**:

- **Given** `blocked_tickers = ["005930", "000660"]` 이 micro persona 컨텍스트로 전달된다,
- **When** `micro.jinja` 가 렌더링된다,
- **Then** 렌더링된 문자열에 다음 모두 포함:
  - `[매매제한 종목]` 헤더
  - `005930` 및 `000660` 각각의 ticker code
  - "후보에서 반드시 제외" 류의 명시적 지시 문장

---

## Non-Goals (Out of Scope)

본 SPEC 은 다음 항목을 **명시적으로 다루지 않는다**:

- **SPEC-016 Phase 2 (Regime Awareness)** — `regime` 및 `risk_appetite` 의 DB 캐싱과 분기 로직. 별도 SPEC 으로 처리.
- **SPEC-016 Phase 3 (불장 모드 + 천장 방어)** — 강세장 페르소나 컨텍스트 및 후기 사이클 트리거. 별도 SPEC.
- **SPEC-017 (실거래 토글)** — paper trading 에서 실거래 전환. 별도 SPEC.
- **DEFAULT_WATCHLIST 종목 변경** — 본 SPEC 은 필터링 로직만 다루며, 하드코딩된 5종목 자체의 변경은 다루지 않는다. 5종목은 그대로 두고, 매매제한 시에는 필터링되며, screened_tickers 가 빈자리를 채우는 구조.
- **성능 최적화** — blocked_tickers 의 캐싱 전략, screened_tickers 의 재조회 비용 등. 현재 데이터 크기로는 무시 가능.
- **신규 persona 타입 추가** — 본 SPEC 은 기존 micro persona 의 컨텍스트 와이어링만 다룬다.
- **screened_tickers 가 0건인 날의 별도 알람** — A-2 참조. 본 SPEC 의 fallback 보다 상위 보호 장치이며, 별도 운영 알람 SPEC 으로 분리 가능.

---

## Implementation Hints (manager-tdd 참고용, 본 SPEC 에서는 구현하지 않음)

본 SPEC 은 specification 만 정의하며, 실 코드 작성은 `/moai:2-run SPEC-TRADING-018` 단계의 manager-tdd 에 위임한다. 다음은 manager-tdd 에 전달할 힌트이다 (강제 사항 아님):

- **필터링 패턴**: `[t for t in expanded if t not in blocked_tickers]` 형식의 set/list comprehension 권장. 성능 민감 영역이 아니므로 단순한 패턴 사용.
- **blocked_tickers 소스**: `src/trading/risk/blocked_cache.py` 의 `get_blocked_tickers()` 가 정식 진입점. 이 함수는 `src/trading/personas/orchestrator.py:39` 에서 이미 import 되어 있으므로 추가 import 불필요.
- **Jinja 가드**: `{% if blocked_tickers %}` 만으로 충분 (빈 리스트는 falsy 이므로 별도 `is defined` 가드 불요. assemble_micro_input 이 항상 키를 채우기 때문).
- **테스트 fixture**: DEFAULT_WATCHLIST 전체를 포함하는 fake blocked_cache 를 monkeypatch 로 주입. screened_tickers 도 mock 으로 합성하여 외부 파일 의존 제거.
- **회귀 영향**: 기존 62/62 테스트 중 `_build_micro_input` 의 shape 을 직접 assert 하는 케이스가 있다면, `blocked_tickers` 키 추가에 따른 dict 비교 갱신 필요. 영향 범위 최소화 위해 `assert key in result` 패턴 사용 권장.

---

## Files Expected to Change (구현 단계 참고)

| File | Change Type | Rough LOC | Owner |
|---|---|---|---|
| `src/trading/personas/orchestrator.py` | Modify `_build_micro_input` (filter + fallback) | +15 ~ +25 | manager-tdd |
| `src/trading/personas/context.py` | Extend `assemble_micro_input` signature + return dict | +5 ~ +10 | manager-tdd |
| `src/trading/personas/prompts/micro.jinja` | Add `{% if blocked_tickers %}` block | +8 | manager-tdd |
| `tests/test_micro_persona_blocked.py` | New file with 3 test cases | +60 ~ +100 | manager-tdd |
| `tests/test_orchestrator.py` | Update existing assertions if any | +0 ~ +10 | manager-tdd |

총 변경 LOC 추정: ~100 ~ 150 LOC, 5 파일, 신규 1 파일 / 수정 4 파일.

---

## Constraints

- **C-1**: 본 SPEC 의 변경은 backward compatible 해야 한다. blocked_tickers 가 비어 있을 때의 동작은 SPEC-018 이전과 완전히 동일.
- **C-2**: SPEC-016 Phase 2 의 향후 도입 시 `_build_micro_input` 시그니처는 호환 가능한 형태로 유지해야 한다. Phase 2 가 추가로 요구할 키워드 인자 (예: `regime`, `risk_appetite`) 는 본 SPEC 에서 차단하지 않는다.
- **C-3**: Coverage 임계 85% 유지 (`.moai/config/sections/quality.yaml`).
- **C-4**: 본 SPEC 은 P0 Critical 이므로, `/moai:2-run SPEC-TRADING-018` 의 manager-tdd 가 RED-GREEN-REFACTOR 사이클을 신속히 진행해야 한다. 목표는 5/11 (월) 14:00 KST 이전 redeploy 완료, 14:00 또는 15:00 intraday cycle 에서 ≥ 1건의 후보 진입 검증.
- **C-5**: 본 SPEC 의 모든 변경은 git branch `feat/spec-018-blocked-tickers` 로 격리, PR 단위로 사용자 리뷰.
- **C-6**: 본 SPEC 은 SPEC-016 Phase 1 위에서만 동작 (commit `9aeebb7` 이상 필요). Phase 1 이 롤백되면 본 SPEC 도 함께 재평가.

---

## Risks

| ID | 리스크 | 영향 | 가능성 | 대응 |
|---|---|---|---|---|
| R-1 | LLM 이 매매제한 블록을 무시하고 005930 등을 후보로 반환 | High | Low | 3-layer defense (orchestrator 필터링이 1차 방어선, prompt awareness 는 2차). 1차에서 이미 universe 에서 제거되므로 LLM 이 후보로 낼 수 없음 |
| R-2 | screened_tickers 도 비어 있는 극한 케이스 (daily_screen 잡 실패) | Medium | Low | 본 SPEC 범위 외, A-2 참조. 별도 운영 알람으로 처리 |
| R-3 | DEFAULT_WATCHLIST 의 5종이 blocked 이 아닌 정상 거래일에 screened 의 fresh 후보가 무시되는 기존 편향 | Medium | High | 본 SPEC 으로는 해결 불가 (DEFAULT_WATCHLIST 자체 변경은 non-goal). screened 후보의 fresh 신호를 활용하려면 별도 SPEC 필요 |
| R-4 | blocked_cache.get_blocked_tickers() 의 반환 형식이 List[str] 이 아닌 예외 케이스 | Medium | Low | 단위 테스트에서 형식 검증. 실제 prod 데이터 (`data/blocked_tickers.json`) 의 stat_cls=55 항목으로 검증 |
| R-5 | 기존 테스트 62/62 중 `_build_micro_input` 의 dict shape 을 직접 비교하는 케이스가 깨짐 | Low | Medium | manager-tdd 가 RED 단계에서 사전 식별, GREEN 단계에서 assertion 갱신 |
| R-6 | Phase 2 (regime DB) 와 Phase 1 의 hotfix 가 동시에 진행되어 merge conflict | Low | Low | Phase 2 는 본 SPEC 완료 후 재개. 본 SPEC 의 brevity (5 파일, ~150 LOC) 로 conflict 표면적 최소 |

---

## Rollout Plan

### 단일 Phase — 5/11 (월) 12:00 ~ 14:00 KST

1. (12:00) `feat/spec-018-blocked-tickers` 브랜치 생성
2. (12:00 ~ 13:00) `/moai:2-run SPEC-TRADING-018` 실행 → manager-tdd 가 RED-GREEN-REFACTOR 사이클 진행
   - RED: 3개 신규 테스트 작성, 모두 실패 확인
   - GREEN: orchestrator.py, context.py, micro.jinja 수정으로 통과
   - REFACTOR: 코드 정리 + 기존 62 테스트 통과 확인
3. (13:00 ~ 13:30) Coverage 검증, ruff/black 통과, PR 생성, 사용자 리뷰
4. (13:30 ~ 14:00) `make redeploy` (SPEC-016 Phase 1 의 단일 진입점) 으로 컨테이너 재배포
5. (14:00 또는 15:00) 첫 intraday cycle 에서 ≥ 1건의 후보 진입 검증 (`SELECT ... FROM persona_runs WHERE ts::date = CURRENT_DATE AND persona_name='micro'` 로 universe 크기 ≥ 10 확인)
6. (15:00 이후) `/moai:3-sync SPEC-TRADING-018` 으로 문서 동기화, SPEC 상태를 `completed` 로 변경

### Safety Gates

- **종료 전 게이트 1**: 단위 테스트 65/65 통과 (기존 62 + 신규 3) AND coverage ≥ 85%
- **종료 전 게이트 2**: 사용자가 직접 `make redeploy` runbook (SPEC-016 Phase 1 산출물) 을 한 번 따라 실행해보고 컨테이너 healthcheck 5/5 통과 확인
- **종료 전 게이트 3**: 14:00 또는 15:00 cycle 에서 micro persona 가 ≥ 5종의 candidate 종목 (모두 non-blocked, 가능하면 screened_tickers 출신) 을 반환
- **종료 전 게이트 4**: 동일 cycle 에서 decision persona 가 signals 배열에 ≥ 1건의 진입 후보를 반환 (반드시 ENTRY 일 필요는 없음, HOLD 라도 universe 가 살아있다는 신호)

---

## Open Questions

- **Q-1**: REQ-018-4 (fallback) 를 P0 와 함께 출시할 것인가, 별도 follow-up 으로 분리할 것인가? — 권장: 동시 출시. 5 파일 중 1 파일만 추가 수정이고, 동일 사고의 재발 방지에 직접 기여. 단, 시간 압박 시 P0 만 우선 출시 후 follow-up 가능.
- **Q-2**: micro.jinja 의 JSON 예시에 `005930, 삼성전자` 가 남아 있어 LLM 응답 편향 가능성. 본 SPEC 의 매매제한 블록 추가로 충분한가, JSON 예시 자체를 일반화된 placeholder (`<ticker>, <종목명>`) 로 바꿔야 하는가? — 권장: 본 SPEC 에서는 매매제한 블록만 추가 (variance 최소화). 예시 변경은 별도 follow-up.
- **Q-3**: blocked_cache.get_blocked_tickers() 의 호출 비용이 cycle 당 1회 추가된다. 캐싱이 필요한가? — A-1 에 따라 무시 가능 (파일 read, 일 단위 변경). 별도 캐싱 SPEC 불요.
- **Q-4**: 본 SPEC 의 변경이 SPEC-014 (뉴스 분류기) 또는 SPEC-013 (스크리닝 잡) 의 출력 포맷에 영향을 주는가? — 답: 아니오. 본 SPEC 은 micro persona 의 input 와이어링만 변경하며, screened_tickers / blocked_tickers 의 생성 측 파이프라인에는 손대지 않는다.

---

## Traceability

| Requirement | Phase | Acceptance Criteria | Files Affected (대표) |
|---|---|---|---|
| REQ-018-1 | hotfix (P0) | S-4 시나리오 1, 2 | `personas/orchestrator.py` (`_build_micro_input`) |
| REQ-018-2 | hotfix (P0) | S-4 시나리오 1 의 `blocked_tickers` 키 검증 | `personas/context.py` (`assemble_micro_input`) |
| REQ-018-3 | hotfix (P0) | S-4 시나리오 3 | `personas/prompts/micro.jinja` |
| REQ-018-4 | hotfix (P1, optional) | S-4 시나리오 1 의 fallback 검증 | `personas/orchestrator.py` (fallback 분기) |
| REQ-018-5 | hotfix (P0) | acceptance.md 전체 | `tests/test_micro_persona_blocked.py` (신규), `tests/test_orchestrator.py` (잠재적 갱신) |
