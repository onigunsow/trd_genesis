---
id: SPEC-TRADING-024
type: acceptance
status: draft
created: 2026-05-15
---

# SPEC-TRADING-024 -- Acceptance Criteria

## Definition of Done

본 SPEC 의 모든 Phase (1, 2, 3) 가 완료되었으며, 각 Phase 의 무사고 운영 1 주 검증을 통과하고, 기존 SPEC-001 ~ SPEC-023 의 동작이 회귀 없이 유지되는 시점.

---

## Acceptance Scenarios (Given/When/Then)

### Stage 1: Adaptive Cron + Threshold Triggers

#### AC-024-1 (REQ-024-1) — Adaptive intraday cron 밀도

- **Given**: scheduler 가 `intraday_interval_minutes: 15` 설정으로 실행 중이고 일반적인 영업일이면
- **When**: 영업일 09:00 ~ 15:30 KST 가 경과하면
- **Then**: 9 회 이상의 intraday 사이클이 실행되어야 하며, `cycle_runs` 테이블에 기록되어야 한다
- **Then (회귀)**: 기존 4 개 cron 시간대 (09:30/11:00/13:30/14:30) 도 정상 발화되어야 하며, 같은 시간대에 중복 발화는 없어야 한다 (dedup 확인)

#### AC-024-2 (REQ-024-2) — Price threshold trigger

- **Given**: holding ticker (예: 005930 삼성전자) 가 watcher 에 등록되어 있고 cooldown 외 상태이면
- **When**: 해당 ticker 의 가격이 11:47 KST 시점에 30 분 내 -3% 하락하면
- **Then**: 60 초 이내에 ticker-specific lightweight 사이클 (micro Haiku → Sonnet decision → safety check) 이 실행되어야 하고, `persona_decisions` 테이블에 event 가 기록되어야 한다
- **Then (cap)**: 같은 영업일 중 21 번째 threshold trigger 가 발화 시도되면 skip 되고 `trigger_log` 에 cap_reached 사유가 기록되어야 한다

#### AC-024-3 (REQ-024-3) — Volume anomaly trigger

- **Given**: dynamic_universe 에 포함된 ticker 가 watcher 에 등록되어 있으면
- **When**: 해당 ticker 의 거래량이 20-day average 의 2 배 초과 AND ATR 이 typical 의 1.5 배 초과를 동시 만족하면
- **Then**: ticker-specific 분석 사이클이 발화되어야 하고, REQ-024-2 와 동일한 cooldown (5 분) 및 일일 cap (20) 이 적용되어야 한다

#### AC-024-4 (REQ-024-4) — Blocked-release watcher

- **Given**: 단기과열 차단 (SPEC-018) 상태의 ticker 055550 가 KRX 에 등록되어 있고 시장 시간이면
- **When**: KRX 가 13:25 KST 에 해당 ticker 를 release 하고, watcher 가 13:30 polling 에서 이를 감지하면
- **Then**: release 가 `trigger_log` 에 기록되고, 해당 ticker 의 re-evaluation 사이클이 즉시 발화되어야 한다

### Stage 2: Real-time Stream + Position Watchdog

#### AC-024-5 (REQ-024-5) — WebSocket 재접속

- **Given**: KIS WebSocket 이 09:00 KST 에 정상 connected 상태이고 holdings + top 30 dynamic ticker = 총 31 ticker 가 구독되어 있으면
- **When**: 11:00 KST 에 WebSocket 이 disconnect 되면
- **Then**: 30 초 이내에 재접속이 완료되어야 하고, 중복 tick 은 dedup 되어 `tick_events` 테이블에 같은 (ticker, ts) 쌍의 row 가 2 개 이상 존재하지 않아야 한다
- **Then (backoff)**: 재접속 실패 시 exponential backoff (1, 2, 4, 8, max 60s) 로 재시도되어야 한다

#### AC-024-6 (REQ-024-6) — News continuous trigger

- **Given**: holding ticker (예: 281820 하이닉스) 에 대한 뉴스가 5 분 polling 주기 직후 발행되었고 impact=5/5 로 분류되면
- **When**: continuous news poll 이 해당 뉴스를 감지하면
- **Then**: 5 분 이내에 position watchdog 이 발화하여 ticker-specific decision 사이클이 실행되어야 한다

#### AC-024-7 (REQ-024-7) — Position watchdog no-op fast path

- **Given**: holding ticker 281820 의 마지막 평가 가격이 등록되어 있고 PnL 평가가 완료된 상태이면
- **When**: tick event 가 동일 가격 (변화 없음) 으로 도착하면
- **Then**: persona 호출 (어떤 tier 이든) 은 발생하지 않아야 하고, `tick_events` 에 no-op 으로 기록되어야 한다
- **Then (PnL 변화 시)**: tick event 의 가격이 마지막 평가 대비 -3.5% 변화하면 decision persona 자동 발화되어야 한다

#### AC-024-8 (REQ-024-8) — Multi-tier throttle

- **Given**: 영업일 동안 Tier 1 Haiku watcher 가 50 회 발화되어 Tier 2 cap (50) 에 도달한 상태이면
- **When**: 51 번째 stream event 가 Tier 1 의 "signal" 판정을 받으면
- **Then**: Tier 2 호출은 skip 되고 `trigger_log` 에 tier2_cap_reached 사유로 기록되어야 한다 (event 자체는 queue 에 보관)

#### AC-024-9 (REQ-024-9) — Cost circuit breaker 80% / 100%

- **Given**: 일일 LLM 누적 비용이 8,000 KRW (80% of 10,000 KRW cap) 에 도달한 상태이면
- **When**: 다음 trigger 가 발화되면
- **Then**: 시스템은 budget warning 을 로그에 기록하고, 해당 event 를 degraded tier (Tier 2 skip, Tier 1 + safety check 만) 로 처리해야 한다

- **Given**: 일일 LLM 누적 비용이 10,000 KRW (100%) 에 도달한 상태이면
- **When**: 다음 trigger 가 발화되면
- **Then**: threshold trigger 가 비활성화되고 cron baseline 만 유지되어야 한다
- **Then (reset)**: 다음 영업일 00:00 KST 에 자동 reset 되어 adaptive cron 및 watcher 정상 동작 재개되어야 한다

### Backward Compatibility

#### AC-024-BC-1 — 기존 4 cron 보존

- **Given**: 본 SPEC 의 Phase 1 또는 2 가 배포되었으면
- **When**: 영업일이 진행되면
- **Then**: 기존 4 개 intraday cron (09:30, 11:00, 13:30, 14:30) 및 pre_market cron (07:30) 이 정상 발화해야 하고, 각 cron 의 기존 동작 (SPEC-016/018/019/020/022/023 의 검증된 행동) 이 회귀 없이 유지되어야 한다

#### AC-024-BC-2 — 일일 매매 한도

- **Given**: 일일 매매 한도가 10 건으로 설정되어 있으면
- **When**: 본 SPEC 의 모든 trigger (price threshold, volume anomaly, position watchdog, blocked-release) 가 종합적으로 11 건 이상의 매매 시도를 발생시키면
- **Then**: 11 번째 매매부터는 일일 한도 초과로 차단되고, `trade_attempts` 테이블에 daily_limit_exceeded 사유로 기록되어야 한다

#### AC-024-BC-3 — Safety check 우회 금지

- **Given**: SPEC-018 단기과열 차단 상태의 ticker 가 본 SPEC 의 어느 trigger 든 발화하면
- **When**: 해당 trigger 가 매매 decision 단계에 진입하면
- **Then**: SPEC-018 의 safety check 이 우회 없이 평가되어야 하고, 차단 상태인 경우 매매가 거절되어야 한다

---

## Test Strategy

### Unit Tests

- `tests/watchers/test_price_threshold.py`: threshold 계산, cooldown, daily cap
- `tests/watchers/test_volume_anomaly.py`: 20-day average 계산, ATR 임계
- `tests/watchers/test_blocked_release.py`: KIS stat_cls polling mock
- `tests/watchers/test_position_watchdog.py`: PnL 변화 감지, no-op fast path, stop-loss/take-profit 평가
- `tests/streams/test_kis_websocket.py`: 재접속 backoff, dedup, heartbeat
- `tests/personas/test_multi_tier_dispatch.py`: tier escalation, throttle, safety check 통과 검증
- `tests/billing/test_cost_monitor.py`: 80% warning, 100% circuit breaker, daily reset

### Integration Tests

- 1 영업일 paper smoke test (Phase 1 완료 시): adaptive cron 9~14 회 정상, threshold trigger 동작, 기존 cron 회귀 없음
- 1 영업일 paper smoke test (Phase 2 완료 시): WebSocket 연결 유지, position watchdog 동작, multi-tier dispatch 정상, cost monitor 추적 정상

### Regression Tests

- 기존 SPEC-016 / 018 / 019 / 020 / 022 / 023 의 전체 test suite 가 본 SPEC 배포 전후 모두 green 이어야 함

---

## Quality Gates

- 모든 신규 코드 test coverage ≥ 85%
- TRUST 5 framework 통과 (Tested, Readable, Unified, Secured, Trackable)
- `ruff check` + `black` 통과
- `mypy --strict` 통과 (가능한 범위 내)
- KIS API rate limit (모의투자 1초 5회) 모든 테스트에서 우회 없음
- LLM 일일 비용 ≤ 10,000 KRW (Phase 3 실측 기준)

---

## Verification Tools

- pytest (unit + integration)
- Docker compose 통합 환경에서 1 영업일 paper smoke test
- KIS API mock + WebSocket mock server (`pytest-asyncio`)
- Anthropic API mock (Tier 1 Haiku, Tier 2/3 Sonnet) for cost monitor 검증
- `.moai/reports/spec-024-validation-2026-06/report.md` 1 주 운영 보고서

---

## Acceptance Sign-off

- Phase 1 완료 후: AC-024-1 ~ 4 + AC-024-BC-1, 2, 3 통과 확인
- Phase 2 완료 후: AC-024-5 ~ 9 + 회귀 테스트 통과 확인
- Phase 3 완료 후: 1 주 무사고 paper 운영 + 비용 budget 내 유지 + threshold 튜닝 적용 확인
