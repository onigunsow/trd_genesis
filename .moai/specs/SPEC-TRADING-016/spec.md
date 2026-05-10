---
id: SPEC-TRADING-016
version: 0.1.0
status: draft
created: 2026-05-10
updated: 2026-05-10
author: onigunsow
priority: critical
issue_number: 0
domain: TRADING
title: "긴급 안정화 + Regime Awareness + 불장 모드 (3-Phase)"
related_specs:
  - SPEC-TRADING-015
  - SPEC-TRADING-014
  - SPEC-TRADING-013
  - SPEC-TRADING-009
  - SPEC-TRADING-008
  - SPEC-TRADING-001
---

# SPEC-TRADING-016 -- 긴급 안정화 + Regime Awareness + 불장 모드 (3-Phase)

## HISTORY

| Date | Version | Changes | Author |
|---|---|---|---|
| 2026-05-10 | 0.1.0 | Initial draft -- 3 phases, 10 EARS requirements, post-mortem 기반 | onigunsow |

---

## Scope Summary

지난 2주간 두 차례 zero-trade 사고(1차 fix `adeadeb`, 2차 fix `2172cdf`)를 겪고도 매매가 살아나지 않은 근본 원인을 정밀 진단한 결과, 기존 패치는 표면 증상의 **약 30%만 해결**한 것으로 확인되었다. Phase 1(persona/data layer)과 Phase 2(infra/cycle layer) 디스커버리를 통해 다음 사실들이 드러났다:

- **인프라 레벨**: Docker 컨테이너가 2차 fix가 반영된 이미지로 **재빌드되지 않은 채** 5시간 이상 가동 중. `hold_warnings` Jinja undefined 에러가 스케줄러 로그에 반복 발생.
- **모델 레벨**: SPEC-015가 "모든 페르소나 → Claude Code CLI, API 비용 0원" 을 명시했음에도 Sonnet API가 어딘가에서 직접 호출되어 `RateLimitError 429` 발생. CLI 폴백 메시지("Haiku로 폴백")와 실제 호출 모델(Sonnet)이 불일치.
- **사이클 레벨**: `run_intraday_cycle()` 이 M5 deferred stub 상태 (`personas/orchestrator.py:894-901`). 모든 intraday 호출이 사실상 pre_market 로직으로 처리되고 DB에는 `cycle_kind="pre_market"` 로 기록됨.
- **persona 레벨**: Macro가 출력하는 `regime`(bull/neutral/bear) + `risk_appetite`(risk-on/neutral/risk-off) 가 **DB 컬럼으로 구조화되지 않고** 500자 텍스트 blob 형태로만 Decision/Risk 에 흘러감. `if regime == "bull"` 류의 분기 로직이 코드 어디에도 없음.
- **데이터 레벨**: `macro_context.md`(raw layer)에 KOSPI/KOSDAQ 일간/주간 등락률, 외국인/기관 매매, 신용잔고, 예탁금 등 **한국 시장 모멘텀 데이터가 없음**. Macro persona 프롬프트는 이 정보를 요구하지만 데이터가 비어 있음. 반면 `intelligence_macro.md`(LLM-analyzed layer)는 코스피 7,498 사상최고, 골드만 9,000 목표, 외인 매도 vs 신고가 갱신, 예탁금 137조, 금리 인상 시그널 등 풍부한 정보를 담고 있으나 항상 주입되지 않음.
- **빈도 레벨**: Macro는 **금요일 17:00 주 1회**만 실행. 신선도가 떨어질수록 APPROVE 비율이 급락 (5/4 fresh: 100% / 5/5 +1d: 28% / 5/6 +2d: 25% / 5/7 +3d: 0%).

이 SPEC은 위 진단을 토대로 **3개의 단계적 Phase**를 정의한다.

### Phase 별 목적과 P레벨

| Phase | 목적 | P-레벨 | 마감 |
|---|---|---|---|
| Phase 1: 긴급 안정화 | 컨테이너/템플릿/CLI 정합성 회복 — 거래 자체가 발생하도록 만든다 | P0 | **5/11(월) 장 시작 전** |
| Phase 2: Regime Awareness 아키텍처 | regime/risk_appetite 의 구조화·DB 캐싱·데일리 갱신·한국 모멘텀 데이터 수집 | P1 | Phase 1 완료 후 1주 |
| Phase 3: 불장 모드 + 천장 방어 | 강세장 활용을 위한 페르소나 컨텍스트 + 후기 사이클 위험 트리거 | P2 | Phase 2 완료 후 1주 |

### 비즈니스 목표 (User-Set)

- **월 수익률 목표 10~20%** (AGGRESSIVE CHALLENGE 레벨, 사용자가 명시적으로 선택)
- **집중 보유**: 동시 1~2 종목 (5종목 분산 → 고확신 집중)
- **현금 비중**: 평소 10~20% (현재 30~50%에서 하향)
- **단기 모멘텀 추격 허용**: event-CAR 임계 |1.5%| → |1.0%|, 보유기간 1~3주 → 4~10일

### 안전 가드 (Capital Preservation Constraint)

사용자(박세훈)는 **가족 부양 책임이 있는 개인 투자자**로 "자본 보전 우선" 원칙을 반복 강조했다. 따라서 Phase 3 의 공격성은 **Phase 3-2 의 후기 사이클 방어 트리거**와 한 묶음으로만 활성화된다 (단독 활성 금지).

---

## Environment

- 기존 SPEC-001 ~ SPEC-015 인프라 (Docker compose, Postgres 16-alpine, Telegram, KIS API)
- 기존 5-persona 시스템 (Macro/Micro/Decision/Risk/Portfolio)
- 기존 prompt 템플릿 6개 (`personas/prompts/*.jinja`)
- 기존 데이터 레이어: `data/contexts/macro_context.md` (raw), `data/contexts/intelligence_macro.md` (LLM-analyzed), `data/contexts/intelligence_micro.md`
- 기존 스케줄러: APScheduler 기반 cron 잡 (pre-market 07:30, intraday 매시 정각 09~15, post-market 15:40, daily report 16:00, weekly macro 금 17:00)
- SPEC-015의 host watcher (`scripts/persona_watcher.sh`) — Phase 1 검증 대상
- 신규 컬럼/테이블: `system_state.current_regime`, `system_state.current_risk_appetite`, 또는 `macro_state_cache` 테이블
- 신규 스크립트: `Makefile` rebuild 타겟, `scripts/build_macro_context.py` 확장
- 신규 feature flag: `bull_mode_enabled`, `late_cycle_defense_enabled`, `cli_only_mode`

## Assumptions

- A-1: SPEC-015 host watcher 인프라가 정상 동작 (heartbeat 파일 갱신 중)이며, Phase 1 의 cli_only_mode 강제 활성화로 Sonnet API 호출이 **모두 차단** 가능하다
- A-2: PostgreSQL 16 의 단일 행 UPDATE 는 race condition 없이 atomic 하다 (regime 캐싱에 충분)
- A-3: KIS Open API 의 시장 모멘텀 endpoint (외국인/기관 매매, 신용잔고, 투자자예탁금) 가 일 단위로 수치를 제공한다 (확인 필요)
- A-4: 강세장 신호와 후기 사이클 신호는 **공존 가능**하다 (예: KOSPI 신고가 + 빚투 36조 동시 발생). 정책은 두 신호를 독립 평가 후 안전 우선 결합한다.
- A-5: Decision/Risk 페르소나의 프롬프트 컨텍스트에 regime/risk_appetite 키워드를 추가하면 LLM 이 분기 로직을 일관되게 따른다 (SPEC-015 의 단일 턴 패턴 위에서 검증됨)
- A-6: 한국 시각 06:00 의 일일 Macro 실행은 미국 마감(한국 06:30) 직후 글로벌 거시 데이터를 흡수하기에 충분히 늦다
- A-7: `intelligence_macro.md` 의 impact-5 스토리 감지는 SPEC-014 의 뉴스 분류기로부터 받은 점수 기반으로 자동 트리거 가능하다

---

## Goals

- **G-1 (Phase 1 출구)**: 5/11 월요일 장 시작 09:00 시점에, 신뢰 가능한 거래 파이프라인이 **단 한 번의 Jinja 에러 / 단 한 번의 RateLimit 에러 없이** 1 cycle 을 완주한다.
- **G-2 (Phase 2 출구)**: Macro 가 일일 실행되며, regime/risk_appetite 가 DB 컬럼으로 조회 가능하고, Decision/Risk 가 분기 로직을 적용한다 (테스트 시나리오로 입증).
- **G-3 (Phase 3 출구)**: 불장 컨텍스트 + 후기 사이클 방어가 **항상 한 묶음으로** 활성화되며, 월 수익률 10~20% 트랙을 향한 1~2종 집중 매매가 시작된다.
- **G-4 (Cross-cutting)**: 자본 보전 원칙을 침해하지 않는다 — 어떤 Phase 도 사용자 승인 없이 단독으로 운영 모드를 전환하지 않는다.

---

## Requirements

### Phase 1: 긴급 안정화 (REQ-016-1)

#### REQ-016-1-1: `run_intraday_cycle` 본 구현 (Event-Driven)

**When** intraday 스케줄러 잡(매시 정각 09~15)이 실행되면, **then** 시스템은 **별도의 intraday cycle 본 구현 함수**를 호출해야 하며, 이 함수는 다음을 충족해야 한다:

- (a) DB 의 `persona_runs` 또는 `cycle_records` 테이블에 레코드를 **삽입하기 전에** `cycle_kind = "intraday"` 가 설정되어 있어야 한다.
- (b) 아침 pre-market 사이클에서 캐시된 Micro 결과(있으면)를 재사용하고, **Micro 를 재실행하지 않는다**.
- (c) Decision 과 Risk 는 신선한 시장 데이터로 매번 새로 실행한다.
- (d) `run_pre_market_cycle` 로 단순 위임하고 사후에 `cycle_kind` 를 덮어쓰는 현 stub 패턴은 **금지된다**.

**Acceptance criteria** — AC-1-1:
- [ ] `src/trading/personas/orchestrator.py:894-901` 의 stub 코드 제거
- [ ] 새 함수 `run_intraday_cycle_impl()` 작성, pre_market 로직과 분리
- [ ] DB query: `SELECT cycle_kind, COUNT(*) FROM persona_runs WHERE created_at >= today GROUP BY cycle_kind` → `intraday` 카운트가 매시 정각마다 1씩 증가
- [ ] Unit test: intraday 호출 1회당 Decision 1회, Risk 1회만 호출되는지 mock 검증
- [ ] 스케줄러 로그에 `cycle=intraday` 로 명시 출력

**Files affected**:
- `src/trading/personas/orchestrator.py` (stub 제거 + 본 구현)
- `src/trading/scheduler/jobs.py` (호출 지점 확인)
- `tests/personas/test_intraday_cycle.py` (신규)

**Dependencies**: 없음 (독립). REQ-016-1-2 와 병렬 작업 가능.

---

#### REQ-016-1-2: 컨테이너 리빌드 가드 + Jinja 템플릿 정합성 (Ubiquitous + Event-Driven)

시스템은 **컨테이너 이미지가 최신 커밋과 일치함을 자동 검증**해야 한다. 또한 **`hold_warnings` 미정의 시에도 Jinja 렌더링이 실패해서는 안 된다**.

세부:

- (a) **(Ubiquitous)** Makefile 에 `redeploy` 타겟 추가 — `docker compose build --no-cache scheduler && docker compose up -d --force-recreate scheduler` 의 묶음. 단일 진입점 강제.
- (b) **(Event-Driven)** **When** 컨테이너가 부팅되면, 시작 헬스체크가 `git rev-parse HEAD` 결과를 컨테이너 내 `/app/.build_commit` 파일과 비교한다. 불일치 시 즉시 종료하고 Telegram 알림 송출.
- (c) **(Event-Driven)** **When** `decision.jinja` 가 렌더링되면, `hold_warnings` 변수가 `is defined` 가드로 보호되고, 미정의 시 빈 리스트로 처리된다.
- (d) **(Ubiquitous)** Phase 1 산출물로 `.moai/specs/SPEC-TRADING-016/runbook_redeploy.md` 작성 — 박세훈 본인이 CLI 초보자임을 감안, 명령어 한 줄씩 단계별 안내.

**Acceptance criteria** — AC-1-2:
- [ ] `Makefile` 의 `redeploy` 타겟 단독 실행으로 fix 가 반영된 컨테이너가 기동
- [ ] `docker compose logs scheduler --since 24h | grep "UndefinedError.*hold_warnings"` 결과 0건 (Phase 1 종료 후 24시간)
- [ ] `decision.jinja` 의 `hold_warnings` 사용 위치 모두 `{% if hold_warnings is defined and hold_warnings %}` 가드 적용
- [ ] 컨테이너 시작 로그에 `build_commit=<sha>` 한 줄 출력
- [ ] runbook 문서 1페이지 분량 작성 완료, tmux 세션 활용법 포함

**Files affected**:
- `Makefile` (redeploy 타겟 추가)
- `src/trading/personas/prompts/decision.jinja` (hold_warnings 가드 검증)
- `Dockerfile` (build_commit 환경변수 ARG 처리)
- `docker-compose.yml` (HEALTHCHECK 추가)
- `.moai/specs/SPEC-TRADING-016/runbook_redeploy.md` (신규)

**Dependencies**: 없음 (독립).

---

#### REQ-016-1-3: SPEC-015 마감 — Sonnet API 직접 호출 차단 (Unwanted + State-Driven)

시스템은 의도된 폴백 경로를 제외한 **모든 Sonnet API 직접 호출을 금지**해야 한다.

세부:

- (a) **(Unwanted)** `cli_only_mode = true` 인 동안, 시스템은 `client.messages.create(model="claude-sonnet-*")` 를 호출해서는 안 된다.
- (b) **(State-Driven)** **While** `cli_only_mode` 가 true 이면, 모든 페르소나 호출은 SPEC-015 의 CLI bridge 만 사용한다.
- (c) **(Event-Driven)** **When** 코드베이스가 정적 분석되면, `grep -r "client.messages.create" src/` 결과가 의도된 fallback 경로 (`personas/cli_bridge.py:fallback_to_haiku()`) 단 1곳으로만 한정된다.
- (d) Feature flag `cli_only_mode` 를 `system_state` 테이블에 추가, default true.

**Acceptance criteria** — AC-1-3:
- [ ] `grep -rn "client.messages.create\|anthropic.Client.*messages" src/ tests/` 결과 화이트리스트 1곳 외 0건
- [ ] `docker compose logs scheduler --since 24h | grep -i "anthropic.*RateLimitError\|429"` 결과 0건 (Phase 1 종료 후 24시간)
- [ ] `system_state.cli_only_mode` 컬럼 존재, default true
- [ ] Telegram `/api_off` 명령으로 cli_only_mode 토글 가능

**Files affected**:
- `src/trading/personas/base.py` (`call_persona` 의 API 분기 차단)
- `src/trading/personas/cli_bridge.py` (fallback 경로 마킹)
- `src/trading/db/migrations/` 신규 마이그레이션 (cli_only_mode 컬럼)
- `src/trading/telegram/commands.py` (`/api_off`, `/api_on` 핸들러)

**Dependencies**: REQ-016-1-2 컨테이너 리빌드 후에 검증 가능.

---

#### REQ-016-1-4: CLI 폴백 일관성 (Event-Driven)

**When** CLI 호출이 실패하여 폴백이 발동되면, **then** 폴백 로그 메시지의 모델명과 실제 호출되는 API 모델명은 **반드시 동일**해야 한다.

세부:

- (a) 현재 메시지 "falling back to Haiku API" 가 출력되면서 실제로는 Sonnet 으로 호출되는 불일치를 제거한다.
- (b) `fallback_to_haiku()` 함수는 `model="claude-haiku-*"` 만 허용하며, 다른 모델 인자가 전달되면 ValueError 를 raise.
- (c) Unit test 로 폴백 경로의 model 일치를 보장한다.

**Acceptance criteria** — AC-1-4:
- [ ] `src/trading/personas/cli_bridge.py:fallback_to_haiku()` 의 모델 화이트리스트 검증
- [ ] Unit test `test_fallback_model_consistency.py` 작성: 폴백 시 로그 메시지에서 추출한 모델 == 실제 API 호출 모델
- [ ] 폴백 발동 Telegram 알림 포맷: `"CLI fallback: {persona} -> {actual_model} ({reason})"` (실제 모델명 명시)

**Files affected**:
- `src/trading/personas/cli_bridge.py`
- `tests/personas/test_fallback_consistency.py` (신규)

**Dependencies**: REQ-016-1-3 의 cli_only_mode 와 함께 검증.

---

### Phase 2: Regime Awareness 아키텍처 (REQ-016-2)

#### REQ-016-2-1: regime 의 DB 구조화 캐싱 (Ubiquitous + Event-Driven)

시스템은 Macro persona 가 산출하는 `regime` 과 `risk_appetite` 를 **구조화된 DB 컬럼으로 캐싱**하여, 다른 페르소나가 텍스트 파싱 없이 조회할 수 있도록 해야 한다.

세부:

- (a) **(Ubiquitous)** `system_state` 테이블 (또는 신규 `macro_state_cache` 테이블) 에 다음 컬럼 추가:
  - `current_regime TEXT NOT NULL DEFAULT 'neutral'` — 도메인: `'bull' | 'neutral' | 'bear'`
  - `current_risk_appetite TEXT NOT NULL DEFAULT 'neutral'` — 도메인: `'risk-on' | 'neutral' | 'risk-off'`
  - `regime_updated_at TIMESTAMP NOT NULL`
  - `regime_source_run_id BIGINT` (FK → persona_runs.id)
- (b) **(Event-Driven)** **When** Macro persona 가 성공적으로 응답을 반환하면, 시스템은 응답 JSON 의 `regime`, `risk_appetite` 필드를 추출하여 위 컬럼을 UPDATE 한다.
- (c) **(State-Driven)** **While** `regime_updated_at` 이 7일을 초과하면, 시스템은 Telegram 경고 송출 후 `current_regime` 을 `'neutral'` 로 안전 폴백한다 (TTL).
- (d) Macro persona 의 응답 스키마에 `regime`, `risk_appetite` 가 필수 필드로 정의되며, 미존재 시 응답 파싱이 실패한다.

**Acceptance criteria** — AC-2-1:
- [ ] DB 마이그레이션 적용, `\d system_state` (또는 `\d macro_state_cache`) 결과 4개 컬럼 존재
- [ ] Macro persona 1회 실행 후 `SELECT current_regime, current_risk_appetite FROM system_state` 가 비어있지 않은 enum 값 반환
- [ ] TTL 시뮬레이션 테스트: `regime_updated_at` 을 8일 전으로 강제 설정 → 다음 cycle 시 `current_regime` 이 `'neutral'` 로 전환
- [ ] Macro 응답 JSON 에 `regime` 키 누락 시 `MacroResponseSchemaError` raise

**Files affected**:
- `src/trading/db/migrations/` 신규 마이그레이션
- `src/trading/personas/macro.py` (응답 후처리)
- `src/trading/db/models.py` (system_state 모델)
- `src/trading/personas/prompts/macro.jinja` (응답 스키마 명시 추가)
- `tests/db/test_regime_cache.py` (신규)

**Dependencies**: Phase 1 완료 (안정적인 macro 실행 기반 필요).

---

#### REQ-016-2-2: Decision/Risk 의 regime 분기 로직 (State-Driven)

Decision 페르소나와 Risk 페르소나는 `current_regime` 컬럼 값을 읽어 다음과 같이 **명시적으로 분기 동작**해야 한다.

세부:

- (a) **(State-Driven)** **While** `current_regime == 'bull'` 이면:
  - Decision: 신뢰도 임계값을 base 대비 -0.1 완화 (예: 0.65 → 0.55)
  - Decision: 외국인 순매도 패널티를 base 의 50% 로 축소 (단, KOSPI 신고가 동시 발생 시에만)
  - Decision: 현금 floor 를 10% 로 하향 (base 30%)
  - Risk: 섹터 집중도 한도를 base 대비 +10%pt 완화
- (b) **(State-Driven)** **While** `current_regime == 'bear'` 이면:
  - Decision: 신뢰도 임계값을 +0.1 강화
  - Risk: 섹터 집중도 한도를 base 대비 -10%pt 강화
  - Risk: 모든 leverage/margin 사용 차단
- (c) **(State-Driven)** **While** `current_regime == 'neutral'` 이면 현재 동작 유지 (변경 없음).
- (d) **(Ubiquitous)** Decision/Risk 페르소나 프롬프트 시스템 메시지에 다음 라인 자동 주입:
  > "현재 시장 regime: {regime}, risk_appetite: {risk_appetite}. 이 컨텍스트에 따라 분기 적용."
- (e) regime 변경이 적용된 의사결정은 `persona_runs.regime_at_decision` 컬럼에 스냅샷 기록 (감사 추적).

**Acceptance criteria** — AC-2-2:
- [ ] Unit test 3개 (bull / neutral / bear) — 동일 입력에 대해 confidence threshold 가 다르게 적용됨을 확인
- [ ] Integration test: regime 을 강제 'bull' 로 설정 후 1 cycle 실행 → Decision 의 cash_target 이 10~20% 범위
- [ ] Decision/Risk 응답 JSON 에 `regime_branch_applied: "bull"|"neutral"|"bear"` 필드 추가, DB 저장
- [ ] `decision.jinja`, `risk.jinja` 에 regime context 주입 라인 존재 (grep 검증)

**Files affected**:
- `src/trading/personas/decision.py`
- `src/trading/personas/risk.py`
- `src/trading/personas/prompts/decision.jinja`
- `src/trading/personas/prompts/risk.jinja`
- `tests/personas/test_regime_branching.py` (신규)

**Dependencies**: REQ-016-2-1 (regime DB 컬럼 선행).

---

#### REQ-016-2-3: Macro 실행 빈도 상향 + Adaptive 트리거 (Event-Driven + Ubiquitous)

세부:

- (a) **(Ubiquitous)** Macro 정기 스케줄을 변경:
  - **기존**: 금요일 17:00 주 1회
  - **신규**: 매일 06:00 + 금요일 17:00 (주간 종합)
- (b) **(Event-Driven)** **When** SPEC-014 의 뉴스 분류기가 impact-5 (highest impact) 스토리를 마지막 Macro 실행 이후 3건 이상 감지하면, **then** 시스템은 다음 Macro 정기 잡을 기다리지 않고 **즉시 추가 Macro 실행을 트리거**한다 (cooldown 30분).
- (c) **(Ubiquitous)** Macro 비용은 SPEC-015 의 CLI bridge 를 사용하므로 0원, 빈도 상향에 따른 추가 비용 없음.

**Acceptance criteria** — AC-2-3:
- [ ] APScheduler 잡 등록: `macro_daily` (cron 0 6 * * *), `macro_weekly` (cron 0 17 * * 5), `macro_adaptive` (event listener)
- [ ] 24시간 모니터링 후 `SELECT COUNT(*) FROM persona_runs WHERE persona='macro' AND created_at >= now() - 24h` 결과 ≥ 1
- [ ] impact-5 스토리 mock 주입 → 30분 cooldown 유지하면서 1회 추가 실행 발생 확인
- [ ] regime 갱신 빈도 향상: `regime_updated_at` 의 평균 staleness ≤ 24h (Phase 2 종료 후 1주 평균)

**Files affected**:
- `src/trading/scheduler/jobs.py`
- `src/trading/news/classifier.py` (impact-5 이벤트 emit)
- `src/trading/personas/macro_trigger.py` (신규, adaptive 로직)
- `tests/scheduler/test_macro_frequency.py` (신규)

**Dependencies**: REQ-016-2-1 (regime 캐시 인프라). SPEC-014 (뉴스 분류기) 의 impact-5 점수가 의도대로 emit 되는지 확인 필요.

---

#### REQ-016-2-4: macro_context 의 한국 시장 모멘텀 데이터 확장 (Ubiquitous)

`build_macro_context.py` 가 생성하는 raw layer (`data/contexts/macro_context.md`) 는 다음 한국 시장 모멘텀 필드를 **반드시 포함**해야 한다.

세부:

- (a) **(Ubiquitous)** 다음 필드를 매 빌드마다 채워야 한다:
  - KOSPI / KOSDAQ 의 일간 변동률 (%) 및 5일 변동률 (%)
  - 외국인 / 기관 / 개인 순매수 (최근 5거래일, 억원 단위)
  - 신용잔고 (margin loans) 총액 (조원, 1일 1회 갱신)
  - 투자자예탁금 (조원, 1일 1회 갱신)
  - VIX (미국) + 한국 변동성 지수 (V-KOSPI)
  - KOSPI 52주 최고가 대비 현재가 비율 (%)
- (b) **(Ubiquitous)** 위 데이터가 없거나 stale (>24h) 인 필드는 `(unavailable)` 마커와 함께 표기, 빌드를 막지 않는다.
- (c) **(Ubiquitous)** 빌드된 `macro_context.md` 의 끝부분에 `intelligence_macro.md` 의 요약 섹션 (최근 24h 의 Top-3 impact 스토리) 을 자동 cross-reference 로 첨부한다.
- (d) 데이터 출처: KIS Open API (외국인/기관/개인 순매수, 신용잔고, 예탁금), pykrx 또는 동등 라이브러리 (VIX, V-KOSPI), KIS 시장지수 API (KOSPI/KOSDAQ).

**Acceptance criteria** — AC-2-4:
- [ ] `python scripts/build_macro_context.py` 실행 후 `data/contexts/macro_context.md` 에 6개 모멘텀 섹션 모두 존재 (`grep -c '## 한국 시장 모멘텀'` ≥ 1)
- [ ] 데이터 stale (>24h) 시 `(unavailable: stale)` 마커 정상 출력
- [ ] cross-reference 섹션 (`## intelligence_macro 요약`) 존재 + Top-3 스토리 인용
- [ ] Macro persona 응답 품질 sanity check: 빌드된 context 를 input 으로 1회 실행 → 응답에 KOSPI 등락률 / 외국인 흐름 / 신용잔고 키워드 중 ≥ 2개 등장

**Files affected**:
- `scripts/build_macro_context.py` (확장)
- `src/trading/data/kis_market.py` (KIS Open API 래퍼, 신규)
- `src/trading/data/sources.py` (pykrx 통합)
- `tests/data/test_macro_context_build.py` (신규)

**Dependencies**: 없음 (REQ-016-2-1 ~ 2-3 와 병렬 가능).

---

### Phase 3: 불장 모드 + 천장 방어 (REQ-016-3)

#### REQ-016-3-1: 불장 모드 페르소나 컨텍스트 (State-Driven)

**While** `current_regime == 'bull'` AND `late_cycle_defense_active == false` 이면, Decision 과 Risk 페르소나는 **공격적 매매 모드**로 전환된다.

세부:

- (a) Decision 의 목표:
  - 동시 보유 종목 1~2개로 집중 (현재 5종목 → 2종목)
  - 현금 floor 10~20% (현재 30~50%)
  - 보유 기간 가이드: 4~10일 (현재 1~3주)
  - event-CAR 임계: |1.0%| 이상이면 후보 진입 (현재 |1.5%|)
- (b) Risk 의 한도 (REQ-016-2-2 의 bull 분기와 합쳐 적용):
  - 섹터 집중도 한도 +10%pt
  - 단일 종목 max position +10%pt (예: 35% → 45%)
- (c) **(Ubiquitous)** Decision/Risk 시스템 프롬프트에 다음 컨텍스트 라인을 자동 주입:
  > "지금은 역대급 강세장 (코스피 7,300+, 6주 연속 상승, 외인 매도에도 신고가 갱신). 보수 모드 해제. 불장 활용 적극. 단, 후기 사이클 신호 발생 시 즉시 방어 모드로 전환."
- (d) **(Unwanted)** 불장 모드는 `late_cycle_defense_active == true` 인 동안에는 **자동으로 비활성화**된다 (강제 OFF, 사용자 토글 불가).
- (e) **(Ubiquitous)** 모든 불장 모드 활성화/비활성화 전환은 Telegram 으로 알림 송출 (`"BULL MODE ON: regime=bull, late_cycle=clear"`).

**Acceptance criteria** — AC-3-1:
- [ ] regime='bull' + late_cycle_defense_active=false 시 Decision 응답에 `target_holdings_count` ∈ [1, 2], `cash_target_pct` ∈ [10, 20]
- [ ] regime='bull' + late_cycle_defense_active=**true** 시 Decision 동작이 neutral 모드로 자동 전환됨 (테스트 시나리오)
- [ ] 모드 전환 Telegram 알림 송출 (mock 검증)
- [ ] event-CAR 임계 강화: 같은 후보에 대해 bull 모드 시 |1.0%| 통과, neutral 모드 시 |1.5%| 통과 (parametrize 테스트)

**Files affected**:
- `src/trading/personas/decision.py` (target_holdings_count, cash_floor 분기)
- `src/trading/personas/risk.py` (섹터 한도 분기)
- `src/trading/personas/prompts/decision.jinja` (bull 컨텍스트 주입)
- `src/trading/personas/prompts/risk.jinja` (bull 컨텍스트 주입)
- `src/trading/telegram/notifier.py` (mode 전환 알림)
- `tests/personas/test_bull_mode.py` (신규)

**Dependencies**: REQ-016-2-1, 2-2 (regime DB 와 분기 로직). REQ-016-3-2 (late-cycle defense 와 동시 출시 필수).

---

#### REQ-016-3-2: 후기 사이클 방어 트리거 (Event-Driven + State-Driven)

시스템은 **후기 사이클 위험 신호**를 매일 16:00 (post-market) 에 평가하고, 임계 초과 시 즉시 방어 모드로 전환한다.

세부:

- (a) **(Ubiquitous)** Late-cycle 위험 신호 정의:
  | 신호 | 임계 | 단계 |
  |---|---|---|
  | 신용잔고 (빚투) | > 35조원 | moderate caution |
  | 신용잔고 (빚투) | > 40조원 | severe caution |
  | 투자자예탁금 | > 140조원 | top warning |
  | V-KOSPI | ≥ 30 | immediate de-risk |
  | KOSPI 일일 하락 | ≤ -3% | flash de-risk |
- (b) **(Event-Driven)** **When** 위 임계 중 어느 하나라도 트리거되면, 시스템은 `late_cycle_defense_active = true` 로 설정하고 다음을 강제한다:
  - moderate caution: 현금 floor 30%, 신규 진입 1일 최대 1건으로 제한
  - severe caution: 현금 floor 50%, 보유 종목의 30% 부분 매도 (강제 deleveraging)
  - top warning: 현금 floor 60%, 신규 진입 차단 (24h)
  - immediate de-risk: 현금 floor 30% 즉시, 모든 신규 진입 차단
  - flash de-risk: 손실 30% 컷 강화, 다음 cycle 까지 신규 진입 차단
- (c) **(State-Driven)** **While** `late_cycle_defense_active == true` 이면 REQ-016-3-1 의 불장 모드는 **자동 비활성**된다.
- (d) **(Ubiquitous)** 매일 16:00 post-market 잡이 `macro_context.md` 의 확장 필드 (REQ-016-2-4) 를 읽어 위 임계를 평가한다.
- (e) **(Event-Driven)** **When** 임계 신호가 해소되면 (예: 빚투 < 33조원 으로 회귀), 시스템은 cooldown 24시간 이후에 `late_cycle_defense_active = false` 로 복귀.
- (f) **(Ubiquitous)** 모든 트리거 발생/해제는 `late_cycle_events` 테이블에 기록되고 Telegram 으로 알림 송출.

**Acceptance criteria** — AC-3-2:
- [ ] post-market 16:00 잡 등록 + 5개 신호 모두 평가 로직 구현
- [ ] mock 시나리오 5종 — 각 신호 임계 초과 시 정확한 단계로 진입
- [ ] 임계 해소 후 24h cooldown 정상 작동
- [ ] `late_cycle_events` 테이블 마이그레이션 + 테스트 데이터 삽입 검증
- [ ] 불장 모드 + late-cycle 트리거 동시 발생 시나리오: 불장 모드가 자동 OFF, late-cycle 단계가 effect (충돌 없음)
- [ ] Telegram 알림 포맷: `"⚠️ LATE-CYCLE DEFENSE: {signal_name}={value}{unit}, level={moderate|severe|top|immediate|flash}"`

**Files affected**:
- `src/trading/scheduler/jobs.py` (post-market 잡 추가)
- `src/trading/risk/late_cycle.py` (신규)
- `src/trading/db/migrations/` (`late_cycle_events` 테이블)
- `src/trading/personas/risk.py` (late-cycle 단계별 cash_floor 적용)
- `src/trading/telegram/notifier.py`
- `tests/risk/test_late_cycle.py` (신규)

**Dependencies**: REQ-016-2-4 (macro_context 확장 필드). REQ-016-3-1 (bull 모드와 상호배제 로직).

---

## Specifications

### S-1: regime DB 스키마

```sql
ALTER TABLE system_state
  ADD COLUMN current_regime TEXT NOT NULL DEFAULT 'neutral'
    CHECK (current_regime IN ('bull', 'neutral', 'bear')),
  ADD COLUMN current_risk_appetite TEXT NOT NULL DEFAULT 'neutral'
    CHECK (current_risk_appetite IN ('risk-on', 'neutral', 'risk-off')),
  ADD COLUMN regime_updated_at TIMESTAMP NOT NULL DEFAULT now(),
  ADD COLUMN regime_source_run_id BIGINT REFERENCES persona_runs(id);
```

### S-2: macro_context 한국 모멘텀 섹션 형식

```markdown
## 한국 시장 모멘텀 (as of 2026-05-09 KST)

### 지수
- KOSPI: 7,498.34 (+0.42% / +2.18% over 5d) — 52주 고점 대비 99.2%
- KOSDAQ: 1,210.55 (+0.31% / +1.05% over 5d)

### 수급 (최근 5거래일, 억원)
| 주체 | -5d | -4d | -3d | -2d | -1d | 누적 |
|---|---|---|---|---|---|---|
| 외국인 | -2,310 | -1,840 | +850 | -3,210 | -2,920 | -9,430 |
| 기관 | +1,200 | -350 | -1,420 | +2,180 | +1,950 | +3,560 |
| 개인 | +1,110 | +2,190 | +570 | +1,030 | +970 | +5,870 |

### 변동성 / 레버리지
- V-KOSPI: 18.3 (전일 대비 +0.4)
- VIX: 22.1
- 신용잔고: 36.2조원 (전주 대비 +0.8조)
- 투자자예탁금: 137.4조원 (전주 대비 +1.2조)

## intelligence_macro 요약 (Top-3 impact 24h)
1. (impact 5) 골드만 KOSPI 9000 목표 상향
2. (impact 4) 한은 5월 금리 인상 시그널
3. (impact 4) 외국인 5거래일 연속 순매도에도 신고가 갱신
```

### S-3: 불장 모드 vs late-cycle 우선순위

```
priority(late_cycle_defense_active=true) > priority(regime='bull')
```

| `current_regime` | `late_cycle_defense_active` | Effective Mode |
|---|---|---|
| bull | false | BULL (REQ-016-3-1) |
| bull | true | LATE-CYCLE DEFENSE (REQ-016-3-2 단계 적용) |
| neutral | false | NEUTRAL (current behavior) |
| neutral | true | LATE-CYCLE DEFENSE |
| bear | false | BEAR (REQ-016-2-2 (b)) |
| bear | true | LATE-CYCLE DEFENSE (더 보수적인 쪽 채택) |

### S-4: Phase 별 출구 기준 표

| Phase | Gate 조건 (모두 충족 필요) |
|---|---|
| Phase 1 → 2 | (1) AC-1-1 ~ AC-1-4 모두 통과 (2) 24h 컨테이너 운영 시 Jinja/RateLimit 에러 0건 (3) 5/11 월 09:00 cycle 1회 정상 완주 |
| Phase 2 → 3 | (1) AC-2-1 ~ AC-2-4 모두 통과 (2) 7일 평균 regime staleness ≤ 24h (3) Decision/Risk regime 분기 로그 1주 동안 일관 적용 (4) 사용자 명시적 승인 |
| Phase 3 종료 | (1) AC-3-1 ~ AC-3-2 모두 통과 (2) 1주 paper trading 결과: 1~2종 집중, drawdown ≤ 10% (3) late-cycle 트리거 mock 시나리오 5종 모두 통과 (4) 사용자 명시적 승인 |

---

## Constraints

- C-1: 사용자가 CLI 초보자이므로, 모든 운영 절차는 **runbook 형식의 단계별 명령어**로 문서화한다 (cf. AC-1-2 의 runbook_redeploy.md).
- C-2: 자본 보전 우선. Phase 3 의 공격성은 단독으로 활성화되지 않으며, late-cycle defense 와 한 묶음으로만 출시된다.
- C-3: SPEC-015 의 CLI 인프라가 깨지면 Phase 1 부터 다시 점검. cli_only_mode = true 가 디폴트이며 API 직접 호출은 의도된 폴백 1곳에서만 허용.
- C-4: KIS Open API 의 일일 호출 한도와 시장 데이터 endpoint 가용성을 사전 확인 (REQ-016-2-4 데이터 출처).
- C-5: regime 캐시 TTL 7일은 안전 폴백이며, 정상 운영 시 실제 staleness 는 24h 미만이어야 한다.
- C-6: late-cycle defense 의 임계값(35조/40조/140조 등)은 본 SPEC 의 초기값이며, Phase 3 paper trading 1주 후 사용자 승인 하에 보정 가능.
- C-7: 불장 모드의 보유 기간 가이드 (4~10일) 와 단일 종목 max position 상향은 **paper trading 환경에 한정** — 실거래 전환 시 사용자 별도 승인 필요.
- C-8: 모든 Phase 의 변경은 git branch (`feat/spec-016-phase{1,2,3}`) 로 격리, PR 단위로 사용자 리뷰.

---

## Risks

| ID | 리스크 | 영향 | 가능성 | 대응 |
|---|---|---|---|---|
| R-1 | 컨테이너 리빌드를 잊고 다시 같은 사고 | High | Medium | Makefile redeploy 단일 진입점 + 시작 헬스체크 (REQ-016-1-2) |
| R-2 | KIS API 의 신용잔고/예탁금 endpoint 가 실시간이 아니거나 폐지 | High | Medium | pykrx 등 대체 라이브러리로 다중 소스화 (REQ-016-2-4 (d)) |
| R-3 | 불장 모드가 활성화된 직후 시장이 천장을 형성 | Critical | Medium | late-cycle defense 와 한 묶음 출시 (REQ-016-3-1 (d)). paper trading 1주 검증 의무 |
| R-4 | regime 캐시 TTL 만료 후 stale 'neutral' 폴백이 실제 'bull' 시장에서 기회 손실 | Medium | Medium | adaptive Macro 트리거 (REQ-016-2-3 (b)) 로 staleness 단축 |
| R-5 | LLM 이 regime 분기 컨텍스트를 무시하고 자체 판단 | Medium | Medium | 프롬프트에 명시적 분기 룰 + 응답 JSON 의 `regime_branch_applied` 필드 강제 (REQ-016-2-2 (e)) |
| R-6 | adaptive Macro 트리거가 cooldown 무시하고 폭주 | Low | Low | 30분 cooldown 강제 + 일일 최대 8회 cap |
| R-7 | Phase 3 의 1~2종 집중이 단일 종목 사고로 큰 손실 | Critical | Medium | paper trading 한정 (C-7), late-cycle defense 자동 발동 (REQ-016-3-2) |
| R-8 | 사용자가 가족 부양 책임에서 기인한 불안 vs 월 10~20% 목표 사이의 심리적 괴리 | Medium | High | 매일 Telegram 모드 전환 알림으로 투명성 확보 (REQ-016-3-1 (e)) + Phase 출구마다 사용자 승인 게이트 |

---

## Rollout Plan

### Phase 1 — 5/10(토) ~ 5/11(월) 09:00

1. (오늘 5/10) `feat/spec-016-phase1` 브랜치 생성
2. REQ-016-1-1 (intraday 본 구현) + REQ-016-1-2 (Makefile + Jinja 가드) **병렬 작업**
3. REQ-016-1-3 (cli_only_mode 마이그레이션) → REQ-016-1-4 (fallback 일관성) 순차
4. 통합 테스트: dry-run cycle 3회 (pre_market / intraday / post_market) 모두 정상 완주
5. 5/11 06:00 KST: 컨테이너 redeploy
6. 5/11 09:00: 실제 장 시작 후 1시간 모니터링 → Phase 1 게이트 통과 시 사용자 승인 받고 Phase 2 착수

### Phase 2 — Phase 1 완료 후 1주 (예: 5/12 ~ 5/18)

1. `feat/spec-016-phase2` 브랜치
2. REQ-016-2-1 (DB 마이그레이션) → REQ-016-2-2 (분기 로직) 순차
3. REQ-016-2-3 (스케줄 변경) + REQ-016-2-4 (macro_context 확장) **병렬**
4. 7일 모니터링: regime 갱신 빈도, Decision/Risk 분기 로그 sanity check
5. 사용자 승인 게이트 → Phase 3 착수

### Phase 3 — Phase 2 완료 후 1주

1. `feat/spec-016-phase3` 브랜치
2. REQ-016-3-1 (bull 모드) + REQ-016-3-2 (late-cycle defense) **반드시 동시 출시**
3. paper trading 1주 (실거래 차단)
4. 결과 리뷰 + 임계값 보정 + 사용자 승인 후 실거래 전환 (필요 시 별도 SPEC 으로 분리 가능)

### Safety Gates

- Phase 1 종료 전: 사용자가 직접 redeploy runbook 을 한 번 따라 실행해보고 문제 없음을 확인
- Phase 2 종료 전: 7일간 zero-trade 사고 재발 없음 확인
- Phase 3 종료 전: paper trading 의 max drawdown ≤ 10%, late-cycle mock 시나리오 5종 모두 통과

---

## Open Questions

- Q-1: 신용잔고 / 투자자예탁금 의 일일 데이터를 KIS Open API 가 직접 제공하는가? 미제공 시 pykrx (한국거래소) 또는 KOSCOM 의 대체 endpoint 확인 필요. — Phase 2 착수 시 첫 작업으로 데이터 가용성 검증.
- Q-2: 사용자가 정의한 "월 10~20% 수익률 목표" 의 측정 단위는 paper trading 의 시뮬레이션 잔고 기준인가, 실제 투입 자본 (10M KRW) 기준인가? — 단순 % 기준이면 동일하나, 손실 발생 시 base 가 달라지므로 명확화 필요.
- Q-3: Phase 3 의 paper trading 종료 후 실거래 전환을 본 SPEC 의 일부로 다룰지, 별도 SPEC-017 로 분리할지? — 권장: 별도 SPEC. 본 SPEC 은 paper trading 검증까지로 한정.
- Q-4: Late-cycle 임계 (빚투 35/40조, 예탁금 140조) 의 출처는 2026-05-09 시장 컨텍스트의 휴리스틱이다. 향후 5년 데이터로 백테스트하여 보정할 가치가 있는가? — Phase 3 후속 개선 항목 후보.
- Q-5: regime 캐시를 `system_state` 테이블 확장으로 둘 것인가, 신규 `macro_state_cache` 테이블로 분리할 것인가? — 변경 빈도와 row 수를 고려하면 system_state 단일 행 UPDATE 로 충분. 초안은 system_state 확장 채택.

---

## Traceability

| Requirement | Phase | Acceptance Criteria | Files Affected (대표) |
|---|---|---|---|
| REQ-016-1-1 | Phase 1 (P0) | AC-1-1 | `personas/orchestrator.py`, `scheduler/jobs.py` |
| REQ-016-1-2 | Phase 1 (P0) | AC-1-2 | `Makefile`, `decision.jinja`, `Dockerfile`, runbook_redeploy.md |
| REQ-016-1-3 | Phase 1 (P0) | AC-1-3 | `personas/base.py`, `cli_bridge.py`, db migration |
| REQ-016-1-4 | Phase 1 (P0) | AC-1-4 | `personas/cli_bridge.py`, fallback test |
| REQ-016-2-1 | Phase 2 (P1) | AC-2-1 | db migration, `personas/macro.py`, macro.jinja |
| REQ-016-2-2 | Phase 2 (P1) | AC-2-2 | `personas/decision.py`, `risk.py`, decision.jinja, risk.jinja |
| REQ-016-2-3 | Phase 2 (P1) | AC-2-3 | `scheduler/jobs.py`, `news/classifier.py`, `personas/macro_trigger.py` |
| REQ-016-2-4 | Phase 2 (P1) | AC-2-4 | `scripts/build_macro_context.py`, `data/kis_market.py` |
| REQ-016-3-1 | Phase 3 (P2) | AC-3-1 | `personas/decision.py`, `risk.py`, decision.jinja, risk.jinja |
| REQ-016-3-2 | Phase 3 (P2) | AC-3-2 | `risk/late_cycle.py`, `scheduler/jobs.py`, db migration (late_cycle_events) |
