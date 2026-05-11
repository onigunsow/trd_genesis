---
id: SPEC-TRADING-020
version: 0.1.0
status: draft
created: 2026-05-12
updated: 2026-05-12
author: onigunsow
priority: high
issue_number: 0
domain: TRADING
title: "DEFAULT_WATCHLIST 편향 제거 + autonomous discovery 우선화"
related_specs:
  - SPEC-TRADING-019
  - SPEC-TRADING-018
  - SPEC-TRADING-016
---

# SPEC-TRADING-020 -- DEFAULT_WATCHLIST 편향 제거 + autonomous discovery 우선화

## HISTORY

| Date | Version | Changes | Author |
|---|---|---|---|
| 2026-05-12 | 0.1.0 | Initial draft — SPEC-019 follow-up. 5 EARS requirements, DEFAULT_WATCHLIST 사용처 8개 site 중 5개 정리, blocked_cache universe 확장으로 055550 류 incident 영구 차단 | onigunsow |

---

## Scope Summary

본 SPEC 은 **SPEC-019 (data refresh layer + universe registry)** 의 직접적인 follow-up 이며, **SPEC-018 (micro persona blocked-ticker awareness)** 위에 얹는 마지막 정리 SPEC 이다. 두 선행 SPEC 의 의도된 효과 (autonomous discovery pipeline) 가 코드의 다른 지점에서 DEFAULT_WATCHLIST 가 여전히 hardcoded bias 로 박혀있어 부분적으로만 실현되었다.

### 발견 시점 (Verified Evidence, 2026-05-12 07:30~07:33 KST)

5/12 07:30 pre_market cycle 에서 micro persona 가 **055550 신한지주** 에 대해 buy signal 생성. 그러나 KIS API 의 safety check (stat_cls=55, 단기과열) 가 주문 직전에 차단. 로그:

```
2026-05-12 07:25:03,067 INFO trading.risk.blocked_cache
  Blocked tickers cache updated: 5 blocked out of 5 checked
```

근본 원인: **07:25 blocked_tickers_cache cron 이 DEFAULT_WATCHLIST 5종만 체크한다** (`src/trading/risk/blocked_cache.py:37`):

```python
tickers_to_check: list[str] = list(DEFAULT_WATCHLIST)
```

055550 는 screened_tickers 에만 존재하고 DEFAULT 에 없으므로 07:25 의 pre-flight check 를 우회. persona 파이프라인을 끝까지 통과한 뒤 마지막 단계에서 safety net 으로 차단됨. 안전 측면은 작동하지만 **persona 시간/CPU 낭비 + 사용자가 매번 buy 알림을 받지만 실제 거래는 0건**.

### 24시간 누적 incident — DEFAULT_WATCHLIST 가 일으킨 3건의 부작용

| Date / Time | Phenomenon | Root Cause | Resolution |
|---|---|---|---|
| 2026-05-11 09:30/11:00/13:30/14:30 | 4 cycles 모두 zero-trade | DEFAULT 5종이 전부 단기과열 list 진입 | SPEC-018 으로 micro persona 가 인지 |
| 2026-05-11 14:41 (manual) | decision persona reject all (decisions: []) | DEFAULT 만 OHLCV 캐시 hit, screened 캐시 미스 | SPEC-019 로 universe 확장 + 자동 refresh |
| 2026-05-12 07:33 | 055550 buy signal → safety net block | blocked_cache 가 DEFAULT 만 체크 | **본 SPEC** |

### 사용자 architectural 의도

사용자 (박세훈, 2026-05-12) 명시:
> "종목을 내가 정하는게 아니라 시스템에서 추천 하고 감시한 다음에 매수 시점을 추천하는게 맞지않나?"

시스템은 이미 autonomous discovery pipeline 보유 (daily_screen 06:30 → micro → decision → risk → safety). 그러나 DEFAULT_WATCHLIST 가 다음 이유로 layered bias 로 작용:

1. 사용자 선호도를 반영하지 않음 (2026-05-04 의 임시 시드)
2. 엄격한 선정 기준 없이 도입 (코멘트: `# Default watchlist (M5 — to be refined with the user)`)
3. 오늘의 incident 처럼 cron infrastructure 의 SPOF 화

### DEFAULT_WATCHLIST 사용처 매핑 (8 sites, 2026-05-12 grep)

| Site | File:Line | 역할 | 본 SPEC 액션 |
|---|---|---|---|
| 1 | `risk/blocked_cache.py:37` | universe 결정 | **REQ-020-2 변경** |
| 2 | `data/universe.py:27,94,107` | SPEC-019 registry | **REQ-020-1 fallback 의미 명확화** |
| 3 | `screener/daily_screen.py:30,184` | base_set 자기참조 | **REQ-020-4 정리** |
| 4 | `personas/context.py:20,290` | 정의 + fallback | **REQ-020-5 docstring** |
| 5 | `personas/orchestrator.py:166-188` | _build_micro_input merge | **REQ-020-3 단순화** |
| 6 | `contexts/build_micro_context.py:12,87` | 06:30 .md context builder | Q-2 로 defer |
| 7 | `tools/portfolio_tools.py:14` | import only | 변경 불필요 |

### 본 SPEC 의 위치

- **SPEC-016 Phase 1 (완료)**: persona 파이프라인 안정화
- **SPEC-017 (미시작)**: 실거래 전환 토글
- **SPEC-018 (완료, merged `5734034`)**: micro persona 의 blocked-ticker 인식
- **SPEC-019 (완료, merged `3d78aa9`)**: data refresh + universe registry 신설
- **SPEC-020 (본 SPEC, high priority)**: DEFAULT_WATCHLIST hardcoded bias 정리, autonomous discovery 우선

본 SPEC 은 SPEC-019 가 만든 `get_data_universe()` 를 **모든 universe 결정 지점이 일관되게 사용**하도록 정리하는 마무리 작업이다.

### 비즈니스 임팩트

- 055550 류 incident 영구 차단 (blocked_cache 가 full universe 를 사전 차단)
- persona 파이프라인의 CPU/시간 낭비 제거 (단기과열 ticker 가 micro 까지 도달 안 함)
- autonomous discovery 결과가 명확히 우선되고 DEFAULT 는 cold-start fallback 으로만 의미를 가짐
- 향후 yaml 기반 user watchlist SPEC 의 사전 정리 (DEFAULT 가 dead code 가 되더라도 안전한 fallback 위치)

---

## Environment

- SPEC-016 Phase 1 + SPEC-018 + SPEC-019 의 redeploy 가 모두 완료된 상태 (main `3d78aa9`)
- 기존 `src/trading/data/universe.py` (SPEC-019 신설) — `get_data_universe()` 함수 보유
- 기존 `src/trading/risk/blocked_cache.py` — 07:25 cron 진입점
- 기존 `src/trading/personas/orchestrator.py` — `_build_micro_input` 메서드
- 기존 `src/trading/screener/daily_screen.py` — 06:30 cron, `base_set` 사용
- 기존 `src/trading/personas/context.py` — `DEFAULT_WATCHLIST` 상수 정의 (5종: 005930, 000660, 035420, 035720, 373220)
- 기존 SPEC-018 의 REQ-018-4 fallback 동작 (`DEFAULT 가 전부 blocked 이면 screened 로 전환`)
- 기존 478 test pass baseline (SPEC-019 후)
- 신규 코드 없음 — 기존 5개 파일의 부분 수정만 + 1개 테스트 신규 + 2개 테스트 갱신

## Assumptions

- A-1: `get_data_universe()` (SPEC-019, REQ-019-6) 의 시그니처와 동작은 유지된다 — 단 내부 merge semantics 만 변경.
- A-2: `screened_tickers.json` 의 파서는 SPEC-018 / SPEC-019 에서 이미 사용 중인 헬퍼를 재사용한다. 새 파서 작성 불필요.
- A-3: DEFAULT_WATCHLIST 의 5종 ticker 자체는 변경하지 않는다 (별도 SPEC).
- A-4: 본 SPEC 의 변경은 SPEC-018 REQ-018-4 fallback 의 의도 (DEFAULT 가 전부 blocked → screened 사용) 를 그대로 보존한다 — 새 logic 으로 자동 충족됨.
- A-5: `daily_screen` 의 `base_set` 의 정확한 역할은 manager-tdd 가 코드 탐색으로 확인. 본 SPEC 은 두 가지 결정 옵션 (제거 vs KOSPI200 대체) 모두 허용.

---

## Goals

- **G-1 (Single universe entrypoint)**: 모든 universe 결정 지점이 `get_data_universe()` 단일 함수를 호출.
- **G-2 (Autonomous discovery first)**: screened_tickers 가 비어있지 않으면 우선 사용, DEFAULT 는 cold-start fallback 으로만.
- **G-3 (Pre-flight safety)**: blocked_cache cron 이 full universe 를 체크하여 055550 류 incident 영구 차단.
- **G-4 (Backward compatibility)**: screened_tickers 가 빈 경우의 동작은 현재와 동일 (DEFAULT 5종 사용).
- **G-5 (Documentation)**: DEFAULT_WATCHLIST 의 의미를 docstring 으로 명확히 — "cold-start fallback only".

---

## Requirements

### REQ-020-1: Universe fallback semantics 일원화 (Ubiquitous + State-Driven, P0)

시스템은 `get_data_universe()` 의 merge semantics 를 **screened 우선, DEFAULT 는 cold-start fallback** 으로 정정해야 한다.

세부:

- (a) **(State-Driven)** **While** `screened_tickers` set 이 non-empty 이면, **the system shall** screened ∪ holdings ∪ KOSPI200_top50 의 union 을 반환하고 DEFAULT_WATCHLIST 는 **포함하지 않는다**.
- (b) **(State-Driven)** **While** `screened_tickers` set 이 empty 또는 missing 이면, **the system shall** DEFAULT_WATCHLIST ∪ holdings ∪ KOSPI200_top50 의 union 을 반환한다 (cold-start fallback).
- (c) **(Ubiquitous)** 반환 list 는 SPEC-019 REQ-019-6 (b) 와 동일하게 `sorted(set(...))`.
- (d) **(Unwanted)** 결과가 빈 list 인 경우, SPEC-019 REQ-019-6 (c) 의 catastrophic-case 가드는 그대로 유지 — DEFAULT_WATCHLIST 만이라도 반환.
- (e) **(Ubiquitous)** 함수 시그니처 (`get_data_universe() -> list[str]`) 와 모든 호출 지점 (`refresh_ohlcv`, `refresh_flows`, `refresh_fundamentals`) 의 호환성은 유지된다 — 호출자 측 변경 불필요.

**Files affected**:

- `src/trading/data/universe.py:27,94,107` — `get_data_universe()` 의 merge logic 정정
- `tests/data/test_universe.py` — 새 fallback semantics 검증 케이스 추가

**Dependencies**: SPEC-019 REQ-019-6 (선행 완료).

---

### REQ-020-2: blocked_cache universe 확장 (Event-Driven, P0)

**When** 07:25 KST `blocked_tickers_cache` cron 이 실행되면, **then** 시스템은 `list(DEFAULT_WATCHLIST)` 대신 `get_data_universe()` 의 전체 universe 를 KIS API 로 체크해야 한다.

세부:

- (a) **(Event-Driven)** **When** APScheduler 가 07:25 trigger 발생시키면, `refresh_blocked_tickers` 는 `get_data_universe()` 호출 결과를 `tickers_to_check` 로 사용한다.
- (b) **(Ubiquitous)** 결과적으로 체크 대상은 DEFAULT (cold-start 시) 또는 screened ∪ holdings ∪ KOSPI200_top50 (정상 운영 시) 의 ≥ 20 ticker.
- (c) **(Unwanted)** 시스템은 더 이상 `tickers_to_check: list[str] = list(DEFAULT_WATCHLIST)` 라는 hardcoded 사용을 **포함해서는 안 된다**.
- (d) **(Ubiquitous)** 기존의 KIS API rate-limit 처리, per-ticker 격리, blocked_tickers.json 저장 로직은 변경 없음 — universe source 만 교체.
- (e) **(Ubiquitous)** 잡 종료 시 metric 로그는 기존과 동일하나 universe 크기 변화가 반영된다 (`5 blocked out of 5 checked` → 예: `5 blocked out of 27 checked`).

**Files affected**:

- `src/trading/risk/blocked_cache.py:37` — `tickers_to_check` 할당 로직 교체
- `tests/risk/test_blocked_cache.py` (신규 또는 기존 테스트 확장) — universe 확장 검증

**Dependencies**: REQ-020-1.

---

### REQ-020-3: orchestrator `_build_micro_input` 단순화 (Ubiquitous + State-Driven, P0)

시스템은 `personas/orchestrator.py:_build_micro_input` 에서 `DEFAULT + screened[:15]` merge 패턴 대신 **screened 우선, DEFAULT fallback** 패턴을 사용해야 한다.

세부:

- (a) **(State-Driven)** **While** `screened_tickers` 가 non-empty 이면, `user_watchlist` 는 screened 만 사용 — DEFAULT 와 merge 하지 않는다.
- (b) **(State-Driven)** **While** `screened_tickers` 가 empty / missing 이면, `user_watchlist` 는 DEFAULT_WATCHLIST 만 사용 (cold-start fallback).
- (c) **(Ubiquitous)** SPEC-018 REQ-018-4 의 fallback (`DEFAULT 가 전부 blocked 이면 screened 로 전환`) 의 의도는 (a)/(b) 의 새 logic 으로 자동 충족된다 — DEFAULT 사용 자체가 cold-start 한정이므로 추가 코드 불필요.
- (d) **(Ubiquitous)** `_build_micro_input` 의 다른 책임 (blocked_tickers 주입, news 첨부, history snapshot) 은 변경 없음.

**Files affected**:

- `src/trading/personas/orchestrator.py:166-188` — `_build_micro_input` 의 merge 로직 단순화
- `tests/personas/test_micro_blocked_tickers.py` — REQ-018-4 fallback test 갱신 (새 logic 으로도 동일 outcome 검증)

**Dependencies**: REQ-020-1 (universe.py 와 동일한 fallback semantics 유지).

---

### REQ-020-4: daily_screen base_set normalization (Ubiquitous, P1)

`screener/daily_screen.py:184` 의 `base_set = set(DEFAULT_WATCHLIST)` 는 daily_screen 의 책임 (후보 발굴) 과 self-referential 관계 (daily_screen 의 output 을 그 input bias 로 사용) 이므로 정리해야 한다.

세부:

- (a) **(Ubiquitous)** manager-tdd 는 RED 단계 직전에 `base_set` 의 실제 사용처를 코드 탐색으로 확인한다.
- (b) **(State-Driven)** **While** `base_set` 이 단순히 "DEFAULT 가 항상 후보에 포함되어야 한다"는 liquidity guarantee 용도라면, `base_set = set()` 으로 변경하여 daily_screen 의 자유도 회복 (DEFAULT 는 daily_screen 의 정규 screening criteria 를 통과한 경우에만 포함됨).
- (c) **(State-Driven)** **While** `base_set` 이 다른 load-bearing 역할 (예: 첫 부팅 시 빈 screened 방지) 이라면, KOSPI200 top-N (pykrx adapter 활용) 으로 교체 또는 현 동작 유지 + 향후 follow-up SPEC 으로 명시.
- (d) **(Ubiquitous)** 결정 결과 (제거 vs KOSPI200 대체 vs 유지) 와 근거를 `plan.md` 의 M-2 단계 산출물로 명시.

**Files affected**:

- `src/trading/screener/daily_screen.py:30,184` — base_set 결정 반영
- 기존 `tests/screener/` (있다면) — base_set 의 새 동작 검증

**Dependencies**: 없음. 본 REQ 는 P1 — base_set 의 역할이 불명확하면 follow-up SPEC 으로 defer 가능.

---

### REQ-020-5: DEFAULT_WATCHLIST docstring 명시 (Ubiquitous, P1)

시스템은 `personas/context.py:20` 의 `DEFAULT_WATCHLIST` 정의 위에 의도를 명시하는 docstring 을 보유해야 한다.

세부:

- (a) **(Ubiquitous)** 기존의 misleading 코멘트 (`# Default watchlist (M5 — to be refined with the user)`) 는 다음과 같은 docstring 으로 교체:
  ```
  # SPEC-020: Cold-start fallback ticker list only.
  # NOT used when daily_screen produces screened_tickers.json with ≥ 1 entry.
  # Do NOT add user-preferred tickers here — use yaml-based watchlist (future SPEC) instead.
  # Current 5 tickers (005930, 000660, 035420, 035720, 373220) were seeded 2026-05-04 as bootstrap defaults.
  ```
- (b) **(Ubiquitous)** docstring 은 README 또는 CHANGELOG 에도 동일 의미가 반영된다 (선택, `/moai:3-sync` 단계에서).
- (c) **(Unwanted)** DEFAULT_WATCHLIST 의 5 ticker 값은 본 SPEC 에서 **변경하지 않는다**.

**Files affected**:

- `src/trading/personas/context.py:20` — 주석 교체

**Dependencies**: 없음.

---

## Specifications

### S-1: get_data_universe() 의 새 fallback semantics (의사 코드)

```python
def get_data_universe() -> list[str]:
    """SPEC-020: screened-first, DEFAULT-as-cold-start-fallback."""
    screened = _load_screened()  # set or None
    holdings = _load_holdings()  # set
    kospi200 = _load_kospi200_top50()  # set

    if screened:  # autonomous discovery active
        universe = screened | holdings | kospi200
    else:  # cold-start fallback
        universe = set(DEFAULT_WATCHLIST) | holdings | kospi200

    if not universe:  # SPEC-019 REQ-019-6 (c) catastrophic guard
        universe = set(DEFAULT_WATCHLIST)

    return sorted(universe)
```

### S-2: Variable rename (선택, 권장)

`risk/blocked_cache.py` 의 `tickers_to_check` 는 의미상 universe 의 일부이므로, manager-tdd 는 다음 rename 을 선택할 수 있다 (변수명 명확화 목적):

- `tickers_to_check` → `universe_to_check` 또는 그대로 유지

### S-3: Acceptance Criteria (Given/When/Then)

본 SPEC 의 5개 acceptance 시나리오는 `acceptance.md` 에 상세 정의. spec.md 에서는 다음 5개를 정식 acceptance criteria 로 명시:

**시나리오 1 — REQ-020-1 screened 우선**:
- **Given** `screened_tickers.json` 에 20개 candidate AND DEFAULT_WATCHLIST 5종이 존재함,
- **When** `get_data_universe()` 호출,
- **Then** 반환 list 는 20 screened candidate 를 모두 포함 AND DEFAULT 5종은 포함하지 않는다 (단, screened 에 우연히 같은 ticker 가 있는 경우 제외).

**시나리오 2 — REQ-020-1 cold-start fallback**:
- **Given** `screened_tickers.json` 이 empty 또는 missing,
- **When** `get_data_universe()` 호출,
- **Then** 반환 list 는 DEFAULT_WATCHLIST 5종을 포함 (cold-start path).

**시나리오 3 — REQ-020-2 blocked_cache universe 확장**:
- **Given** 07:25 `blocked_tickers_cache` cron trigger AND `get_data_universe()` 가 ≥ 20 ticker 반환,
- **When** `refresh_blocked_tickers` 실행,
- **Then** KIS API 가 universe 의 모든 ticker (≥ 20) 에 대해 query 호출됨 AND `tickers_to_check` 는 DEFAULT 5종만 포함하지 않는다.

**시나리오 4 — 055550 류 incident 영구 차단 (REQ-020-2 의 자기검증)**:
- **Given** 055550 가 `screened_tickers.json` 에 있음 AND KRX 단기과열 list 에도 있음,
- **When** 07:25 cron 실행,
- **Then** `blocked_tickers.json` 에 055550 이 포함되고 AND 07:30 pre_market cycle 의 micro persona 가 055550 을 blocked 로 인식.

**시나리오 5 — REQ-020-3 micro persona universe 청결**:
- **Given** `_build_micro_input` 호출 시 screened 가 20 candidate 보유,
- **When** micro persona input assemble,
- **Then** `user_watchlist` 는 정확히 20 screened candidate (DEFAULT 5종이 추가로 섞이지 않음).

---

## Non-Goals (Out of Scope)

본 SPEC 은 다음을 **명시적으로 다루지 않는다**:

- DEFAULT_WATCHLIST 의 5 ticker 값 변경 — 별도 결정 SPEC
- DEFAULT_WATCHLIST 의 yaml config 마이그레이션 — 향후 별도 SPEC
- KOSPI200 top-50 source 변경 — SPEC-019 Q-1 에서 이미 pykrx 동적 결정
- 실거래 전환 토글 — SPEC-017 영역
- 텔레그램 봇 라우팅 변경 — SPEC-019 follow-up
- 신규 persona 타입 또는 LLM 로직 변경 — SPEC-016 Phase 2/3 영역
- `contexts/build_micro_context.py:87` 의 DEFAULT_WATCHLIST 사용 — Q-2 로 defer (load-bearing 여부 manager-tdd 가 결정)
- `tools/portfolio_tools.py:14` 의 import — 변경 불필요 (import 만 존재)

---

## Implementation Hints (manager-tdd 참고용, 본 SPEC 에서는 구현하지 않음)

- **변경 LOC 예측**: 총 ~50 LOC 내외 (소스 5개 파일 + 테스트 3개 파일).
- **DEFAULT_WATCHLIST 보존**: 절대 삭제하지 않는다. fallback 상수로 그대로 유지.
- **screened_tickers 파서**: SPEC-018 / SPEC-019 에서 이미 사용 중인 헬퍼 재사용 — 새 파서 작성 불필요.
- **테스트 fixture**: `tmp_path` 로 빈 `screened_tickers.json` 합성하여 cold-start path 검증.
- **REQ-020-4 결정 로직**: M-2 (Pre-RED) 단계에서 `base_set` 의 모든 호출처를 grep → load-bearing 여부 결정. 불명확하면 P1 follow-up.
- **회귀 영향**: SPEC-018 의 `tests/personas/test_micro_blocked_tickers.py` 의 REQ-018-4 fallback test 가 새 logic 에서도 동일 outcome 을 가지는지 검증 필요. (DEFAULT 가 전부 blocked 인 cold-start case → screened 로 fallback) — 본 SPEC 의 새 logic 에서는 cold-start case 에서 screened 가 empty 가정이므로, 해당 test 의 setup 을 조정해야 할 수 있음.

---

## Files Expected to Change (구현 단계 참고)

| File | Change Type | Rough LOC | Owner |
|---|---|---|---|
| `src/trading/data/universe.py` | Modify (merge logic) | +10 ~ +15 | manager-tdd |
| `src/trading/risk/blocked_cache.py` | Modify (universe source) | +3 ~ +5 | manager-tdd |
| `src/trading/personas/orchestrator.py` | Modify (_build_micro_input) | +5 ~ +10 | manager-tdd |
| `src/trading/screener/daily_screen.py` | Modify (base_set decision) | +0 ~ +10 | manager-tdd |
| `src/trading/personas/context.py` | Modify (docstring) | +5 ~ +8 | manager-tdd |
| `tests/data/test_universe.py` | Extend | +30 ~ +50 | manager-tdd |
| `tests/risk/test_blocked_cache.py` | New file | +40 ~ +60 | manager-tdd |
| `tests/personas/test_micro_blocked_tickers.py` | Modify | +5 ~ +15 | manager-tdd |

총 변경 LOC 추정: ~100 ~ 170 LOC, 8 파일 (소스 5 + 테스트 3).

---

## Constraints

- **C-1**: backward compatible — `screened_tickers.json` 이 empty 또는 missing 일 때 동작은 현재와 동일.
- **C-2**: SPEC-018 REQ-018-4 의 fallback 동작이 새 logic 에서도 의미상 보존되어야 함 (cold-start case 의 blocked-DEFAULT 시나리오).
- **C-3**: 기존 478 test pass baseline (SPEC-019 후) 유지. 신규 5~7 test 추가 후 ~485 pass 목표.
- **C-4**: Coverage 임계 85% 유지.
- **C-5**: 본 SPEC 의 모든 변경은 git branch `feat/spec-020-default-watchlist-bias-removal` 로 격리, PR 단위로 사용자 리뷰.
- **C-6**: 본 SPEC 은 high (not critical) — safety check 가 여전히 작동하므로 P0 critical 격상은 불필요. 단, manager-tdd 가 5/12 08:20-08:30 KST window 안에 merge + redeploy 가능 시 권장.

---

## Risks

| ID | 리스크 | 영향 | 가능성 | 대응 |
|---|---|---|---|---|
| R-1 | REQ-020-4 의 `base_set` 이 daily_screen 의 load-bearing 동작에 영향 | Medium | Medium | P1 으로 분류. manager-tdd 가 RED 단계 직전 코드 탐색 + 결정. 불확실 시 현 동작 유지 + follow-up SPEC 명시 |
| R-2 | SPEC-018 의 REQ-018-4 fallback test 회귀 | Low | Medium | 해당 test 의 setup 을 새 logic 에 맞춰 조정. 동일 outcome 검증 |
| R-3 | blocked_cache 의 KIS API 호출 횟수 5 → ~27 로 증가, rate-limit 부담 | Low | Low | universe 크기는 ≤ 100, KIS rate-limit (보통 ≥ 10/sec) 에 충분한 여유 |
| R-4 | screened_tickers 가 비정상 0건이지만 missing 은 아닌 경우 (예: 빈 list) | Low | Low | A-2 의 파서가 빈 list 와 missing 을 동일하게 처리한다고 가정 — manager-tdd 가 RED 케이스로 검증 |
| R-5 | 본 SPEC 의 변경 후 첫 cycle 에서 universe 갑작스러운 확장이 다운스트림 (decision/risk) 에 부하 | Low | Low | SPEC-019 의 refresh layer 가 이미 universe 전체에 대해 cache 를 채워둠 — 추가 부하 무시 가능 |

---

## Rollout Plan

### 단일 Phase — 5/12 (화) 08:20 ~ 08:30 KST window

1. (08:20~) `feat/spec-020-default-watchlist-bias-removal` 브랜치 생성
2. `/moai:2-run SPEC-TRADING-020` 실행 — manager-tdd 가 RED-GREEN-REFACTOR 진행
   - Pre-RED: `base_set` 의 호출처 grep (REQ-020-4 결정)
   - RED: `tests/data/test_universe.py` 확장 + `tests/risk/test_blocked_cache.py` 신규 + SPEC-018 test 갱신
   - GREEN: 5개 소스 파일 minimal modification (~50 LOC)
   - REFACTOR: 정리 + 478 baseline 회귀 검증
3. Coverage 검증, ruff/black 통과, PR 생성, 사용자 리뷰
4. `make redeploy` 로 컨테이너 재배포
5. (5/12 09:00) stale-monitor cron 첫 실행 — 본 SPEC 으로 인한 동작 변화 없음 (SPEC-019 monitoring 그대로)
6. (5/12 09:30) 첫 intraday cycle — micro persona 의 universe 가 screened 만 포함하는지 확인 (DEFAULT contamination 없음)
7. (5/13 07:25) 다음날 첫 blocked_tickers_cache cron — `... blocked out of N checked` 의 N 이 5 가 아닌 ≥ 20 인지 로그 확인 (REQ-020-2 의 자기검증)
8. (5/13 09:30) 두번째 intraday cycle — 회귀 없음 최종 확인
9. `/moai:3-sync SPEC-TRADING-020` 으로 문서 동기화, SPEC 상태를 `completed` 로 변경

### Safety Gates

- **종료 전 게이트 1**: 단위 테스트 ~485 통과 (기존 478 + 신규 5~7) AND coverage ≥ 85%
- **종료 전 게이트 2**: 사용자가 직접 `make redeploy` 후 컨테이너 healthcheck 5/5 통과
- **종료 전 게이트 3**: 5/12 09:30 cycle 로그에서 micro user_watchlist 가 DEFAULT 5종만이 아닌 screened universe 임 확인
- **종료 전 게이트 4**: 5/13 07:25 cron 로그에서 `blocked out of ≥ 20 checked` 형태 확인
- **종료 전 게이트 5**: SPEC-018 의 REQ-018-4 fallback test 가 새 logic 에서도 GREEN

---

## Open Questions

- **Q-1 (REQ-020-4)**: `daily_screen` 의 `base_set` 이 어떤 load-bearing 역할을 하는가? (a) 단순 self-bias, (b) liquidity guarantee, (c) cold-start safety. manager-tdd 가 코드 탐색 후 결정. 권장: (a) 라면 제거, (b)/(c) 라면 KOSPI200 top-N 대체 또는 follow-up SPEC 으로 defer.
- **Q-2 (defer)**: `contexts/build_micro_context.py:87` (06:30 build_micro_context cron 의 DEFAULT_WATCHLIST loop) 도 `get_data_universe()` 로 전환해야 하는가? — 본 SPEC 의 REQ 에 포함하지 않음. 정적 .md context generation 의 안정성에 영향을 줄 수 있어 manager-tdd 가 discovery 후 별도 SPEC 으로 분리 권장.

---

## Traceability

| Requirement | Phase | Acceptance Criteria | Files Affected (대표) |
|---|---|---|---|
| REQ-020-1 | hotfix (P0) | S-3 시나리오 1, 2 | `data/universe.py`, `tests/data/test_universe.py` |
| REQ-020-2 | hotfix (P0) | S-3 시나리오 3, 4 | `risk/blocked_cache.py`, `tests/risk/test_blocked_cache.py` |
| REQ-020-3 | hotfix (P0) | S-3 시나리오 5 | `personas/orchestrator.py`, `tests/personas/test_micro_blocked_tickers.py` |
| REQ-020-4 | hotfix (P1) | acceptance.md `base_set` decision | `screener/daily_screen.py` |
| REQ-020-5 | hotfix (P1) | acceptance.md docstring 검증 | `personas/context.py` |
