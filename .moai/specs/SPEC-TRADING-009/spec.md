---
id: SPEC-TRADING-009
version: 0.1.0
status: draft
created: 2026-05-05
updated: 2026-05-05
author: onigunsow
priority: medium
issue_number: 0
domain: TRADING
title: "AI/LLM Architecture Upgrade — Tool-calling Active Retrieval + Risk REJECT Reflection Loop"
related_specs:
  - SPEC-TRADING-001
  - SPEC-TRADING-007
  - SPEC-TRADING-008
---

# SPEC-TRADING-009 — AI/LLM Architecture Upgrade

## HISTORY

| 일자 | 버전 | 변경 내용 | 작성자 |
|---|---|---|---|
| 2026-05-05 | 0.1.0 | 초안 — Tool-calling Active Retrieval + Risk REJECT Reflection Loop (4 모듈) | onigunsow |

## 범위 요약

SPEC-TRADING-001이 5-페르소나 시스템의 기본 파이프라인을, SPEC-TRADING-007이 Static Context + Dynamic Memory를, SPEC-TRADING-008이 Prompt Caching 비용 최적화를 정의했다면, 본 SPEC은 **LLM 아키텍처 고도화** 전담이다.

두 가지 핵심 업그레이드를 정의한다:

1. **Tool-calling based Active Information Retrieval** — 페르소나가 필요한 정보만 능동적으로 조회하는 Function Calling 패턴. 현재 bulk injection 대비 토큰 ~80% 절감 + 추론 집중도 향상.
2. **Risk REJECT Reflection Loop** — Risk 페르소나가 REJECT 시 거부 사유를 Decision 페르소나에게 피드백하여 자기 수정 + 재평가(최대 2회 재시도). 오판 REJECT 감소 + 매매 품질 향상.

**학술적 근거**:
- Upgrade 1: AlphaQuanter paper (Acquire-Reason-Act loop) — 단일 에이전트가 정보 공백을 식별하고 능동적으로 Tool을 호출
- Upgrade 2: FinCon/TradingAgents papers — Multi-agent debate와 conceptual verbal reinforcement 기반 self-reflection

본 SPEC은 기존 매매 로직, Risk 한도 규칙, 페르소나 시스템 프롬프트 구조를 변경하지 않는다. 호출 아키텍처(information flow + feedback loop)만 고도화한다.

## 환경 (Environment)

- 기존 SPEC-TRADING-001 인프라 그대로 활용 — Postgres, Anthropic API, Telegram, Docker compose 재사용
- Anthropic API `tools` parameter 지원 (claude-sonnet-4-6, claude-opus-4-7 모두 tool use 지원)
- 기존 `src/trading/personas/context.py`의 data-assembly 함수들을 Tool definition으로 전환
- 기존 `src/trading/personas/orchestrator.py`의 Micro→Decision→Risk 순차 파이프라인에 reflection loop 추가
- SPEC-008 Phase A (Prompt Caching)와 공존 — 시스템 프롬프트 + Tool definition은 캐시 가능, Tool result는 캐시 불가

## 가정 (Assumptions)

1. Anthropic API의 Tool Use (Function Calling) 기능이 본 SPEC 운영 기간 동안 안정적으로 유지된다. `claude-sonnet-4-6` 및 `claude-opus-4-7` 모두 `tools` parameter를 지원한다.
2. Tool Use 호출 시 추가 latency는 Tool function 실행 시간(DB query ~50ms) + API round-trip 1회(~500ms)이며, 현재 bulk injection 대비 총 latency 증가가 3초 미만이다.
3. Tool Use 사용 시 Anthropic billing은 tool_use output tokens + tool_result input tokens로 기존 대비 token 총량은 감소하되 API 호출 횟수는 증가한다 (가격 유리).
4. Reflection Loop의 최대 2회 재시도는 Risk persona 호출 2회 추가 (Sonnet 4.6 × 2 ≈ +2,000원/일)를 의미하며, 월 추가 비용 ~4만원 이내이다.
5. SPEC-001의 60초 이벤트 트리거 응답 요건(REQ-EVENT-04-6)은 Reflection Loop 포함 시에도 준수 가능하다 (최대 2회 재시도 × ~5초/회 = +10초).
6. SPEC-007의 Static Context `.md` 파일 구조와 Dynamic Memory 테이블은 Tool-calling 전환 후에도 데이터 소스로서 그대로 유지된다 (access pattern만 변경).

## Robustness Principles (SPEC-001 6대 원칙 승계)

본 SPEC은 SPEC-TRADING-001 v0.2.0의 6대 Robustness 원칙을 그대로 승계한다. 특히:

- **외부 의존성 실패 가정 (원칙 1)** — Tool 호출 timeout (5초), fallback to bulk injection 지원
- **실패 침묵 금지 (원칙 3)** — Tool 호출 실패, Reflection Loop 무한루프 차단 모두 audit_log + Telegram
- **자동 복구 후 인간 통보 (원칙 4)** — Tool timeout 시 자동 fallback → 인간 통보
- **테스트로 명세를 굳힌다 (원칙 5)** — Tool registry + Reflection Loop 모듈 100% coverage

---

## 요구사항 (Requirements) — EARS

EARS 표기 약식: **U**=Ubiquitous, **E**=Event-driven, **S**=State-driven, **O**=Optional, **N**=Unwanted

---

### Module 1 — Tool Registry & Function Calling Infrastructure

**REQ-TOOL-01-1 [U]** The system shall implement a Tool Registry under `src/trading/tools/registry.py` defining all available tools as Anthropic API `tools` schema format (name, description, input_schema as JSON Schema).

**REQ-TOOL-01-2 [U]** The system shall implement the following tools:

| Tool Name | 설명 | 입력 | 출력 |
|---|---|---|---|
| `get_macro_indicators` | FRED/ECOS 거시 지표 조회 | `series_ids: list[str]` | 최신 값 + 날짜 목록 |
| `get_global_assets` | S&P500/VIX/USD-KRW 등 글로벌 자산 시세 | `symbols: list[str], days: int` | 최근 N일 종가 + 변동률 |
| `get_ticker_technicals` | 종목 기술적 지표 (MA/RSI/MACD) | `ticker: str, lookback_days: int` | 기술적 요약 dict |
| `get_ticker_fundamentals` | PER/PBR/ROE/시총 | `ticker: str` | 펀더멘털 dict |
| `get_ticker_flows` | 외국인/기관/개인 수급 | `ticker: str, days: int` | 수급 누적 dict |
| `get_recent_disclosures` | DART 공시 조회 | `tickers: list[str], days: int` | 공시 목록 |
| `get_static_context` | Static .md 파일 로드 | `name: str` (macro_context/micro_context/macro_news/micro_news) | .md 내용 문자열 |
| `get_active_memory` | Dynamic Memory 조회 | `table: str, limit: int, scope_filter: list[str] \| None` | 메모리 행 목록 |
| `get_portfolio_status` | 현재 포지션 + 자산 현황 | (없음) | KIS 잔고 dict |
| `get_watchlist` | 현재 워치리스트 | (없음) | 종목 코드 + 이름 목록 |

**REQ-TOOL-01-3 [U]** Each tool implementation shall be a standalone function under `src/trading/tools/` that wraps existing logic from `context.py`, `data/` adapters, and `kis/account.py`. Tool functions shall be pure data fetchers with no side effects.

**REQ-TOOL-01-4 [U, Robustness-1]** Each tool invocation shall enforce a timeout of 5 seconds. **When** a tool call exceeds the timeout, **then** the system shall return a structured error response `{"error": "timeout", "tool": "<name>"}` to the LLM rather than crashing the persona invocation.

**REQ-TOOL-01-5 [E]** When a tool call raises an exception (DB error, network timeout, data unavailable), the system shall catch the exception and return `{"error": "<exception_type>", "message": "<description>"}` to the LLM. The LLM shall then decide whether to retry, use alternative data, or proceed without it.

**REQ-TOOL-01-6 [U]** Tool definitions (name + description + input_schema) shall be included in the Anthropic `tools` parameter of the API call. These definitions shall be marked with `cache_control: {"type": "ephemeral"}` (SPEC-008 compatibility) since tool schemas are stable across invocations.

**REQ-TOOL-01-7 [U]** The system shall log every tool call to `audit_log` with: tool_name, input_params (hash only for privacy), execution_time_ms, success/failure, result_size_bytes.

**REQ-TOOL-01-8 [N]** Tool functions shall NOT access Anthropic API or invoke other personas. Tools are data-only; no LLM calls within tools.

---

### Module 2 — Persona Tool-calling Integration

**REQ-PTOOL-02-1 [U]** The `call_persona()` function in `base.py` shall be extended to support tool-use mode. When `tools` parameter is provided, the function shall handle the multi-turn tool-use loop:
1. Send initial messages with tools parameter
2. When response has `stop_reason="tool_use"`, execute the requested tool(s)
3. Append `tool_result` message and re-send
4. Repeat until `stop_reason="end_turn"` or max tool rounds reached

**REQ-PTOOL-02-2 [U]** The system shall enforce a maximum of **8 tool rounds** per single persona invocation. **If** a persona exceeds 8 tool rounds without producing a final response, **then** the system shall force-terminate the loop and use the last available text response (or empty default), write `audit_log` event `TOOL_LOOP_EXCEEDED`, and emit Telegram alert.

**REQ-PTOOL-02-3 [E]** When the Macro persona is invoked, the system shall provide tools: `get_macro_indicators`, `get_global_assets`, `get_static_context` (macro), `get_active_memory` (macro_memory). The Macro persona actively queries only what it needs for the current analysis.

**REQ-PTOOL-02-4 [E]** When the Micro persona is invoked, the system shall provide tools: `get_ticker_technicals`, `get_ticker_fundamentals`, `get_ticker_flows`, `get_recent_disclosures`, `get_static_context` (micro), `get_active_memory` (micro_memory), `get_watchlist`. The Micro persona iterates over tickers of interest, querying each individually.

**REQ-PTOOL-02-5 [E]** When the Decision persona is invoked, the system shall provide tools: `get_portfolio_status`, `get_ticker_technicals`, `get_ticker_fundamentals`, `get_static_context`, `get_active_memory`. Decision persona queries details for specific tickers it is considering trading.

**REQ-PTOOL-02-6 [E]** When the Risk persona is invoked, the system shall provide tools: `get_portfolio_status`, `get_ticker_technicals`, `get_ticker_flows`. Risk persona may query additional data to validate Decision signals.

**REQ-PTOOL-02-7 [U]** Token accounting in `persona_runs` shall additionally record: `tool_calls_count` (INT), `tool_input_tokens` (INT), `tool_output_tokens` (INT). Total tokens = standard input/output + tool round-trip tokens.

**REQ-PTOOL-02-8 [U]** The system prompt for each persona shall be updated to include tool-use instructions: *"필요한 정보가 있으면 제공된 Tool을 호출하여 조회하세요. 불필요한 정보를 미리 요청하지 말고, 분석에 꼭 필요한 데이터만 능동적으로 가져오세요."*

**REQ-PTOOL-02-9 [U]** The daily report (REQ-REPORT-05-6) shall include a tool usage summary line: `"Tool 호출: 총 X회, 평균 Y회/페르소나, 실패 Z건"`.

---

### Module 3 — Risk REJECT Reflection Loop

**REQ-REFL-03-1 [E]** When the Risk persona returns `verdict="REJECT"`, the orchestrator shall initiate a Reflection Loop:
1. Extract `rationale` and `concerns` from the Risk response
2. Re-invoke the Decision persona with original input PLUS rejection feedback context
3. Decision persona produces a revised signal (or explicitly confirms withdrawal)
4. Re-invoke Risk persona on the revised signal
5. If Risk returns APPROVE on the revised signal → proceed to code-rule check and execution
6. If Risk returns REJECT again → final rejection (no further retry)

**REQ-REFL-03-2 [N]** The system shall NOT execute more than **2 reflection rounds** per original Decision signal. The loop structure is: Decision(original) → Risk → [REJECT] → Decision(revised-1) → Risk → [REJECT] → Decision(revised-2) → Risk → [final]. Maximum 3 Risk invocations per signal (initial + 2 retries).

**REQ-REFL-03-3 [U]** The Decision persona's reflection input shall include the following additional context fields:
- `rejection_feedback.round`: 1 or 2 (current retry round)
- `rejection_feedback.risk_verdict`: "REJECT"
- `rejection_feedback.risk_rationale`: Risk persona의 거부 사유 전문
- `rejection_feedback.risk_concerns`: 우려 사항 목록
- `rejection_feedback.original_signal`: 원래 시그널 (참조용)
- `rejection_feedback.instruction`: *"위 거부 사유를 반영하여 시그널을 수정하거나, 철회(withdraw)하세요. 새 시그널은 Risk가 제기한 모든 우려를 해소해야 합니다."*

**REQ-REFL-03-4 [E]** When the Decision persona responds to a reflection round, the system shall accept one of two outcomes:
- **Revised signal**: `signals` 필드에 수정된 시그널 포함 → Risk 재평가로 진행
- **Withdrawal**: `signals` 필드가 빈 배열 `[]` 또는 `withdraw: true` → 즉시 종료, `audit_log` event `REFLECTION_WITHDRAWN`

**REQ-REFL-03-5 [S]** While a Reflection Loop is in progress, the system shall NOT initiate another reflection loop for the same cycle. Each cycle (pre_market / intraday / event) processes reflection sequentially per signal.

**REQ-REFL-03-6 [U]** Every Reflection Loop execution shall persist to a new DB table `reflection_rounds`:
- `id` BIGSERIAL PRIMARY KEY
- `cycle_kind` TEXT NOT NULL
- `original_decision_id` BIGINT REFERENCES persona_decisions(id)
- `round_number` SMALLINT NOT NULL (1 or 2)
- `risk_persona_run_id` BIGINT REFERENCES persona_runs(id) — the REJECT run
- `risk_rationale` TEXT
- `revised_decision_run_id` BIGINT REFERENCES persona_runs(id) — the retry Decision run
- `revised_risk_run_id` BIGINT REFERENCES persona_runs(id) — the retry Risk run
- `final_verdict` TEXT NOT NULL (APPROVE/REJECT/WITHDRAWN)
- `created_at` TIMESTAMPTZ DEFAULT NOW()

**REQ-REFL-03-7 [U]** The daily report shall include a reflection summary line: `"Reflection: 시도 X건, 성공(APPROVE) Y건, 최종 REJECT Z건, 철회 W건"`.

**REQ-REFL-03-8 [E]** When a Reflection Loop results in APPROVE (Risk approves revised signal), the Telegram briefing shall indicate: `"[Risk → REJECT → Reflection Round N → APPROVE]"` with both original and revised signal details.

**REQ-REFL-03-9 [N]** The Risk persona prompt shall NOT be modified to know about the Reflection Loop. Risk always evaluates independently — it does not know whether it is evaluating an original or revised signal. SoD independence is preserved.

**REQ-REFL-03-10 [U, Robustness-1]** Each reflection round (Decision re-invoke + Risk re-invoke) shall have a combined timeout of 30 seconds. **If** the timeout is exceeded, **then** the system shall abort the reflection loop, keep the original REJECT, write `audit_log` event `REFLECTION_TIMEOUT`, and emit Telegram alert.

---

### Module 4 — Backward Compatibility & Migration

**REQ-COMPAT-04-1 [U]** The system shall support a feature flag `TOOL_CALLING_ENABLED` (default: `false`) in `system_state` table. **While** `TOOL_CALLING_ENABLED=false`, the system shall use the existing bulk-injection context assembly (SPEC-007 pattern). **While** `TOOL_CALLING_ENABLED=true`, the system shall use the tool-calling pattern.

**REQ-COMPAT-04-2 [U]** The system shall support a feature flag `REFLECTION_LOOP_ENABLED` (default: `false`) in `system_state` table. **While** `REFLECTION_LOOP_ENABLED=false`, Risk REJECT leads to immediate rejection (current behavior). **While** `REFLECTION_LOOP_ENABLED=true`, Risk REJECT triggers the Reflection Loop.

**REQ-COMPAT-04-3 [E]** When `TOOL_CALLING_ENABLED` is switched from `false` to `true`, the system shall:
1. Write `audit_log` event `TOOL_CALLING_ACTIVATED`
2. Continue using the same tool functions that wrap existing context.py logic
3. Existing static `.md` files and memory tables remain unchanged (read via tools now)

**REQ-COMPAT-04-4 [E]** When `TOOL_CALLING_ENABLED=true` AND a tool invocation fails with timeout or error for 3 consecutive calls within a single persona invocation, the system shall automatically fallback to bulk injection for that specific persona call. This is a per-invocation fallback (not global). Event: `TOOL_FALLBACK_TRIGGERED`.

**REQ-COMPAT-04-5 [U]** The migration shall be phased:
- **Phase A** (Week 1): Deploy Tool Registry + infrastructure. Feature flags OFF. All tests pass.
- **Phase B** (Week 2): Enable `TOOL_CALLING_ENABLED=true` for Micro persona only (least critical). Monitor token savings + latency.
- **Phase C** (Week 3): Enable for Macro + Decision + Risk. Validate full pipeline.
- **Phase D** (Week 4): Enable `REFLECTION_LOOP_ENABLED=true`. Monitor reflection rate + outcomes.

**REQ-COMPAT-04-6 [N]** The system shall NOT remove the existing `context.py` assembly functions during Phase A~C. These functions serve as the fallback path and will be deprecated only after 2 weeks of stable tool-calling operation.

**REQ-COMPAT-04-7 [E]** When switching feature flags via Telegram command `/tool-calling on|off` or `/reflection on|off` from `chat_id=60443392`, the system shall update `system_state`, write `audit_log`, and confirm via Telegram within 5 seconds.

---

### Non-Functional Requirements

**REQ-NFR-09-1 [U, Performance]** Tool-calling mode shall achieve ≤ 1/5 of current input tokens per persona invocation (target: Micro currently ~5000 tok → ≤ 1000 tok base + tool results on-demand). Total token budget including tool round-trips shall be ≤ 80% of current bulk injection budget.

**REQ-NFR-09-2 [U, Latency]** End-to-end persona invocation latency (including tool round-trips) shall not exceed current latency + 5 seconds. If latency exceeds this threshold for 3 consecutive invocations, the system shall auto-fallback and emit alert.

**REQ-NFR-09-3 [U, Cost]** Reflection Loop additional cost shall be ≤ 5만원/month (assuming ~10 REJECT events/day × 2 retry rounds × 22 trading days × Sonnet cost).

**REQ-NFR-09-4 [U, Observability]** The cost monitoring report (SPEC-008 REQ-COSTM-03-1) shall additionally include: tool_calls_total, tool_failures, reflection_rounds, reflection_success_rate.

---

## Specifications (구현 명세 요약)

### DB Schema 변경 (Migration v10)

```sql
-- reflection_rounds table
CREATE TABLE reflection_rounds (
    id BIGSERIAL PRIMARY KEY,
    cycle_kind TEXT NOT NULL,
    original_decision_id BIGINT REFERENCES persona_decisions(id),
    round_number SMALLINT NOT NULL CHECK (round_number IN (1, 2)),
    risk_persona_run_id BIGINT NOT NULL REFERENCES persona_runs(id),
    risk_rationale TEXT,
    revised_decision_run_id BIGINT REFERENCES persona_runs(id),
    revised_risk_run_id BIGINT REFERENCES persona_runs(id),
    final_verdict TEXT NOT NULL CHECK (final_verdict IN ('APPROVE','REJECT','WITHDRAWN')),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- persona_runs 추가 컬럼
ALTER TABLE persona_runs ADD COLUMN tool_calls_count INTEGER DEFAULT 0;
ALTER TABLE persona_runs ADD COLUMN tool_input_tokens INTEGER DEFAULT 0;
ALTER TABLE persona_runs ADD COLUMN tool_output_tokens INTEGER DEFAULT 0;

-- system_state 추가 컬럼
ALTER TABLE system_state ADD COLUMN tool_calling_enabled BOOLEAN NOT NULL DEFAULT false;
ALTER TABLE system_state ADD COLUMN reflection_loop_enabled BOOLEAN NOT NULL DEFAULT false;

-- audit trail for tool calls
CREATE TABLE tool_call_log (
    id BIGSERIAL PRIMARY KEY,
    persona_run_id BIGINT REFERENCES persona_runs(id),
    tool_name TEXT NOT NULL,
    input_hash TEXT NOT NULL,
    execution_ms INTEGER NOT NULL,
    success BOOLEAN NOT NULL,
    result_bytes INTEGER,
    error TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX idx_tool_call_log_run ON tool_call_log(persona_run_id);
CREATE INDEX idx_tool_call_log_name ON tool_call_log(tool_name, created_at DESC);
```

### 신규 모듈 구조

```
src/trading/tools/
├── __init__.py
├── registry.py           # Tool schema definitions (Anthropic tools format)
├── executor.py           # Tool dispatch + timeout + error handling
├── market_tools.py       # get_macro_indicators, get_global_assets, get_ticker_*
├── context_tools.py      # get_static_context, get_active_memory
├── portfolio_tools.py    # get_portfolio_status, get_watchlist
└── fallback.py           # Fallback to bulk injection on consecutive failures
```

### 변경 모듈

- `src/trading/personas/base.py` — `call_persona()` 확장: tool-use loop 지원
- `src/trading/personas/orchestrator.py` — Reflection Loop 로직 추가 (REJECT 시 재시도)
- `src/trading/personas/prompts/*.jinja` — Tool-use instruction 추가 (기존 프롬프트에 1줄 추가)
- `src/trading/reports/daily_report.py` — Tool 사용량 + Reflection 통계 섹션 추가
- `src/trading/bot/telegram_bot.py` — `/tool-calling`, `/reflection` 명령어 추가
- `src/trading/db/migrations/010_tool_calling.sql` — 위 스키마

### SPEC-008 Prompt Caching과의 공존

- Tool definitions (JSON Schema)는 system prompt 일부로 `cache_control: ephemeral` 적용 가능 → 매 호출마다 동일하므로 cache hit 유지
- Tool result messages는 매번 다르므로 캐시 불가 (이는 의도된 동작)
- 시스템 프롬프트 본문(persona instructions)은 기존 캐싱 그대로 유지

### 의존 SPEC

본 SPEC은 SPEC-TRADING-001 (5-페르소나), SPEC-TRADING-007 (Static Context + Memory), SPEC-TRADING-008 Phase A (Prompt Caching) 의 구현이 완료된 이후 적용된다. 특히 SPEC-007의 `context.py` assembly 함수들이 Tool wrapper의 기반이 된다.

---

## Traceability

| REQ ID | Module | 구현 위치 (예정) | 검증 (acceptance.md) |
|---|---|---|---|
| REQ-TOOL-01-1~8 | M1 (Tool Registry) | `src/trading/tools/*` | M1 시나리오 |
| REQ-PTOOL-02-1~9 | M2 (Persona Integration) | `src/trading/personas/base.py`, `orchestrator.py`, `prompts/*.jinja` | M2 시나리오 |
| REQ-REFL-03-1~10 | M3 (Reflection Loop) | `src/trading/personas/orchestrator.py`, `db/migrations/010_*` | M3 시나리오 |
| REQ-COMPAT-04-1~7 | M4 (Backward Compat) | `src/trading/config.py`, `personas/base.py`, `bot/telegram_bot.py` | M4 시나리오 |
| REQ-NFR-09-1~4 | Cross-cutting | 전 모듈 | NFR 시나리오 |

---

## Future Scope (본 SPEC 범위 외)

- **Parallel Tool Calls** — Anthropic API가 병렬 tool call을 지원하면 Micro persona가 여러 종목을 동시 조회 가능. 현재는 순차 처리.
- **Tool-calling for Portfolio/Retrospective** — 본 SPEC은 Macro/Micro/Decision/Risk에만 적용. Portfolio와 Retrospective는 후속 SPEC에서 전환.
- **Semantic Tool Selection** — 현재는 persona별 고정 tool set. 향후 LLM이 전체 tool catalog에서 자율 선택하는 패턴.
- **Decision-Risk Debate (Multi-round)** — 현재 최대 2회 재시도. 향후 FinCon 스타일 multi-agent debate (3+ rounds, 중재자 도입) 검토.
- **Reflection for HOLD verdict** — 현재 REJECT만 reflection 대상. HOLD에 대한 reflection은 모호성이 높아 보류.
- **Tool-calling with Streaming** — 현재 non-streaming. 향후 streaming + tool call 조합으로 latency 개선.
