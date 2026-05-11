---
id: SPEC-TRADING-018
title: "Acceptance Criteria -- Micro persona blocked-ticker awareness"
created: 2026-05-11
updated: 2026-05-11
status: ready_for_run
---

# Acceptance Criteria -- SPEC-TRADING-018

## Definition of Done

본 SPEC 은 다음 모든 조건이 충족될 때 `completed` 로 전환된다:

- [ ] REQ-018-1 ~ REQ-018-5 의 모든 acceptance test 통과
- [ ] 기존 단위 테스트 62/62 + 신규 3 = 65/65 모두 통과
- [ ] Coverage ≥ 85% (.moai/config/sections/quality.yaml)
- [ ] ruff / black 0건 위반
- [ ] PR 사용자 리뷰 완료
- [ ] `make redeploy` 후 컨테이너 healthcheck 5/5 통과
- [ ] 5/11 14:00 또는 15:00 intraday cycle 에서 micro persona universe ≥ 5종 + decision persona signals 비어있지 않음

---

## Test Scenarios (Given-When-Then)

### Scenario 1 — 오늘(2026-05-11)의 사고 재현 및 해소 (REQ-018-1, REQ-018-2)

**Given**:
- `data/blocked_tickers.json` 이 DEFAULT_WATCHLIST 의 5종 (`["005930", "000660", "035420", "035720", "373220"]`) 을 stat_cls=55 (단기과열) 로 모두 포함
- `data/screened_tickers.json` 이 20개의 비-blocked 신규 후보를 포함 (오늘 06:35 daily_screen 출력)
- `blocked_cache.get_blocked_tickers()` 가 위 5종을 반환하도록 monkeypatch

**When**:
- `_build_micro_input(today="2026-05-11", macro_summary=<sample>)` 이 호출된다

**Then**:
- 반환된 `expanded_watchlist` 의 길이 ≥ 10
- `expanded_watchlist` 에 `"005930", "000660", "035420", "035720", "373220"` 중 **어느 것도 포함되지 않음**
- `expanded_watchlist` 의 모든 원소가 `screened_tickers` 출신 (또는 DEFAULT_WATCHLIST 중 non-blocked 종목, 본 시나리오에서는 모두 screened 출신)
- `assemble_micro_input` 의 반환 dict 에 `"blocked_tickers"` 키가 존재
- `result["blocked_tickers"]` 값이 `["005930", "000660", "035420", "035720", "373220"]` (순서는 무관, set 비교 가능)

---

### Scenario 2 — 정상 거래일의 zero behavioral regression (REQ-018-1 (c), REQ-018-2 (c))

**Given**:
- `data/blocked_tickers.json` 이 비어 있음 (또는 파일 부재)
- `blocked_cache.get_blocked_tickers()` 가 빈 리스트 `[]` 를 반환하도록 monkeypatch
- `data/screened_tickers.json` 이 20개의 후보를 포함

**When**:
- `_build_micro_input(today="2026-05-11", macro_summary=<sample>)` 이 호출된다

**Then**:
- 반환된 `expanded_watchlist` 의 내용이 **SPEC-018 적용 이전과 완전히 동일** (DEFAULT_WATCHLIST 5종 + screened[:15] 의 중복 제거 결과)
- `expanded_watchlist` 에 DEFAULT_WATCHLIST 의 5종이 모두 포함됨 (`"005930", "000660", "035420", "035720", "373220"`)
- `assemble_micro_input` 의 반환 dict 에 `"blocked_tickers"` 키가 존재
- `result["blocked_tickers"]` 값이 **빈 리스트** `[]` (None 이 아님)

---

### Scenario 3 — prompt awareness 렌더링 (REQ-018-3)

**Given**:
- Jinja2 Environment 가 `src/trading/personas/prompts/` 디렉터리에서 `micro.jinja` 를 로드
- 렌더링 컨텍스트에 `blocked_tickers = ["005930", "000660"]` 와 기타 필수 키 (today, macro_summary, universe_snapshot 등) 가 채워짐

**When**:
- `template.render(**context)` 호출

**Then**:
- 렌더링된 문자열에 다음 모두 포함:
  - `[매매제한 종목]` 헤더 문자열
  - `005930` 및 `000660` 각 ticker code
  - "후보에서 반드시 제외" 또는 동등한 명시적 제외 지시 문장
- 블록은 단독 섹션으로 분리 (`[유니버스 스냅샷]` 등 기존 블록과 시각적 분리)

---

### Scenario 4 — blocked 가 비어 있을 때의 블록 부재 (REQ-018-3 (c))

**Given**:
- Jinja2 Environment 가 `micro.jinja` 를 로드
- 렌더링 컨텍스트에 `blocked_tickers = []` (빈 리스트)

**When**:
- `template.render(**context)` 호출

**Then**:
- 렌더링된 문자열에 `[매매제한 종목]` 헤더가 **부재**
- 렌더링된 문자열에 "매매제한" 키워드 자체가 부재 (블록 전체가 생략됨)
- 다른 컨텍스트 블록 (`[유니버스 스냅샷]`, `[공시]` 등) 은 정상 렌더링

---

### Scenario 5 — Optional fallback: 빈 universe edge case (REQ-018-4)

**Given**:
- DEFAULT_WATCHLIST 의 5종이 모두 blocked
- `data/screened_tickers.json` 이 비어 있음 → 빈 리스트
- 또는 screened 도 모두 blocked

**When**:
- `_build_micro_input(today="2026-05-11", macro_summary=<sample>)` 이 호출된다

**Then (REQ-018-4 (a) 가 활성화된 경우)**:
- screened 가 가용하면 `screened_tickers[:10]` 중 non-blocked 종목으로 watchlist 가 채워짐
- screened 도 비어 있으면 빈 watchlist 그대로 전달 (micro persona 가 `signals: []` 반환하도록 둠)
- 로그에 `[micro_fallback] DEFAULT_WATCHLIST fully blocked, falling back to screened_tickers[:N]` 메시지 출력

---

### Scenario 6 — End-to-end live cycle 검증 (Definition of Done 의 라이브 게이트)

**Given**:
- SPEC-018 의 변경이 commit + merged
- `make redeploy` 로 컨테이너 재배포 완료
- 컨테이너 healthcheck 5/5 통과
- `data/blocked_tickers.json` 에 오늘의 5종 단기과열 종목 포함 (현실 데이터)

**When**:
- 14:00 또는 15:00 intraday scheduler 잡이 실행

**Then**:
- DB 쿼리 결과 검증:
  ```sql
  SELECT persona_name, jsonb_array_length(response_json->'candidates') AS n_candidates
  FROM persona_runs
  WHERE ts >= CURRENT_DATE + INTERVAL '14 hours'
    AND ts < CURRENT_DATE + INTERVAL '16 hours'
    AND persona_name = 'micro'
  ORDER BY ts DESC LIMIT 1;
  ```
  - `n_candidates` ≥ 5
- decision persona 의 signals 배열:
  ```sql
  SELECT persona_name, jsonb_array_length(response_json->'signals') AS n_signals
  FROM persona_runs
  WHERE ts >= CURRENT_DATE + INTERVAL '14 hours'
    AND persona_name = 'decision'
  ORDER BY ts DESC LIMIT 1;
  ```
  - `n_signals` ≥ 1 (반드시 ENTRY 일 필요는 없음, HOLD/WATCH 라도 universe 가 살아 있다는 신호)

---

## Quality Gates (TRUST 5)

### Tested

- **Unit tests**: 신규 3개 (`tests/test_micro_persona_blocked.py`) + 기존 62 = 65/65 PASS
- **Coverage**: ≥ 85% (lines), 본 SPEC 의 변경 영역 (orchestrator._build_micro_input, context.assemble_micro_input, micro.jinja) 은 ≥ 95%
- **Characterization**: Scenario 2 가 기존 동작의 characterization test 역할 (zero regression 보장)

### Readable

- 변경된 함수에 type hint 보강 (`blocked_tickers: list[str] | None = None`)
- 변경된 함수에 한 줄 docstring 보강 (`"""[SPEC-018] blocked_tickers 를 universe 에서 제외하고 prompt 컨텍스트로 전달"""`)
- 신규 테스트의 각 케이스에 의도를 설명하는 docstring

### Unified

- `ruff check .` 0 위반
- `black --check .` 0 위반
- `mypy src/trading/personas/` (선택, 프로젝트에서 mypy 사용 중이라면) 0 위반

### Secured

- 본 변경에 보안 영향 없음 (외부 input 처리 없음, 내부 데이터 와이어링만 변경)
- `blocked_cache.get_blocked_tickers()` 의 반환값이 list[str] 임을 단위 테스트에서 형식 검증

### Trackable

- 모든 commit message 가 `feat(SPEC-TRADING-018): ...` 또는 `fix(SPEC-TRADING-018): ...` 형식 (conventional commits)
- PR description 이 본 SPEC 의 모든 REQ-018-* 항목 참조
- MX tag 후보: `_build_micro_input` 에 `@MX:ANCHOR` (cycle 진입점), fallback 분기에 `@MX:NOTE` (의도 명시)

---

## Verification Methods and Tools

| Verification | Tool / Command | 기대 결과 |
|---|---|---|
| 단위 테스트 통과 | `pytest tests/ -v` | 65 passed in N seconds |
| Coverage | `pytest --cov=src/trading --cov-report=term-missing` | TOTAL coverage ≥ 85% |
| Linter | `ruff check .` | All checks passed! |
| Formatter | `black --check .` | All done! ✨ |
| 컨테이너 healthcheck | `docker compose ps` | scheduler healthy 5/5 |
| 라이브 cycle (Scenario 6) | 위 SQL 쿼리 | n_candidates ≥ 5, n_signals ≥ 1 |
| 회귀 (Scenario 2) | 단위 테스트 `test_build_micro_input_filters_blocked` 의 blocked=[] 케이스 | PASS |

---

## Out of Scope (Verification 대상 아님)

다음은 본 SPEC 의 acceptance 에서 검증하지 않는다:

- micro persona LLM 응답의 의미적 품질 (예: 후보 종목의 매매 가치)
- decision persona 의 ENTRY 신호 생성 여부 (HOLD/WATCH 도 universe 가 살아 있다는 신호로 충분)
- KIS Open API 의 실시간 가격 정확성
- SPEC-016 Phase 2 (regime DB) 의 통합 동작
- screened_tickers 가 0건인 날의 별도 알람 (A-2 참조)
- DEFAULT_WATCHLIST 자체의 변경 (non-goal)
- micro.jinja 의 JSON 예시 변경 (Q-2 참조, 별도 follow-up)
