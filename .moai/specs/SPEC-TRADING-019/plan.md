---
id: SPEC-TRADING-019
title: "Implementation Plan -- Market data automated refresh layer"
created: 2026-05-11
updated: 2026-05-11
status: ready_for_run
---

# Implementation Plan -- SPEC-TRADING-019

## Context Recap

- **상위 SPEC**: SPEC-019 는 SPEC-016 Phase 1 (commit `9aeebb7`) + SPEC-018 (commit `feat/spec-018-blocked-tickers` 후) 위에 얹는 data infrastructure hotfix 이며, SPEC-016 Phase 2 (regime DB) 와는 직교한다.
- **발견 시점**: 2026-05-11 14:41 KST 라이브 검증일, SPEC-018 의 manual pre_market_cycle 실행 결과 `decisions: []` 반환. micro persona 의 fix 는 정상이었으나 decision persona 가 4종의 valid 후보 (005380/009540/161890/034730) 를 OHLCV/fundamentals/flows 캐시 미스로 거부.
- **근본 원인**: `scheduler/runner.py` 의 14개 cron 잡 중 **시장 데이터 fetch 잡이 0건**. `trading fetch-data` CLI 가 수동 backfill 용으로만 작성되어 있고 cron 으로 연결되지 않음. 약 10일간 (~5/2 이후) stale 운영.
- **해결 전략**: 5개 신규 cron 잡 (OHLCV / flows / fundamentals / disclosures / stale-monitor) + 1개 universe 레지스트리 모듈 + 1개 monitoring 모듈.

## Implementation Approach

### Methodology

- **Mode**: TDD (RED-GREEN-REFACTOR) — `.moai/config/sections/quality.yaml` 의 development_mode 기본값.
- **Rationale**: 본 SPEC 은 신규 모듈 3개 (universe / refresh_market_data / data_freshness) 가 핵심이고, 외부 API mock + 시간 mock 이 충분히 가능하므로 TDD 가 자연스러움. 또한 cron 잡의 idempotency / 휴장일 가드 / per-ticker 격리 등 비기능 요구사항이 강해 회귀 방지가 핵심 가치.

### Milestones (Priority-based)

본 SPEC 은 단일 Phase 의 hotfix 이므로 milestone 을 priority 순으로 나열한다 (시간 추정 없음).

**Primary Goal (P0, hotfix 출시 조건)**:

1. **M-1 (Pre-RED)**: 코드 탐색 — active holdings 의 조회 진입점 확인 (`src/trading/portfolio/` 또는 `src/trading/db/`), KOSPI200 source 결정 (정적 yaml vs pykrx 동적), Telegram 알람 진입점 확인 (`trading.notify.telegram` 모듈 또는 직접 API 호출), 현 `.env` 의 chat_id 변수명 확인.
2. **M-2 (RED, universe)**: `tests/data/test_universe.py` 작성 — `get_data_universe()` 의 union 조합 / 빈 source 처리 / DEFAULT_WATCHLIST fallback (REQ-019-6 c) 검증 케이스 5~7개. 모두 실패 확인.
3. **M-3 (RED, refresh)**: `tests/scheduler/test_data_refresh_jobs.py` 작성 — `refresh_ohlcv()` / `refresh_flows()` / `refresh_fundamentals()` / `refresh_disclosures()` 의 per-ticker 격리 / metric 출력 / DART gap 자동 감지 검증 케이스 8~12개. 모두 실패 확인.
4. **M-4 (RED, monitoring)**: `tests/monitoring/test_data_freshness.py` 작성 — `check_and_alert()` 의 stale 임계 / KRX 휴장일 보정 / 알람 메시지 형식 / false-positive 방지 검증 케이스 5~8개. 모두 실패 확인. `datetime.now()` 직접 사용 금지, clock 파라미터 주입 패턴.
5. **M-5 (GREEN, universe)**: `src/trading/data/universe.py` 구현 — REQ-019-6 충족, M-2 의 모든 테스트 통과.
6. **M-6 (GREEN, refresh)**: `src/trading/scripts/refresh_market_data.py` 구현 — REQ-019-1 ~ REQ-019-4 충족, M-3 의 모든 테스트 통과.
7. **M-7 (GREEN, monitoring)**: `src/trading/monitoring/data_freshness.py` 구현 — REQ-019-5 충족, M-4 의 모든 테스트 통과.
8. **M-8 (GREEN, scheduler)**: `src/trading/scheduler/runner.py` 에 5개 cron 잡 추가. APScheduler 로그에 등록 확인하는 통합 테스트 1건 추가 (선택).
9. **M-9 (REFACTOR)**: 코드 정리, `_safe_call` / `_wrap` helper 재사용 점검, type hint + docstring 보강, 기존 65 테스트 (SPEC-018 baseline) 통과 확인, coverage ≥ 85% 검증.
10. **M-10 (Deploy)**: PR 생성, 사용자 리뷰, `make redeploy`, 컨테이너 healthcheck 5/5 통과.
11. **M-11 (Stale gate)**: 5/12 09:00 stale-monitor cron 실행 → Telegram 알람으로 gap 감지 확인 (REQ-019-5 의 자기검증).
12. **M-12 (Refresh gate)**: 5/12 16:00 OHLCV cron 실행 → metric 로그 확인, 90일 backfill 완료.
13. **M-13 (Cycle gate)**: 5/13 09:30 첫 intraday cycle → micro universe ≥ 5종 + decision signals ≥ 1건 (본 SPEC 의 최종 출구).

**Secondary Goal (P1, optional follow-up)**:

14. **M-14**: REQ-019-7 (bootstrap backfill on container start) 구현 — 컨테이너 entrypoint 분기 추가, 첫 부팅 시 90일 backfill 자동 실행. R-7 (false-positive on cold start) 의 대응책으로 P0 격상 검토 권장.
15. **M-15**: REQ-019-8 (per-ticker timeout budget) 구현 — `signal.alarm` 또는 `concurrent.futures` 의 타임아웃 wrapper 추가.
16. **M-16**: `/moai:3-sync SPEC-TRADING-019` 으로 문서 동기화 (CHANGELOG, README 의 cron 잡 섹션, runbook 확장).

**Final Goal (출구 게이트)**:

17. **M-17**: 본 SPEC 의 status 를 `completed` 로 갱신. 5/14 09:00 stale-monitor 가 알람 송출하지 않음을 확인 (false-positive 검증).

### Technical Approach

#### Layer 1: Universe registry (`src/trading/data/universe.py`)

- 신규 단일 함수 `get_data_universe() -> list[str]`.
- 4 source 의 union: DEFAULT_WATCHLIST (import from `src/trading/personas/context.py`), screened (read `data/screened_tickers.json`), holdings (DB or portfolio module 조회), KOSPI200 top-50 (정적 yaml 또는 pykrx).
- 각 source 의 실패는 logger.warning + 건너뜀. 전체 실패 시 DEFAULT_WATCHLIST 만 반환 (catastrophic case 방지).
- 반환: `sorted(set(...))` 의 ticker code 리스트.
- 단위 테스트는 monkeypatch 로 각 source 를 stub 하여 모든 분기 검증.

#### Layer 2: Refresh entrypoints (`src/trading/scripts/refresh_market_data.py`)

- 4개 진입점: `refresh_ohlcv()`, `refresh_flows()`, `refresh_fundamentals()`, `refresh_disclosures()`.
- 공통 패턴:
  - universe 조회 (disclosures 는 universe 무관)
  - 각 ticker 에 대해 try/except 로 어댑터 호출
  - metric dict 누적 + INFO 로그
  - 외부 함수 (어댑터, cache 조회) 는 모두 mock 가능한 함수 인자 또는 모듈 레벨 import 로 노출
- DART 의 gap 자동 감지 (REQ-019-4 c): `cache.get_latest_disclosure_ts()` 조회 후 today - 2 이전이면 `--recent 12` 동등 호출.
- OHLCV 의 incremental vs backfill 분기 (REQ-019-1 c): `cache.get_latest_ohlcv_ts(ticker)` 가 None 이면 90일 backfill, 아니면 (last_ts + 1d) ~ today.
- bootstrap 분기 (REQ-019-7, optional): `if cache.count_rows('ohlcv') == 0` 류 가드 후 90일 fetch.

#### Layer 3: Monitoring (`src/trading/monitoring/data_freshness.py`)

- 단일 진입점 `check_and_alert(clock: Callable[[], datetime] = datetime.now)`.
- 4개 테이블 점검 (ohlcv / fundamentals / flows / disclosures), 각각 expected_ts 계산.
- expected_ts 계산은 `calendar.py` 의 KRX 휴장일 헬퍼 활용:
  - ohlcv / flows: 직전 trading day
  - fundamentals: 직전 Sunday (+1d 여유)
  - disclosures: today - 1 (DART 365일)
- stale 임계 (S-4 표) 초과 시 알람 메시지 누적 → Telegram 송출.
- 알람 메시지 형식 (S-5) 준수 — 4개 테이블 결과를 한 메시지로 압축.
- Telegram 진입점: `trading.notify.telegram.send_message(chat_id, text)` 또는 `requests.post`.

#### Layer 4: Scheduler wiring (`src/trading/scheduler/runner.py`)

- 기존 `main()` 함수 내 14개 잡 등록 직후 5개 잡 추가:
  - `data_refresh_ohlcv` — `lambda: _wrap("data_refresh_ohlcv", refresh_market_data.refresh_ohlcv)` + `CronTrigger(day_of_week="mon-fri", hour=16, minute=0, timezone=KST)`
  - `data_refresh_flows` — 동일 패턴, 16:05
  - `data_refresh_fundamentals` — `_safe_call` (휴장일 가드 불요), Sunday 18:00
  - `data_refresh_disclosures` — `_safe_call` (365일), 매일 18:00
  - `data_freshness_check` — `_wrap`, mon-fri 09:00
- `_wrap` / `_safe_call` 의 기존 패턴 그대로 활용. 별도 helper 추가 불요.

### Architecture Direction

본 SPEC 은 **data infrastructure layer** 의 결함을 수정하는 hotfix 이며, 다음 아키텍처 원칙을 따른다:

- **Single Responsibility**: universe (정책) / refresh (실행) / monitoring (검증) 의 3개 모듈로 책임 분리.
- **Single Source of Truth**: `get_data_universe()` 가 fetch 정책의 유일한 진입점. 어디서도 DEFAULT_WATCHLIST 를 직접 import 하여 union 하지 않음.
- **Defense in Depth**: refresh cron 이 실패해도 09:00 stale-monitor 가 30초 이내 알람 송출 → 운영자가 인지 → 수동 복구 가능.
- **Backward Compatibility**: 기존 14개 cron 잡 / persona / scheduler 동작 변경 없음. 5개 신규 잡만 추가.
- **Forward Compatibility**: SPEC-016 Phase 2 (regime DB) 의 도입 시 universe / refresh / monitoring 모듈에 영향 없음. Phase 2 의 macro_state_cache 또는 system_state 컬럼은 본 SPEC 과 독립.
- **Testability**: `clock` / `Telegram client` / 어댑터를 모두 함수 인자 또는 module-level import 로 노출 → monkeypatch 로 100% mock 가능.

### Testing Strategy

- **신규 테스트 3개 파일** (`tests/data/test_universe.py`, `tests/scheduler/test_data_refresh_jobs.py`, `tests/monitoring/test_data_freshness.py`):
  - universe 테스트: union 조합 / 빈 source / DEFAULT_WATCHLIST fallback / 정렬 + dedup / 외부 IO 실패 처리 — 5~7개 케이스
  - refresh 테스트: per-ticker 격리 / metric 출력 / DART gap 자동 감지 / OHLCV incremental vs backfill / fundamentals weekly / 휴장일 가드 — 8~12개 케이스
  - monitoring 테스트: 4 테이블 stale 감지 / KRX 휴장일 보정 / 알람 메시지 형식 / false-positive (fresh data) / clock mock — 5~8개 케이스
- **회귀 테스트**: 기존 65 테스트 (SPEC-018 baseline 기준) 전수 통과. 특히 `runner.py` 의 잡 개수를 assert 하는 케이스가 있다면 14 → 19 로 갱신.
- **통합 테스트 (선택)**: APScheduler 등록 검증 — `main()` 호출 후 `sched.get_jobs()` 의 id 리스트에 5개 신규 잡이 포함되는지 확인.

### Rollout

- branch: `feat/spec-019-data-refresh`
- commits: TDD 사이클마다 단위 commit (RED test 추가 / GREEN 모듈별 구현 / REFACTOR 정리). 추천 commit 분할:
  1. `feat(SPEC-019): add universe registry module (REQ-019-6)`
  2. `feat(SPEC-019): add refresh_market_data entrypoints (REQ-019-1 ~ 4)`
  3. `feat(SPEC-019): add data_freshness monitoring (REQ-019-5)`
  4. `feat(SPEC-019): wire 5 new cron jobs in scheduler/runner.py`
  5. `test(SPEC-019): add 20+ test cases across 3 new test files`
- PR: 1개 PR 로 단일 검토 (5개 commit 묶음)
- redeploy: `make redeploy` (SPEC-016 Phase 1 산출물의 단일 진입점)
- 검증: 5/12 09:00 stale 알람 + 5/12 16:00 refresh metric 로그 + 5/13 09:30 cycle 결과

## Dependencies

- **상위 의존**:
  - SPEC-016 Phase 1 (commit `9aeebb7`) 의 인프라/CLI 안정화 — 완료됨
  - SPEC-018 의 micro persona blocked-ticker 인식 — 완료됨 (라이브 검증 단계)
- **하위 의존**: 없음. 본 SPEC 은 sink (다른 SPEC 이 본 SPEC 을 기다리지 않음).
- **블로커 후보 (manager-tdd 가 Pre-RED 단계에서 확인 필요)**:
  - active holdings 의 조회 진입점 — `src/trading/portfolio/` 또는 `src/trading/db/` 의 기존 함수
  - KOSPI200 top-50 의 출처 — pykrx 동적 vs 정적 yaml 결정
  - Telegram 알람 진입점 — `trading.notify` 모듈 존재 여부 확인
  - 현 `.env` 의 chat_id 변수명 — `TELEGRAM_CHAT_ID` 또는 다른 변수명
  - 기존 65 테스트 중 cron 잡 개수를 assert 하는 케이스 — 사전 식별

## Risk Response

| Risk | 대응 milestone | 후속 조치 |
|---|---|---|
| R-1 (pykrx rate-limit) | M-3 의 per-ticker 격리 검증 | M-12 의 첫 실행에서 error_count 모니터링, 필요 시 16:05 → 16:15 등 시차 조정 |
| R-4 (holdings 진입점 불명확) | M-1 의 Pre-RED 코드 탐색 | 미발견 시 빈 set 으로 대체, DEFAULT_WATCHLIST + screened 만으로 우선 출시 |
| R-5 (chat_id 혼동) | M-1 의 Pre-RED 확인 + M-4 의 단위 테스트 chat_id assert | C-8 명시, prod bot 명시적 변수명 사용 |
| R-6 (휴장일 false-positive) | M-4 의 KRX 휴장일 보정 단위 테스트 | M-11 의 5/12 09:00 실제 알람 메시지 검증 |
| R-7 (cold start false-positive) | M-14 의 REQ-019-7 (bootstrap backfill) | 사용자 결정: REQ-019-7 P0 격상 vs 첫 24h grace period |

## Quality Gates

- **TRUST 5**:
  - Tested: 신규 ~25 테스트 + 기존 65 = ~90 통과, coverage ≥ 85%
  - Readable: 신규 함수에 type hint + docstring 보강. SPEC-019 reference 코멘트로 의도 명시.
  - Unified: ruff/black 통과
  - Secured: 외부 API 호출 시 secrets 노출 금지 — TELEGRAM_BOT_TOKEN 은 env 로만 접근, 로그에 출력 금지. pykrx / DART 호출은 인증 불요이므로 추가 보안 영향 없음.
  - Trackable: 모든 commit 이 SPEC-TRADING-019 참조, conventional commits 형식.

- **MX Tag 후보**:
  - `get_data_universe()` 에 `@MX:ANCHOR` (fan_in ≥ 4: refresh_ohlcv / flows / fundamentals + 향후 호출)
  - `refresh_ohlcv()` 의 per-ticker try/except 블록에 `@MX:NOTE` (의도 명시: "per-ticker isolation, batch never aborts")
  - DART gap 자동 감지 분기에 `@MX:NOTE` (의도 명시: "first-deploy 12-day backfill recovery")
  - `check_and_alert()` 에 `@MX:ANCHOR` (운영 가시성의 핵심 진입점)
  - Telegram 송출 함수 호출 지점에 `@MX:WARN` + `@MX:REASON` (외부 IO + secrets 의존, 실패 시 silent fallback 필수)

## Next Steps

1. 사용자 승인 후 `/clear` 실행하여 컨텍스트 초기화
2. `/moai:2-run SPEC-TRADING-019` 으로 manager-tdd 에 위임
3. manager-tdd 가 Pre-RED 코드 탐색 (M-1) 결과를 보고 → 사용자가 active holdings 진입점 / KOSPI200 source / chat_id / REQ-019-7 격상 여부 결정
4. RED-GREEN-REFACTOR 사이클 진행
5. 구현 완료 후 `make redeploy` 로 배포
6. 5/12 09:00 stale 알람 + 16:00 refresh metric + 5/13 09:30 cycle 결과 검증
7. `/moai:3-sync SPEC-TRADING-019` 으로 문서 동기화
