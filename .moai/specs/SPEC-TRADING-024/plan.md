---
id: SPEC-TRADING-024
type: plan
status: draft
created: 2026-05-15
---

# SPEC-TRADING-024 -- Implementation Plan

## Strategic Approach

본 SPEC 은 시간 고정 cron → 이벤트 기반 자율 트레이딩으로의 architectural evolution 이다. 3 phase 로 나누어 점진적으로 도입하여 회귀 위험을 최소화한다.

### Core Architecture Decision Records

#### ADR-024-1: 기존 4 cron 유지 + adaptive layer 추가

**결정**: 기존 09:30/11:00/13:30/14:30 intraday cron 은 그대로 유지하고, 그 위에 15 분 간격 adaptive cron 을 추가. 두 layer 가 같은 시간에 중복 발화하지 않도록 dedup 처리.

**Why**: backward compatibility. SPEC-016 / 018 / 019 / 020 / 022 / 023 의 모든 검증된 동작이 기존 4 cron 기준이므로 이를 제거하면 회귀 위험 高. adaptive layer 는 부가 가치만 추가.

**Trade-off**: dedup 로직 추가 부담. 그러나 회귀 위험 < dedup 비용.

#### ADR-024-2: In-process event bus (asyncio.Queue) → 미래 Redis 확장

**결정**: Stage 2 event bus 는 `asyncio.Queue` 기반 in-process pub/sub. cross-process 가 필요해지면 Redis 로 확장.

**Why**: paper 단계에서 single-process 충분. Redis 도입은 운영 부담 + 추가 의존성. YAGNI 원칙.

#### ADR-024-3: Multi-tier dispatcher (Haiku → Sonnet → Sonnet+Risk)

**결정**: 모든 trigger 는 Tier 1 (Haiku, ~$0.001) 을 거쳐 filter 된 후 Tier 2 (Sonnet micro), Tier 3 (Sonnet decision/risk) 으로 escalate.

**Why**: 비용 통제. 매 tick 에 Sonnet 호출 시 일 비용이 budget 을 수 배 초과. Haiku 가 95% noise 를 흡수.

#### ADR-024-4: Cost circuit breaker 는 hard stop 대신 tier 강등

**결정**: 100% budget 도달 시 threshold trigger 비활성화 + cron baseline 만 유지 (hard stop 아님).

**Why**: 운영 안전성 우선. 시장 시간 중 hard stop 시 holdings 의 stop-loss 도 멈추므로 위험. tier 강등은 critical safety 는 유지.

---

## Phase 1 -- Adaptive Cron + Threshold Triggers (1 주)

### Scope
REQ-024-1, REQ-024-2, REQ-024-3, REQ-024-4

### Pre-conditions

- SPEC-022 / 023 의 4 일 (2026-05-15 ~ 2026-05-19 KST) paper-trading 무사고 운영 게이트 통과
- `.moai/config/sections/scheduler.yaml` 작성 가능 상태

### Milestones (priority-based, no time estimates)

#### Primary Goal: Adaptive cron 동작
- `.moai/config/sections/scheduler.yaml` 신설 (또는 기존 확장): `intraday_interval_minutes`, threshold/cap 키
- `src/trading/scheduler/runner.py` 수정: adaptive cron 추가 + dedup logic
- 기존 4 cron 동작 회귀 없음을 통합 테스트로 확인

#### Secondary Goal: Price threshold watcher
- `src/trading/watchers/__init__.py`, `src/trading/watchers/price_threshold.py`
- holdings ∪ dynamic_tickers ∪ recent micro candidates 통합 ticker 집합 산출 함수
- ticker 당 cooldown (5 분), 일일 cap (20) 의 throttle 구현
- DB migration: `tick_events`, `trigger_log` 테이블 (alembic)

#### Tertiary Goal: Volume anomaly watcher
- `src/trading/watchers/volume_anomaly.py`
- 20-day average 거래량 + ATR 기반 anomaly 감지
- REQ-024-2 의 throttle 와 통합

#### Optional Goal: Blocked-release watcher
- `src/trading/watchers/blocked_release.py`
- KIS `stat_cls` 5 분 polling
- 단기과열 release 감지 시 re-evaluation 사이클 발화

### Validation
- Manual smoke test: paper 모드, 1 영업일 운영
- 통합 테스트: `tests/watchers/test_price_threshold.py`, `test_volume_anomaly.py`, `test_blocked_release.py`
- 회귀 테스트: 기존 5 개 cron 시간대 모두 정상 발화

### Risks
- 기존 cron 과의 dedup 누락 → 같은 시간 중복 발화 → KIS rate limit 초과
  - 대응: dedup unit test, runner.py 의 `last_executed_at` 추적
- threshold trigger 가 과민 → daily cap 빠르게 소진 → 후속 신호 무시
  - 대응: Phase 3 튜닝, 또는 ATR 기반 동적 threshold

---

## Phase 2 -- WebSocket Stream + Position Watchdog + Multi-tier Dispatch (2 주)

### Scope
REQ-024-5, REQ-024-6, REQ-024-7, REQ-024-8, REQ-024-9

### Pre-conditions

- Phase 1 의 1 주 무사고 운영 완료
- KIS WebSocket auth / 구독 한도 사전 검증 완료 (Open Question Q-1, Q-2 해소)

### Milestones (priority-based)

#### Primary Goal: KIS WebSocket client
- `src/trading/streams/__init__.py`, `src/trading/streams/kis_websocket.py`
- websockets (Python 3.13 asyncio) 기반 client
- heartbeat 30s, exponential backoff (1, 2, 4, 8, max 60), dedup
- `src/trading/streams/event_bus.py`: in-process `asyncio.Queue` 기반 pub/sub
- top-N 구독 대상 산출: holdings 우선 + dynamic_universe activity 상위 (총 ≤ 40)

#### Primary Goal: Multi-tier dispatcher
- `src/trading/personas/dispatcher.py`: Tier 1 → 2 → 3 직렬, 각 tier 진입 전 safety check 우회 없음
- `src/trading/personas/haiku_watcher.py`: ~2K token prompt ("signal/noise?"), Anthropic Haiku API call
- Throttle: ticker 당 Tier-3 = 5 분 1회, 일 Tier-2 = 50 회

#### Secondary Goal: Position watchdog
- `src/trading/watchers/position_watchdog.py`
- holding ticker 의 tick event 평가 (PnL ±3%, volume surge, 뉴스 impact 5)
- SPEC-016 RSI 기반 stop-loss/take-profit 매 tick 평가 + no-op fast path

#### Secondary Goal: Cost monitor + circuit breaker
- `src/trading/billing/cost_monitor.py`
- DB migration: `llm_cost_log` (cycle_kind, persona_name, model, input_tokens, output_tokens, cost_krw, ts)
- 80% / 100% threshold 동작
- Daily reset (00:00 KST)

#### Tertiary Goal: News continuous mode
- `src/trading/watchers/news_continuous.py`
- 기존 News Intelligence (SPEC-013/014) 의 함수 시그너처 변경 없이 flag 만 추가
- 5 분 polling, holdings ∪ dynamic_tickers keyword 매칭 + impact ≥ 4 시 trigger 발화

### Validation
- `tests/streams/test_kis_websocket.py`: 재접속, dedup, heartbeat
- `tests/watchers/test_position_watchdog.py`: PnL 변화, no-op fast path
- `tests/personas/test_multi_tier_dispatch.py`: tier escalation, throttle
- `tests/billing/test_cost_monitor.py`: 80%/100% threshold, daily reset
- 통합 smoke test: paper 모드, 1 영업일 (Phase 2 전체 흐름)

### Risks
- KIS WebSocket 인증 실패 / 한도 초과
  - 대응: Q-1, Q-2 의 사전 검증. fallback = WebSocket 비활성화 시 Phase 1 동작 (REST polling 기반 threshold trigger) 으로 자동 강등
- LLM 비용 폭주
  - 대응: Tier 1 Haiku 가 filter 역할 충실히 수행하는지 Phase 3 측정. 비상 시 cost circuit breaker 자동 발동
- Position watchdog 의 false positive 가 일 매매 한도 (10건) 소진
  - 대응: paper 단계에서 threshold tuning, real 전환 시 별도 SPEC 으로 검토

---

## Phase 3 -- Live Observation + Tuning (1 주)

### Scope
전체 REQ. 실측 기반 튜닝.

### Milestones

#### Primary Goal
- 1 주 paper 운영 데이터 수집: 사이클 수, trigger 발화 횟수, persona tier 분포, 비용 분포
- threshold (price ±%, volume multiplier, daily cap, budget cap) 실측 기반 튜닝
- `.moai/config/sections/scheduler.yaml` 업데이트

#### Secondary Goal
- 운영 보고서 작성: `.moai/reports/spec-024-validation-2026-06/report.md`
- 다음 SPEC (SPEC-025?) 후보 식별 (예: news 강화, real 모드 전환 준비)

### Validation
- 1 주 paper 운영 무사고
- 일일 LLM 비용 ≤ 10,000 KRW
- 회귀 없음 (기존 5 cron + 기존 5 persona 정상)

---

## Cross-Phase Quality Gates

- 모든 phase 에서 SPEC-018 단기과열 차단 우회 없음
- 모든 tier 진입 전 safety check 통과
- 일일 매매 한도 (10건) 변경 없음
- mode=paper default 유지 (real 전환은 SPEC-017 책임)
- KIS API rate limit (모의투자 1초 5회) 절대 우회 금지
- 모든 신규 코드는 TDD (RED-GREEN-REFACTOR) 또는 DDD (ANALYZE-PRESERVE-IMPROVE) 적용
- 85% 이상 test coverage

---

## Implementation Hints (for manager-tdd / manager-ddd, NOT for manager-spec)

- **WebSocket library**: `websockets` (Python 3.13 native asyncio support)
- **Event bus**: `asyncio.Queue` per watcher; cross-process 가 필요해지면 Redis 로 확장
- **Throttle**: token bucket per ticker (memory 기반으로 시작)
- **Cost tracking**: append-only `llm_cost_log` + 일별 aggregate materialized view
- **Haiku watcher prompt**: ~2K token, "signal/noise? yes/no + reason" 단순 binary classification
- **Reconnect**: exponential backoff 1, 2, 4, 8, max 60s + heartbeat 30s

---

## Dependencies and Sequencing

- Phase 1 → Phase 2 → Phase 3 (직렬). 각 phase 의 무사고 운영 1 주 검증 후 다음 phase 진입.
- 모든 phase 이전에 SPEC-022/023 의 게이트 통과 필수 (~2026-05-19 KST 목표)
- Phase 2 직전 KIS WebSocket auth / 구독 한도 사전 검증 (Q-1, Q-2)
