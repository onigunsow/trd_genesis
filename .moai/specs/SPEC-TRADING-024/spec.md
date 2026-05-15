---
id: SPEC-TRADING-024
version: 0.1.0
status: draft
created: 2026-05-15
updated: 2026-05-15
author: onigunsow
priority: high
issue_number: 0
domain: TRADING
title: "이벤트 기반 자율 트레이딩 — 실시간 시장 모니터링 + 적응형 페르소나 호출"
related_specs:
  - SPEC-TRADING-016
  - SPEC-TRADING-018
  - SPEC-TRADING-019
  - SPEC-TRADING-020
  - SPEC-TRADING-021
  - SPEC-TRADING-022
  - SPEC-TRADING-023
---

# SPEC-TRADING-024 -- 이벤트 기반 자율 트레이딩 (실시간 시장 모니터링 + 적응형 페르소나 호출)

## HISTORY

| Date | Version | Changes | Author |
|---|---|---|---|
| 2026-05-15 | 0.1.0 | Initial planning draft — 9 EARS requirements, 3-phase rollout (Stage 1: adaptive cron + threshold triggers, Stage 2: WebSocket stream + position watchdog, Stage 3: 1주 validation). 구현은 SPEC-022/023 의 4일 (5/15~5/19 KST) 연속 paper-trading 무사고 운영 게이트 통과 후 착수 | onigunsow |
| 2026-05-15 | 0.2.0 | Stage 1 (REQ-024-1~4) 배포 완료 (main `31b0c83`, redeploy 16:07 KST). REQ-024-8 (Multi-tier persona) 에 **Hybrid Execution Mode** 결정 추가: Tier-1 Haiku watcher 는 직접 Anthropic API 호출 (subprocess overhead 회피 + 분단위 폴링 가능), Tier-2/Tier-3 Sonnet 페르소나는 기존 cli_only_mode 유지 (SPEC-015/016 호환). 사용자 의사결정 — 2026-05-15 16:12 KST | onigunsow |

---

## Scope Summary

본 SPEC 은 현재 **시간 고정 cron 기반** (pre_market 07:30 + intraday 09:30/11:00/13:30/14:30 KST = 일 5회) 으로 동작하는 paper-trading 시스템을 **이벤트 기반 자율 트레이딩** 으로 진화시키는 전략적 multi-phase planning SPEC 이다. 본 SPEC 자체는 **planning-only** 이며, 실제 구현은 SPEC-022 (universe discovery) 와 SPEC-023 (universe auto-expansion) 의 4일 (5/15~5/19 KST) paper-trading 연속 무사고 운영 게이트 통과 후 착수한다.

### 위치 및 직교성

- **SPEC-016 Phase 1 (완료, 2026-05-10 21:38 redeploy, 5/5 healthcheck 통과)**: 인프라/CLI/Jinja 정합성 안정화
- **SPEC-017 (미시작, KRX 실거래 전환)**: 본 SPEC 의 이벤트 기반 코드는 paper/real 양쪽에서 동작하도록 설계되어야 함 (mode=paper 가 default)
- **SPEC-018 / 019 / 020 (모두 2026-05-12 merged)**: micro persona blocked-ticker awareness, data refresh layer, DEFAULT_WATCHLIST 편향 제거 — 본 SPEC 의 watcher 가 재사용
- **SPEC-021 (미시작, US 시장 통합 planning)**: Stage 2 의 KIS WebSocket 추상화는 US 브로커 (예: Polygon, Alpaca) 어댑터로 교체 가능하도록 설계 권장
- **SPEC-022 / 023 (paper-trading 검증 중, gate 통과 시점 = 2026-05-19 KST 목표)**: dynamic universe discovery 및 auto-expansion — 본 SPEC 의 WebSocket 구독 대상 (top-N) 산출에 직접 사용
- **SPEC-024 (본 SPEC, P0 strategic evolution)**: 시간 고정 cron → 이벤트 기반 적응형 cron + WebSocket stream + 다중 tier persona dispatch

본 SPEC 은 SPEC-001 ~ SPEC-023 의 **모든 기존 동작과 완전히 직교 (additive only)** 한다: 기존 4개 intraday cron (09:30/11:00/13:30/14:30) 과 pre_market cron (07:30) 은 Phase 1~3 어느 시점에서도 그대로 유지되며, 본 SPEC 의 adaptive cron 및 watcher 는 추가 layer 로 동작한다.

### 비즈니스 임팩트 및 사용자 의도

사용자 (박세훈) 의 명시적 declaration (3 회):

1. (2026-05-12) "종목을 내가 정하는게 아니라 시스템에서 추천 하고 감시한 다음에 매수 시점을 추천하는게 맞지않나?"
2. AGGRESSIVE CHALLENGE 목표 (월 10~20% 성장)
3. (2026-05-15) "지금은 모듈들이 정해진 시간에만 움직이는 중인데 자율적으로 시장정보를 수집하고 움직이게 개선가능할까?"

현재 5 회 cron 간 gap (1.5~2 시간) 동안 시장 이벤트가 누적되어 다음 cron 까지 대기. 본 SPEC 으로 gap → 분 단위 응답성 확보, 단 박세훈 페르소나의 **mid-term horizon (NOT day-trading)** 및 **자본 보전 우선** 원칙은 유지.

### 핵심 아키텍처 진화

- **Before**: 시간 고정 cron (일 5회) → 정해진 시간에만 micro/macro/decision/risk/portfolio persona 직렬 실행
- **After Stage 1**: adaptive cron (일 9~14회, 15~30분 간격) + price/volume threshold trigger → 시장 이벤트 시 60초 내 응답
- **After Stage 2**: KIS WebSocket tick stream + position watchdog + multi-tier dispatcher (Haiku → Sonnet micro → Sonnet decision/risk) → 진정한 이벤트 기반 자율 운영

---

## Environment

- 기존 SPEC-001 ~ SPEC-023 인프라 (Docker compose, Postgres 16-alpine, Telegram dev/cron/trading bot 분리, KIS API mock)
- 기존 5-persona 시스템 (Macro/Micro/Decision/Risk/Portfolio) — 본 SPEC 에서 prompt 또는 invocation logic 은 변경 없음. 단, **호출 시점 및 빈도** 가 적응형으로 진화
- 기존 cron scheduler: `src/trading/scheduler/runner.py` — 본 SPEC 에서 adaptive cron 추가 + watcher 부팅 hook 추가, **기존 5 개 cron 시간표는 변경 없음**
- 기존 KIS REST API client: `src/trading/kis/*` — 본 SPEC 에서 WebSocket client 신설 (`src/trading/streams/kis_websocket.py`), REST 클라이언트는 변경 없음
- 기존 dynamic universe (SPEC-022/023): `get_dynamic_universe()` — 본 SPEC 의 WebSocket 구독 대상 top-N 산출에 직접 사용
- 기존 News Intelligence pipeline (SPEC-013/014): 6 회/일 batch news_crawl — 본 SPEC 에서 "continuous mode" flag 추가 (5 분 polling)
- 기존 safety check (SPEC-018 단기과열 차단, SPEC-016 자본 보전 규칙): 본 SPEC 의 모든 tier 진입 직전에 우회 없이 통과해야 함
- LLM 비용 모델: Haiku ~$0.001/call, Sonnet ~$0.05/call (heavy persona via CLI bridge) — 본 SPEC 의 multi-tier dispatch 가 비용 통제
- KIS API rate limit: 모의투자 1초 5 회 — adaptive cron 및 watcher 가 rate limit 우회 시도 절대 금지
- 신규 모듈 (Phase 1 ~ 2 에 걸쳐 구현 — 본 SPEC 은 정의만):
  - `src/trading/streams/__init__.py`, `src/trading/streams/kis_websocket.py`, `src/trading/streams/event_bus.py`
  - `src/trading/watchers/__init__.py`, `src/trading/watchers/price_threshold.py`, `src/trading/watchers/volume_anomaly.py`, `src/trading/watchers/blocked_release.py`, `src/trading/watchers/position_watchdog.py`, `src/trading/watchers/news_continuous.py`
  - `src/trading/personas/dispatcher.py`, `src/trading/personas/haiku_watcher.py`
  - `src/trading/billing/cost_monitor.py`
  - `.moai/config/sections/scheduler.yaml` (신규 or 확장)
  - 신규 DB 테이블: `tick_events`, `trigger_log`, `llm_cost_log`

---

## Assumptions

- A-1: KIS WebSocket API 가 안정적 (heartbeat, 재접속) — 실제 검증은 Phase 2 착수 시 수행 (Open Question Q-1 참조)
- A-2: 한 세션 (09:00 ~ 15:30 KST) 내 WebSocket 구독 대상 ticker 수는 ≤ 40 (KIS API 문서 기준; 초과 시 우선순위 회전 정책 적용 — Q-2 참조)
- A-3: LLM 일일 예산 10,000 KRW (≈ $7) 가 paper trading 단계에서 적절 — 실측 후 Phase 3 에서 재조정 (Q-3)
- A-4: 박세훈 페르소나의 mid-term horizon (수일 ~ 수주) 은 본 SPEC 후에도 유지. **본 SPEC 은 day-trading 으로의 전환이 아니며**, 단지 진입/청산 시점의 정밀도를 분 단위로 향상시키는 것
- A-5: 기존 5 개 cron 은 backward compatibility 를 위해 유지 (Q-8 의 권장 옵션 채택). adaptive cron 은 기존 cron 의 **상위 집합** 으로 동작 (기존 시간대도 포함)
- A-6: paper 모드에서는 stop-loss / take-profit 자동 집행 허용. real 모드 (SPEC-017 이후) 에서는 notify-only 모드로 시작 (Q-6 참조)
- A-7: 일일 매매 한도 (현재 10건/일) 는 본 SPEC 에서 변경하지 않음. threshold trigger 가 한도를 초과하는 신호를 발생시키면 큐잉 후 다음 날 처리 또는 무시

---

## Requirements (EARS)

### Stage 1: Adaptive Cron + Threshold Triggers (Phase 1)

#### REQ-024-1 (P0, Event-driven) — Adaptive intraday cron 밀도 증가

**WHEN** scheduler 가 시작되고 시장 시간 (09:00~15:30 KST) 에 진입하면, **THEN** 시스템은 `intraday_interval_minutes` (default 15, 설정 가능 via `.moai/config/sections/scheduler.yaml`) 간격으로 `run_intraday_cycle` 을 실행해야 한다. 하루 9 ~ 14 회 사이클이 실행되며, 각 사이클은 SPEC-019 의 cached micro/macro 데이터를 재사용한다. 기존 4 개 intraday cron (09:30/11:00/13:30/14:30) 시간대도 adaptive cron 의 한 instance 로 흡수되며, 별도 직렬 실행으로 인한 중복은 발생하지 않아야 한다.

#### REQ-024-2 (P0, Event-driven) — Price-threshold persona trigger

**WHEN** monitored ticker (holdings ∪ dynamic_tickers ∪ recent micro candidates) 의 가격이 ±N% (default 2%, 설정 가능) 를 M 분 (default 30, 설정 가능) 내 움직이면, **THEN** 시스템은 해당 ticker subset 대상으로 lightweight 사이클 (micro Haiku → Haiku 신호 시 Sonnet decision → safety check 의 직렬 흐름) 을 60 초 내 실행해야 한다. 일일 cap = max 20 threshold-triggered 사이클 (runaway 방지).

#### REQ-024-3 (P0, Event-driven) — Volume + volatility anomaly trigger

**WHEN** monitored ticker 의 거래량이 20-day average 의 2 배 초과 **AND** ATR 이 typical 값의 1.5 배 초과를 동시 만족하면, **THEN** 시스템은 해당 ticker 대상 분석 사이클을 발화해야 한다. REQ-024-2 와 동일한 throttling (일 20회 cap, ticker 당 5분 cooldown) 을 적용한다.

#### REQ-024-4 (P1, Event-driven) — Blocked-release watcher

**WHEN** 단기과열 차단 (SPEC-018) 상태의 ticker 에 대해 KIS `stat_cls` 폴링이 release 를 감지하면 (5 분 간격 폴링, 시장 시간대만), **THEN** 시스템은 release 사실을 로그에 기록하고 해당 ticker 의 re-evaluation 사이클을 즉시 발화해야 한다.

### Stage 2: Real-time Stream Integration (Phase 2)

#### REQ-024-5 (P0, State-driven) — KIS WebSocket 실시간 가격 스트림

**IF** scheduler 가 active 이고 시장 시간이며 WebSocket 모듈이 enabled 면, **THEN** 시스템은 KIS WebSocket 에 top-N tickers (default 40 = KIS 한도; holdings 우선 고정 + dynamic_universe 의 activity 상위 30) 를 구독하고, heartbeat 30 초 간격 유지, disconnect 시 exponential backoff (1s, 2s, 4s, 8s, max 60s) 재접속, 중복 tick dedup 을 보장해야 한다. 새 모듈: `src/trading/streams/kis_websocket.py`.

#### REQ-024-6 (P1, Event-driven) — News stream continuous mode

**WHEN** continuous mode flag 가 활성화되면, **THEN** 시스템은 기존 6x/일 batch 외에 5 분 간격 (설정 가능) 으로 news source 를 polling 하며, holdings ∪ dynamic_tickers 의 keyword 와 일치 AND `impact >= 4` 인 뉴스 감지 시 ticker-specific analysis trigger 를 발화해야 한다. 기존 News Intelligence pipeline (SPEC-013/014) 의 함수 시그너처는 변경하지 않고 flag 만 추가한다.

#### REQ-024-7 (P0, Event-driven) — Position watchdog

**WHEN** 각 holding ticker 에 대해 tick event 가 도착하면, **THEN** position watchdog 은 다음 조건들을 평가하고 충족 시 decision persona 를 자동 발화해야 한다: (a) Unrealized PnL 이 마지막 평가 대비 ±N% (default ±3%) 변화, (b) holding 세션 중 volume surge, (c) impact 5/5 뉴스가 holding ticker 와 매칭. SPEC-016 Phase 1 의 RSI 기반 stop-loss / take-profit 사전 규칙은 매 tick 마다 평가되며, no-op fast path 를 통해 변화 없는 tick 에서는 persona 호출이 발생하지 않아야 한다.

### Stage 2 supporting infrastructure

#### REQ-024-8 (P0, Ubiquitous) — Multi-tier persona invocation

시스템은 항상 다음 3-tier dispatcher 를 통해 persona 를 호출해야 한다:

- **Tier 1 (always on)**: lightweight Haiku watcher 가 stream event 를 읽어 cheap heuristic filter ("signal/noise?") 를 적용
- **Tier 2 (on Tier-1 pass)**: Sonnet micro persona 가 ticker-specific 분석 (~5K tokens)
- **Tier 3 (on Tier-2 signal)**: Sonnet decision + risk persona 가 full 평가 (~50K tokens)

Throttle 규칙: ticker 당 Tier-3 = 5 분에 1회 max, 일일 Tier-2 = 50회 max. 모든 tier 의 직전에 SPEC-018 단기과열 차단 등 safety check 를 우회 없이 통과해야 한다.

##### REQ-024-8.1 — Hybrid Execution Mode (사용자 결정 2026-05-15)

**Tier 1 (Haiku watcher) 은 직접 Anthropic API 호출**, Tier 2/3 (Sonnet 페르소나) 은 기존 `cli_only_mode` (SPEC-015/016) 를 유지한다.

**근거**:
- Tier 1 은 분단위 폴링 (또는 stream 이벤트당) 발사되므로 `claude` CLI subprocess spawn overhead (~10~30s) 가 누적되면 실시간성 손상.
- 직접 API 호출 시 응답 latency ~2~5s 로 단축, 거의 무료 (Haiku 모델 가격 매우 저렴: $0.25/$1.25 per 1M tokens).
- Tier 2/3 은 기존 SPEC-015 REQ-015-1 `block_if_cli_only_mode` 데코레이터 위반 우려가 있어 CLI bridge 를 그대로 사용.
- 결과적으로 cli_only_mode 자체는 "Sonnet 페르소나만 CLI 의무화" 로 의미가 축소된다 — Haiku 는 적용 대상 외.

**구현 요구**:
- Tier 1 모듈은 `anthropic` Python SDK 를 직접 호출 (env: `ANTHROPIC_API_KEY` 사용)
- Tier 2/3 호출 코드는 변경 없음 (기존 `trading.personas.base.call_persona_via_cli` 그대로)
- `block_if_cli_only_mode` 데코레이터는 Haiku Tier-1 코드 경로에는 적용하지 않는다 (allowlist 추가)
- 비용 모니터 (REQ-024-9) 는 Haiku API 호출 토큰 사용량도 함께 집계해야 한다

#### REQ-024-9 (P1, Ubiquitous) — Cost monitor + circuit breaker

시스템은 항상 LLM 호출 비용 (Haiku/Sonnet) 을 real-time 으로 `llm_cost_log` 테이블에 append-only 로 기록하고, 일일 예산 (default 10,000 KRW, 설정 가능) 대비 사용량을 추적해야 한다. **WHEN** 일일 사용량이 80% 도달하면, **THEN** Tier-2/3 호출을 throttle 한다. **IF** 100% 도달하면, **THEN** threshold trigger 를 비활성화 (cron baseline 만 유지) 하고 다음 영업일 00:00 KST 에 자동 reset 한다.

---

## Specifications

### Phase 1 (Stage 1, 1 주 목표): Adaptive cron + threshold triggers

대상 REQ: REQ-024-1, 2, 3, 4

설계 원칙: WebSocket 없이도 동작 가능한 baseline. 외부 의존성 최소 (REST polling + 내부 timer). manual smoke test (paper 모드, 1 영업일) 으로 검증.

핵심 산출:
- `.moai/config/sections/scheduler.yaml` (또는 기존 확장): `intraday_interval_minutes`, `price_threshold_pct`, `volume_anomaly_multiplier`, `daily_trigger_cap`, `blocked_release_poll_interval`
- `src/trading/watchers/price_threshold.py`, `src/trading/watchers/volume_anomaly.py`, `src/trading/watchers/blocked_release.py`
- `src/trading/scheduler/runner.py` 수정: adaptive cron 추가 (기존 5 개 cron 유지), watcher 부팅 hook
- DB migration: `tick_events`, `trigger_log` 테이블
- 테스트: `tests/watchers/test_price_threshold.py`, `tests/watchers/test_volume_anomaly.py`, `tests/watchers/test_blocked_release.py`

### Phase 2 (Stage 2, 2 주 목표): WebSocket stream + position watchdog + multi-tier dispatch

대상 REQ: REQ-024-5, 6, 7, 8, 9

설계 원칙: Phase 1 안정화 검증 후 착수. KIS WebSocket 인증/한도 사전 검증 필수 (Q-1, Q-2). 내부 event bus 는 `asyncio.Queue` 기반 in-process pub/sub 으로 시작 (cross-process 가 필요해지면 Redis 로 확장).

핵심 산출:
- `src/trading/streams/kis_websocket.py`, `src/trading/streams/event_bus.py`
- `src/trading/watchers/position_watchdog.py`, `src/trading/watchers/news_continuous.py`
- `src/trading/personas/dispatcher.py`, `src/trading/personas/haiku_watcher.py`
- `src/trading/billing/cost_monitor.py`
- DB migration: `llm_cost_log` 테이블 (cycle_kind, persona_name, model, input_tokens, output_tokens, cost_krw, ts)
- 테스트: `tests/streams/test_kis_websocket.py`, `tests/watchers/test_position_watchdog.py`, `tests/personas/test_multi_tier_dispatch.py`, `tests/billing/test_cost_monitor.py`

### Phase 3 (1 주 목표): Live observation + threshold tuning

대상 REQ: 전체

설계 원칙: 1 주 paper 운영 데이터 수집. threshold (price ±%, volume multiplier, daily cap, budget cap) 의 실측 기반 튜닝. cost monitor 가 budget 내 유지되는지 확인.

핵심 산출:
- threshold 튜닝 결과 적용 (`scheduler.yaml` 업데이트)
- 1 주 운영 보고서 (.moai/reports/spec-024-validation-2026-06/) — 사이클 수, trigger 발화 횟수, persona 호출 분포, 비용 분포

### Non-goals (Out of Scope)

- 실거래 모드 전환 (SPEC-017 의 책임)
- 미국 시장 통합 (SPEC-021 의 책임). 단 Stage 2 의 stream 추상화는 US 어댑터 swap 가능하도록 인터페이스 분리 권장
- 옵션 / 파생 상품
- High-frequency trading (sub-second decision)
- Day-trading 모드 (박세훈 페르소나의 mid-term horizon 유지)
- 강화학습 / 모델 fine-tuning
- Multi-user / multi-account 지원
- Mobile / web 대시보드 (CLI / log 만)

---

## Dependencies and Rollout

### Dependencies on other SPECs

- **BLOCKED-UNTIL**: SPEC-022 (universe discovery) 및 SPEC-023 (universe auto-expansion) 의 4 일 (2026-05-15 ~ 2026-05-19 KST) 연속 paper-trading 무사고 운영 게이트 통과. 본 SPEC 의 watcher 가 dynamic universe 를 의존하므로 universe 가 안정화되지 않으면 watcher 가 무의미.
- **RELATED**: SPEC-021 (US 시장) — Stage 2 의 WebSocket 추상화 인터페이스는 SPEC-021 의 미국 브로커 어댑터 (Polygon / Alpaca) 와 swap 가능하도록 설계
- **COMPATIBLE-WITH**: SPEC-016 / 018 / 019 / 020 / 022 / 023 — 모두 backward compatibility 유지. 기존 동작 무회귀

### Rollout Plan

- **Pre-deploy gate (~2026-05-19 KST)**: SPEC-022/023 의 4 일 paper-trading 무사고 운영 완료 + KRX 시장 안정성 확인
- **Phase 1 (1 주)**: Stage 1 (REQ-024-1~4). Adaptive cron + threshold trigger 만. WebSocket 없음. paper 모드 manual smoke test.
- **Phase 2 (2 주)**: Stage 2 (REQ-024-5~9). WebSocket + position watchdog + multi-tier dispatch + cost monitor. 통합 테스트 필수.
- **Phase 3 (1 주)**: 1 주 live 데이터 관찰, threshold 튜닝, cost budget 내 유지 확인
- **Total calendar**: 4 주 (gate 통과 후 착수 기준)

---

## Open Questions (Flag Only, 미해결)

- **Q-1 (WebSocket auth)**: KIS WebSocket 인증 방식이 REST 와 동일한가? manager-tdd 가 Phase 2 착수 시점에 KIS 공식 문서 (developers.koreainvestment.com) 검증 필요
- **Q-2 (Top-N 한도)**: KIS WebSocket 구독 한도 = 40 ticker (문서 기준). dynamic universe 가 40 초과 시 회전 전략 (시간 단위 회전 vs holdings 우선 고정 + 나머지 회전) 결정 필요
- **Q-3 (LLM 예산)**: 일일 10,000 KRW 가 paper 단계에서 적절한지? Haiku 호출 빈도 (예상 일 200~500회) × $0.001 = $0.2 ~ $0.5 → 충분. Sonnet 호출 빈도 가 변동성 高. Phase 3 실측 후 재조정
- **Q-4 (Price threshold)**: ±2% 가 변동성 큰 세션에서 과민한가? ATR 기반 동적 threshold 로 교체 검토 (Phase 3 튜닝 시점에 결정)
- **Q-5 (News polling 5분)**: source 서버 부하 우려. 기존 6x/일 batch + supplement 형태로 시작 (별도 stream 보강 = continuous 가 아니라 batch 강화). Phase 2 설계 시점 재검토
- **Q-6 (Stop-loss 자동화 수준)**: paper 모드는 full auto 안전. real 모드 (SPEC-017 이후) 는 notify-only 로 시작 권장. 본 SPEC 의 REQ-024-7 은 paper 기준이며 real 전환 시 별도 SPEC 으로 분리
- **Q-7 (Circuit breaker 행동)**: 100% budget 도달 시 hard stop vs tier 강등? 본 SPEC 은 tier 강등 + threshold trigger 비활성화 방식 채택 (안전성 우선)
- **Q-8 (기존 4 cron 처리)**: parallel keep vs replace? 본 SPEC 은 keep (REQ-024-1 의 adaptive cron 이 기존 시간대를 흡수하되, 명시적 기존 cron 도 backward compat 위해 유지). adaptive cron 이 같은 시간에 중복 발화하지 않도록 dedup 필요 (구현 책임)

---

## Acceptance & Traceability

자세한 acceptance criteria 는 `acceptance.md` 참조.
구현 계획 및 milestone 은 `plan.md` 참조.

본 SPEC 은 SPEC-022 및 SPEC-023 의 게이트 통과 (~2026-05-19 KST) 후 `/moai:2-run SPEC-TRADING-024` 로 implementation 진입한다. Status 는 그때까지 `draft` 유지.
