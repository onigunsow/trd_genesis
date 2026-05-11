---
id: SPEC-TRADING-018
title: "Implementation Plan -- Micro persona blocked-ticker awareness"
created: 2026-05-11
updated: 2026-05-11
status: ready_for_run
---

# Implementation Plan -- SPEC-TRADING-018

## Context Recap

- **상위 SPEC**: 본 SPEC 은 SPEC-016 Phase 1 (commit `9aeebb7`) 위에 얹는 hotfix 이며, SPEC-016 Phase 2 (regime DB) 와는 직교한다.
- **발견 시점**: 2026-05-11 라이브 검증일, 09:30 및 11:00 두 차례 intraday cycle 이 `signals: []` 반환.
- **근본 원인**: micro persona 가 DEFAULT_WATCHLIST 5종에 잠겨 있고, 오늘 5종이 모두 단기과열 매매제한 리스트와 일치.
- **해결 전략**: 3-layer defense (orchestrator 필터링 + context 와이어링 + prompt awareness) + optional fallback.

## Implementation Approach

### Methodology

- **Mode**: TDD (RED-GREEN-REFACTOR) — `.moai/config/sections/quality.yaml` 의 development_mode 기본값 사용.
- **Rationale**: 변경 규모가 작고 (5 파일, ~150 LOC) 영구 회귀 방지가 핵심 가치이므로, 신규 3개 테스트를 먼저 작성하는 TDD 가 적합. Brownfield enhancement (기존 코드 영역 수정) 패턴 — Pre-RED 단계에서 `_build_micro_input` 및 `assemble_micro_input` 현행 동작 파악 선행.

### Milestones (Priority-based)

본 SPEC 은 단일 Phase 의 hotfix 이므로 milestone 을 priority 순으로 나열한다 (시간 추정 없음).

**Primary Goal (P0, hotfix 출시 조건)**:

1. **M-1**: 신규 단위 테스트 3개 작성 (RED 단계, 모두 실패 확인) — REQ-018-5 의 a-c
2. **M-2**: `_build_micro_input` 의 blocked_tickers 필터링 구현 — REQ-018-1
3. **M-3**: `assemble_micro_input` 의 blocked_tickers 키 추가 — REQ-018-2
4. **M-4**: `micro.jinja` 의 매매제한 블록 추가 — REQ-018-3
5. **M-5**: 신규 3개 테스트 통과 (GREEN), 기존 62 테스트 통과 (no regression)
6. **M-6**: REFACTOR — 코드 정리, simplify skill 적용, coverage ≥ 85% 확인
7. **M-7**: `make redeploy` 로 컨테이너 재배포, healthcheck 5/5 통과
8. **M-8**: 14:00 또는 15:00 intraday cycle 검증 — micro persona universe ≥ 5종 + decision persona signals 배열 비어있지 않음

**Secondary Goal (P1, optional follow-up)**:

9. **M-9**: REQ-018-4 (screened fallback) 의 추가 분기 로직 구현 + 테스트 1건 추가
10. **M-10**: `/moai:3-sync SPEC-TRADING-018` 으로 문서 동기화 (CHANGELOG, README 영향 부분)

**Final Goal (출구 게이트)**:

11. **M-11**: 본 SPEC 의 status 를 `completed` 로 갱신, SPEC-016 Phase 2 재개 신호

### Technical Approach

#### Layer 1: orchestrator 필터링 (`src/trading/personas/orchestrator.py`)

- 현행 `_build_micro_input(today, macro_summary)` 함수 (라인 154-166) 의 `expanded_watchlist` 산출 직후, `blocked_cache.get_blocked_tickers()` 호출
- 필터링 패턴: `[t for t in expanded if t not in set(blocked)]` (set 으로 변환하여 O(N) 검색)
- 필터링 결과가 빈 리스트일 때 (REQ-018-4 활성 시) screened_tickers[:10] 중 non-blocked 으로 보강
- `assemble_micro_input` 호출에 `blocked_tickers=blocked` 전달

#### Layer 2: context 와이어링 (`src/trading/personas/context.py`)

- `assemble_micro_input` 시그니처에 `blocked_tickers: list[str] = None` 추가 (None 디폴트, 함수 내 빈 리스트로 정규화)
- 반환 dict 에 `"blocked_tickers": blocked_tickers or []` 추가 — 항상 키 존재 보장 (REQ-018-2 (c))

#### Layer 3: prompt awareness (`src/trading/personas/prompts/micro.jinja`)

- 기존 컨텍스트 블록 (`[유니버스 스냅샷]`, `[공시]`, `[메모리]` 등) 과 시각적으로 분리된 위치에 `{% if blocked_tickers %} ... {% endif %}` 블록 삽입
- 블록 위치 권장: `[유니버스 스냅샷]` 직후 (universe 와 blocked 의 의미적 인접성)

#### Layer 4 (optional, REQ-018-4): fallback 분기

- `_build_micro_input` 의 필터링 결과 길이 체크 → 0 이고 screened 가 있으면 fallback
- 로그: `logger.warning("[micro_fallback] DEFAULT_WATCHLIST fully blocked, falling back to screened_tickers[:N=%d]", n)`

### Architecture Direction

본 SPEC 은 **persona context wiring** 의 결함을 수정하는 hotfix 이며, 다음 아키텍처 원칙을 따른다:

- **Single Responsibility**: 각 레이어 (orchestrator / context / prompt) 가 자신의 책임에 한정된 변경
- **Defense in Depth**: 3-layer 모두 blocked 인식 — orchestrator 가 1차 방어선 (universe 자체에서 제외), prompt 가 2차 방어선 (LLM 이 우회해도 prompt 가 차단), context 는 데이터 와이어링 (1차와 2차 사이의 연결)
- **Backward Compatibility**: blocked_tickers 가 비어 있는 모든 정상 거래일에 기존 동작 100% 보존
- **Forward Compatibility**: SPEC-016 Phase 2 의 regime/risk_appetite 도입 시 시그니처 충돌 없도록 키워드 인자 형태 유지

### Testing Strategy

- **신규 테스트 3개** (`tests/test_micro_persona_blocked.py`):
  1. `test_build_micro_input_filters_blocked` — pytest 의 monkeypatch 로 `blocked_cache.get_blocked_tickers` 를 DEFAULT_WATCHLIST 전체 반환하도록 stub, screened_tickers 도 mock 으로 20개 합성 → 결과 watchlist 가 5종을 모두 제외하고 ≥10종 포함 assert
  2. `test_assemble_micro_input_returns_blocked_key` — 직접 호출하여 반환 dict 의 키 존재 + 값 일치 assert
  3. `test_micro_jinja_renders_blocked_block` — `personas/prompts` 의 Jinja Environment 를 로드, blocked_tickers 변수로 렌더링하여 문자열 매칭 assert (블록 존재 케이스 + 부재 케이스)
- **회귀 테스트**: 기존 62 테스트 전수 통과 — 특히 `tests/test_orchestrator.py` 의 `_build_micro_input` shape 관련 assertion 식별 후 갱신

### Rollout

- branch: `feat/spec-018-blocked-tickers`
- commits: TDD 사이클마다 단위 commit (RED test 추가 / GREEN 구현 / REFACTOR 정리)
- PR: 1개 PR 로 단일 검토
- redeploy: `make redeploy` (SPEC-016 Phase 1 산출물의 단일 진입점)
- 검증: 14:00 또는 15:00 cycle 의 persona_runs 테이블 쿼리

## Dependencies

- **상위 의존**: SPEC-016 Phase 1 (commit `9aeebb7`) 의 인프라/CLI 안정화 — 완료됨
- **하위 의존**: 없음. 본 SPEC 은 sink (다른 SPEC 이 본 SPEC 을 기다리지 않음)
- **블로커 후보 (없음 확인됨)**:
  - `blocked_cache.get_blocked_tickers()` 함수 존재 확인 (orchestrator.py:39 의 import 로 검증됨)
  - `data/blocked_tickers.json` 의 stat_cls=55 항목 형식 — exchange feed 가 안정적으로 갱신 중 (A-1)
  - `data/screened_tickers.json` 의 가용성 — 06:35 daily_screen 잡이 매일 정상 실행 (A-2)

## Risk Response

| Risk | 대응 milestone | 후속 조치 |
|---|---|---|
| R-1 (LLM 이 매매제한 무시) | M-2 (1차 방어선 보장) | M-8 의 라이브 cycle 검증으로 최종 확인 |
| R-3 (DEFAULT_WATCHLIST 편향) | 본 SPEC 범위 외 | follow-up SPEC 으로 분리 |
| R-4 (blocked_cache 반환 형식) | M-1 의 신규 테스트 fixture | 실 prod 데이터로 추가 검증 |
| R-5 (기존 테스트 깨짐) | M-1 의 pre-RED 단계 | manager-tdd 가 식별 후 즉시 갱신 |

## Quality Gates

- **TRUST 5**:
  - Tested: 신규 3 테스트 + 기존 62 = 65 통과, coverage ≥ 85%
  - Readable: 변경된 함수 시그니처에 type hint + docstring 보강
  - Unified: ruff/black 통과
  - Secured: 본 변경에 보안 영향 없음 (input validation 만 추가)
  - Trackable: 모든 commit 이 SPEC-TRADING-018 참조, conventional commits 형식

- **MX Tag 후보**:
  - `_build_micro_input` 함수에 `@MX:ANCHOR` 고려 (cycle 의 진입점, fan_in ≥ 3)
  - 신규 fallback 분기에 `@MX:NOTE` (의도 명시: "fully-blocked edge case fallback")

## Next Steps

1. 사용자 승인 후 `/clear` 실행하여 컨텍스트 초기화
2. `/moai:2-run SPEC-TRADING-018` 으로 manager-tdd 에 위임
3. 구현 완료 후 `make redeploy` 로 배포
4. 14:00 또는 15:00 cycle 검증
5. `/moai:3-sync SPEC-TRADING-018` 으로 문서 동기화
