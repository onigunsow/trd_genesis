---
id: SPEC-TRADING-020
title: "Acceptance Criteria -- DEFAULT_WATCHLIST 편향 제거"
created: 2026-05-12
updated: 2026-05-12
status: ready_for_run
---

# Acceptance Criteria -- SPEC-TRADING-020

## Definition of Done

본 SPEC 은 다음 모든 조건이 충족될 때 `completed` 로 전환된다:

- [ ] REQ-020-1 ~ REQ-020-3 의 P0 acceptance test 통과
- [ ] (P1) REQ-020-4 의 `base_set` 결정 사항이 PR description 또는 plan.md 에 명시됨
- [ ] (P1) REQ-020-5 의 docstring 이 `personas/context.py:20` 에 반영됨
- [ ] 기존 단위 테스트 478/478 (SPEC-019 baseline) + 신규 5~7 = ~485 모두 통과
- [ ] Coverage ≥ 85% (`.moai/config/sections/quality.yaml`)
- [ ] ruff / black 0건 위반
- [ ] PR 사용자 리뷰 완료
- [ ] `make redeploy` 후 컨테이너 healthcheck 5/5 통과
- [ ] 5/12 09:30 첫 intraday cycle 에서 micro persona `user_watchlist` 가 screened only (DEFAULT contamination 없음) 확인
- [ ] 5/13 07:25 blocked_tickers_cache cron 로그에서 `blocked out of N checked` 의 N ≥ 20 확인

---

## Test Scenarios (Given-When-Then)

### Scenario 1 — REQ-020-1 screened 우선 (P0)

**Given**:
- `screened_tickers.json` 에 20개 candidate 존재 (예: 055550, 005380, 009540 등 — DEFAULT 와 겹치지 않음)
- `DEFAULT_WATCHLIST` = `["005930", "000660", "035420", "035720", "373220"]`
- `holdings` set = empty
- `kospi200_top50` set = (assume empty or mock)

**When**:
- `get_data_universe()` 호출

**Then**:
- 반환 list 가 20개의 screened candidate 를 모두 포함
- 반환 list 가 DEFAULT_WATCHLIST 의 5종 (005930, 000660, 035420, 035720, 373220) 을 **포함하지 않음** (screened 와 겹치는 경우 제외)
- 반환 list 는 `sorted(set(...))` 형식

---

### Scenario 2 — REQ-020-1 cold-start fallback (P0)

**Given**:
- `screened_tickers.json` 이 missing OR empty list `[]`
- `holdings` set = empty
- `kospi200_top50` set = (assume empty or mock)

**When**:
- `get_data_universe()` 호출

**Then**:
- 반환 list 가 DEFAULT_WATCHLIST 의 5종 (005930, 000660, 035420, 035720, 373220) 을 **포함**
- 반환 list 의 길이 ≥ 5

---

### Scenario 3 — REQ-020-2 blocked_cache universe 확장 (P0)

**Given**:
- `screened_tickers.json` 에 20개 candidate 존재
- `get_data_universe()` 가 ≥ 20 ticker 반환
- KIS API 가 정상 작동
- 07:25 KST 시각

**When**:
- APScheduler `blocked_tickers_cache` cron trigger 발생
- `refresh_blocked_tickers` 실행

**Then**:
- `tickers_to_check` 변수가 `list(DEFAULT_WATCHLIST)` 로 **할당되지 않음**
- `tickers_to_check` 변수가 `get_data_universe()` 반환값으로 할당됨
- KIS API 가 universe 의 모든 ticker (≥ 20) 에 대해 query 호출됨
- `blocked_tickers.json` 에 universe 전체의 blocked 결과 저장
- 로그에 `blocked out of N checked` 형태 (N ≥ 20)

---

### Scenario 4 — 055550 류 incident 영구 차단 (REQ-020-2 자기검증, P0)

**Given**:
- 055550 (신한지주) 가 `screened_tickers.json` 에 있음
- 055550 가 KRX 단기과열 list 에도 있음 (KIS API stat_cls=55 반환)
- 07:25 cron 이 새 universe (REQ-020-2 적용 후) 로 실행됨

**When**:
- 07:30 pre_market cycle 시작

**Then**:
- 07:25 cron 종료 시 `blocked_tickers.json` 에 055550 포함됨
- 07:30 micro persona 가 candidate pool 에서 055550 을 blocked 로 인식 → buy signal 생성하지 않음
- 결과적으로 safety net (KIS API stat_cls 체크) 까지 도달하지 않음 (persona 가 사전에 거름)

---

### Scenario 5 — REQ-020-3 micro persona universe 청결 (P0)

**Given**:
- `_build_micro_input` 가 호출됨
- 입력으로 `screened_tickers` 가 20개 candidate 보유
- `blocked_tickers` 가 5개 ticker 포함 (DEFAULT 와 무관)

**When**:
- `_build_micro_input` 가 micro persona input 을 assemble

**Then**:
- `user_watchlist` 가 정확히 20개의 screened candidate
- `user_watchlist` 에 DEFAULT_WATCHLIST 의 5종이 추가로 섞이지 않음
- (회귀 검증) SPEC-018 REQ-018-4 의 fallback 의도가 유지됨 — cold-start case (screened empty) 에서는 DEFAULT 사용

---

## Negative / Edge Case Scenarios

### Scenario 6 — Empty screened (cold-start path 검증, REQ-020-1 b)

**Given**:
- `screened_tickers.json` 이 빈 list `[]`

**When**:
- `get_data_universe()` 호출

**Then**:
- 반환 list 가 DEFAULT_WATCHLIST 의 5종을 포함
- (SPEC-019 REQ-019-6 (c) catastrophic guard 와 호환)

---

### Scenario 7 — Missing screened file (cold-start path 검증, REQ-020-1 b)

**Given**:
- `screened_tickers.json` 파일이 존재하지 않음 (FileNotFoundError)

**When**:
- `get_data_universe()` 호출

**Then**:
- 함수가 예외를 raise 하지 않고 정상 반환
- 반환 list 가 DEFAULT_WATCHLIST 의 5종을 포함
- logger.warning 으로 missing 기록 (SPEC-019 REQ-019-6 (d))

---

### Scenario 8 — SPEC-018 REQ-018-4 regression (회귀 검증)

**Given**:
- `screened_tickers.json` 이 empty (cold-start)
- `blocked_tickers` 가 DEFAULT_WATCHLIST 5종을 모두 포함 (전부 blocked)

**When**:
- `_build_micro_input` 호출

**Then**:
- 시스템이 panic 없이 처리됨
- (SPEC-018 의 의도) 가능하면 screened 로 fallback — 단 본 SPEC 의 새 logic 에서는 screened 가 empty 이므로 fallback 대상이 없음
- 결과적으로 `user_watchlist` 는 비어있거나 DEFAULT 만 (panic 없이) — 빈 universe 의 다운스트림 처리는 SPEC-018 의 빈 candidate 처리 로직이 담당
- (필요 시 SPEC-018 test 의 setup 을 새 logic 에 맞춰 조정한 후 GREEN)

---

## Verification Method

### Unit Tests

- `tests/data/test_universe.py` (extend) — 시나리오 1, 2, 6, 7
- `tests/risk/test_blocked_cache.py` (new file) — 시나리오 3, 4 (KIS API mock)
- `tests/personas/test_micro_blocked_tickers.py` (modify) — 시나리오 5, 8

### Integration / Live Verification

- 5/12 09:30 cycle 로그 — `user_watchlist` content inspection
- 5/13 07:25 cron 로그 — `blocked out of N checked` 의 N 값 확인
- (선택) 5/13 09:30 cycle 의 micro persona behavior 회귀 없음 확인

---

## Quality Gate Criteria

| Criterion | Target | Verification |
|---|---|---|
| Test count | 478 (baseline) + 5~7 (new) = ~485 | `make test` 또는 `pytest` |
| Test pass rate | 100% | CI logs |
| Coverage | ≥ 85% | `pytest --cov` |
| ruff violations | 0 | `make lint` |
| black violations | 0 | `make format-check` |
| Type hints | All new functions typed | `mypy` (현 프로젝트 기준) |
| Container healthcheck | 5/5 | `docker compose ps` |
| 5/12 09:30 micro universe | screened only (no DEFAULT contamination) | Log inspection |
| 5/13 07:25 blocked_cache | N ≥ 20 | Log inspection |

---

## Out-of-Scope Verification (NOT in this SPEC)

- DEFAULT_WATCHLIST 5종의 변경 — 별도 SPEC
- yaml watchlist 마이그레이션 — 별도 SPEC
- `contexts/build_micro_context.py:87` 의 DEFAULT loop 전환 — Q-2 로 defer
- KOSPI200 top-50 source 변경 — SPEC-019 Q-1 에서 이미 결정됨
- 실거래 전환 — SPEC-017

---

## Notes

- 본 SPEC 의 모든 시나리오는 단위 테스트 수준에서 검증 가능 — 외부 API mock + tmp fixture 합성.
- 시나리오 4 (055550 incident 영구 차단) 의 live 검증은 5/13 의 실제 cron 실행 로그로 확인.
- 시나리오 8 의 SPEC-018 regression 은 manager-tdd 가 새 logic 에 맞춘 test setup 조정 후 검증.
