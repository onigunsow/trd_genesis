---
id: SPEC-TRADING-020
title: "Implementation Plan -- DEFAULT_WATCHLIST 편향 제거"
created: 2026-05-12
updated: 2026-05-12
status: ready_for_run
---

# Implementation Plan -- SPEC-TRADING-020

## Context Recap

- **상위 SPEC**: SPEC-020 은 SPEC-019 (commit `3d78aa9`) + SPEC-018 (commit `5734034`) 위에 얹는 마지막 정리 hotfix.
- **발견 시점**: 2026-05-12 07:30~07:33 KST. 055550 신한지주가 micro persona buy signal 까지 통과한 뒤 safety net 으로 차단됨. 07:25 `blocked_tickers_cache` cron 이 DEFAULT_WATCHLIST 5종만 체크하여 055550 (screened-only) 이 pre-flight 우회.
- **근본 원인**: SPEC-019 가 `get_data_universe()` 를 만들었으나, `risk/blocked_cache.py` / `personas/orchestrator.py` / `screener/daily_screen.py` 가 여전히 `DEFAULT_WATCHLIST` 를 hardcoded 로 직접 사용. SPEC-019 의 의도된 효과 (autonomous discovery) 가 universe 결정 지점 전반에서 일관되게 적용되지 않음.
- **해결 전략**: `get_data_universe()` 의 fallback semantics 정정 (screened 우선, DEFAULT 는 cold-start only) + 모든 universe 결정 지점이 동일 함수 호출 + DEFAULT_WATCHLIST docstring 명시.

## Implementation Approach

### Methodology

- **Mode**: TDD (RED-GREEN-REFACTOR) — `.moai/config/sections/quality.yaml` default.
- **Rationale**: 본 SPEC 은 기존 함수의 분기 로직 변경이 핵심이고, fixture (빈 screened_tickers.json) 합성이 간단함. TDD 가 regression 방지에 최적.

### Milestones (Priority-based)

본 SPEC 은 단일 Phase 의 lightweight hotfix 이므로 milestone 을 priority 순으로 나열.

**Primary Goal (P0, hotfix 출시 조건)**:

1. **M-1 (Pre-RED)**: 코드 탐색 — `screener/daily_screen.py:184` 의 `base_set` 사용처를 grep 으로 전수조사. load-bearing 여부 결정 (REQ-020-4 의 (b)/(c)/(d) 분기). 또한 `screened_tickers.json` 파서 헬퍼 확인 (SPEC-018/SPEC-019 에서 사용한 함수 재사용 가능 여부).
2. **M-2 (RED, universe)**: `tests/data/test_universe.py` 확장 — REQ-020-1 의 시나리오 1, 2 검증 케이스 추가 (screened 우선 / cold-start fallback). 모두 실패 확인.
3. **M-3 (RED, blocked_cache)**: `tests/risk/test_blocked_cache.py` 신규 작성 — REQ-020-2 의 시나리오 3, 4 검증. universe 확장 후 KIS API 호출 횟수 검증 + 055550 류 ticker 의 사전 차단 검증. 디렉터리 없으면 생성. 모두 실패 확인.
4. **M-4 (RED, orchestrator)**: `tests/personas/test_micro_blocked_tickers.py` 갱신 — REQ-020-3 의 시나리오 5 (DEFAULT contamination 없음) 검증 + SPEC-018 REQ-018-4 fallback test 의 setup 을 새 logic 에 맞춰 조정. 모두 실패 확인.
5. **M-5 (GREEN, universe)**: `src/trading/data/universe.py` 의 `get_data_universe()` 분기 로직 정정 (~10 LOC). M-2 통과.
6. **M-6 (GREEN, blocked_cache)**: `src/trading/risk/blocked_cache.py:37` 의 `tickers_to_check` 할당을 `get_data_universe()` 호출로 교체 (~3 LOC). M-3 통과.
7. **M-7 (GREEN, orchestrator)**: `src/trading/personas/orchestrator.py:166-188` 의 `_build_micro_input` merge 로직 단순화 (~5 LOC). M-4 통과.
8. **M-8 (GREEN, base_set)**: M-1 결정에 따라 `src/trading/screener/daily_screen.py:30,184` 정리 (제거 / KOSPI200 대체 / 현 동작 유지 + follow-up 명시). 결정 근거를 `plan.md` 또는 PR description 에 기록.
9. **M-9 (GREEN, docstring)**: `src/trading/personas/context.py:20` 의 코멘트를 REQ-020-5 의 docstring 으로 교체 (~5 LOC).
10. **M-10 (REFACTOR)**: 코드 정리, type hint + docstring 보강, 기존 478 테스트 (SPEC-019 baseline) 통과 확인, coverage ≥ 85% 검증.
11. **M-11 (Deploy)**: PR 생성, 사용자 리뷰, `make redeploy`, 컨테이너 healthcheck 5/5 통과.
12. **M-12 (Cycle gate)**: 5/12 09:30 첫 intraday cycle — micro persona universe 가 screened only (DEFAULT contamination 없음) 확인.
13. **M-13 (Cron gate)**: 5/13 07:25 blocked_tickers_cache cron — `blocked out of ≥ 20 checked` 로그 확인.

**Secondary Goal (P1, optional follow-up)**:

14. **M-14**: REQ-020-4 의 `base_set` 이 KOSPI200 대체로 결정된 경우, pykrx 의 `stock.get_index_portfolio_deposit_file("1028")` 통합 검증.
15. **M-15**: `/moai:3-sync SPEC-TRADING-020` — CHANGELOG, README 의 universe 섹션 (있다면) 갱신.
16. **M-16**: Q-2 의 follow-up SPEC 결정 — `contexts/build_micro_context.py:87` 의 DEFAULT_WATCHLIST loop 전환 여부.

---

## Technical Approach

### A. Universe fallback semantics 정정 (REQ-020-1)

**현재 (SPEC-019)**:
```python
universe = set(DEFAULT_WATCHLIST) | screened | holdings | kospi200
```

**변경 (SPEC-020)**:
```python
if screened:
    universe = screened | holdings | kospi200
else:
    universe = set(DEFAULT_WATCHLIST) | holdings | kospi200
```

핵심: screened 의 non-empty 가 autonomous discovery 의 신호 → DEFAULT 와 mutually exclusive.

### B. blocked_cache universe source 교체 (REQ-020-2)

**현재**:
```python
tickers_to_check: list[str] = list(DEFAULT_WATCHLIST)
```

**변경**:
```python
from trading.data.universe import get_data_universe
tickers_to_check: list[str] = get_data_universe()
```

회귀 영향: KIS API 호출 횟수 5 → ~20-30 회. C-1 의 backward compat 은 cold-start case 에서 자동으로 유지됨 (screened empty → DEFAULT 사용).

### C. orchestrator `_build_micro_input` 단순화 (REQ-020-3)

**현재 (SPEC-018 + SPEC-019 적용 후)**:
```python
candidates = list(DEFAULT_WATCHLIST) + list(screened[:15])
# ... blocked filter, fallback logic for REQ-018-4 ...
```

**변경**:
```python
candidates = list(screened) if screened else list(DEFAULT_WATCHLIST)
# REQ-018-4 fallback 은 자동 충족 — DEFAULT 가 전부 blocked 인 cold-start 시
# screened 가 비어있다는 의미가 더 이상 성립하지 않음
```

REQ-018-4 의 test 갱신 필수.

### D. daily_screen base_set 결정 (REQ-020-4, P1)

manager-tdd 가 M-1 단계에서 결정:

- 옵션 A (단순 self-bias): `base_set = set()` — daily_screen 의 자유도 회복
- 옵션 B (liquidity guarantee): KOSPI200 top-N 으로 교체 (pykrx adapter)
- 옵션 C (불확실): 현 동작 유지 + follow-up SPEC 명시

권장: 옵션 A 우선 시도. 옵션 B/C 는 R-1 의 risk 가 검증되면 즉시 적용.

### E. docstring 교체 (REQ-020-5)

`personas/context.py:20`:

```python
# SPEC-020: Cold-start fallback ticker list only.
# NOT used when daily_screen produces screened_tickers.json with ≥ 1 entry.
# Do NOT add user-preferred tickers here — use yaml-based watchlist (future SPEC) instead.
# Current 5 tickers (005930, 000660, 035420, 035720, 373220) were seeded 2026-05-04 as bootstrap defaults.
DEFAULT_WATCHLIST: list[str] = [...]
```

---

## Risks and Mitigation

| ID | 리스크 | 대응 (M-x 매핑) |
|---|---|---|
| R-1 | `base_set` load-bearing | M-1 에서 결정. 불확실 시 옵션 C (defer) |
| R-2 | SPEC-018 REQ-018-4 test 회귀 | M-4 에서 setup 조정 |
| R-3 | KIS API 호출 5→27 증가 부하 | C-1 의 rate-limit 여유로 무시 가능 |
| R-4 | screened 빈 list vs missing 처리 | M-2 의 fixture 로 검증 |
| R-5 | universe 확장 downstream 부하 | SPEC-019 refresh layer 가 이미 캐시 채움 |

---

## Dependencies

- **Hard dependency**: SPEC-019 (merged `3d78aa9`) — `get_data_universe()` 함수 존재 전제.
- **Hard dependency**: SPEC-018 (merged `5734034`) — REQ-018-4 fallback test 가 갱신 대상.
- **Soft dependency**: 478 test pass baseline (SPEC-019 후) — 회귀 없음 검증 기준.

---

## Notes

- 본 SPEC 은 **lightweight follow-up** — 신규 모듈 0개, 신규 파일 1개 (test), 수정 5 파일.
- 시간 (clock-time) 추정 금지. priority-based milestone 으로만 표현.
- 본 SPEC 의 변경은 단일 PR 단위로 처리 가능 — `feat/spec-020-default-watchlist-bias-removal`.
