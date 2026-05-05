# Implementation Plan: SPEC-TRADING-009

Created: 2026-05-05
SPEC Version: 0.1.0
Development Mode: DDD (ANALYZE-PRESERVE-IMPROVE)

---

## 1. Plan Summary

This plan implements two major AI/LLM architecture upgrades: (1) Tool-calling based Active Information Retrieval replacing the current bulk-injection pattern in `context.py`, and (2) a Risk REJECT Reflection Loop enabling Decision persona self-correction. The implementation follows DDD methodology with characterization tests written first for the existing persona pipeline (`base.py`, `orchestrator.py`, `context.py`), then introduces the new `src/trading/tools/` module incrementally behind feature flags. The phased rollout (A through D over 4 weeks) ensures zero-downtime migration with automatic fallback to the existing bulk-injection path.

---

## 2. Requirements by Module

### Module 1 — Tool Registry & Function Calling Infrastructure
| REQ ID | Type | Summary |
|--------|------|---------|
| REQ-TOOL-01-1 | U | Tool Registry at `src/trading/tools/registry.py` in Anthropic `tools` schema format |
| REQ-TOOL-01-2 | U | 10 tools defined (macro, global assets, technicals, fundamentals, flows, disclosures, static context, active memory, portfolio, watchlist) |
| REQ-TOOL-01-3 | U | Standalone pure data-fetcher functions wrapping `context.py` logic |
| REQ-TOOL-01-4 | U | 5-second timeout per tool invocation with structured error response |
| REQ-TOOL-01-5 | E | Exception handling returns error dict to LLM |
| REQ-TOOL-01-6 | U | Tool definitions marked `cache_control: ephemeral` for SPEC-008 compatibility |
| REQ-TOOL-01-7 | U | Every tool call logged to `tool_call_log` table |
| REQ-TOOL-01-8 | N | Tools must NOT call Anthropic API or other personas |

### Module 2 — Persona Tool-calling Integration
| REQ ID | Type | Summary |
|--------|------|---------|
| REQ-PTOOL-02-1 | U | `call_persona()` extended for multi-turn tool-use loop |
| REQ-PTOOL-02-2 | U | Max 8 tool rounds per invocation |
| REQ-PTOOL-02-3 | E | Macro persona tool set: macro_indicators, global_assets, static_context, active_memory |
| REQ-PTOOL-02-4 | E | Micro persona tool set: technicals, fundamentals, flows, disclosures, static_context, active_memory, watchlist |
| REQ-PTOOL-02-5 | E | Decision persona tool set: portfolio_status, technicals, fundamentals, static_context, active_memory |
| REQ-PTOOL-02-6 | E | Risk persona tool set: portfolio_status, technicals, flows |
| REQ-PTOOL-02-7 | U | Token accounting: tool_calls_count, tool_input_tokens, tool_output_tokens |
| REQ-PTOOL-02-8 | U | System prompts include tool-use instructions |
| REQ-PTOOL-02-9 | U | Daily report includes tool usage summary line |

### Module 3 — Risk REJECT Reflection Loop
| REQ ID | Type | Summary |
|--------|------|---------|
| REQ-REFL-03-1 | E | REJECT triggers reflection: extract rationale -> re-invoke Decision -> re-invoke Risk |
| REQ-REFL-03-2 | N | Max 2 reflection rounds (3 Risk invocations total) |
| REQ-REFL-03-3 | U | Feedback context fields: round, verdict, rationale, concerns, original_signal, instruction |
| REQ-REFL-03-4 | E | Decision response: revised signal OR withdrawal |
| REQ-REFL-03-5 | S | No concurrent reflection for same cycle |
| REQ-REFL-03-6 | U | `reflection_rounds` DB table persistence |
| REQ-REFL-03-7 | U | Daily report reflection summary line |
| REQ-REFL-03-8 | E | Telegram briefing includes reflection outcome |
| REQ-REFL-03-9 | N | Risk prompt NOT modified to know about reflection (SoD independence) |
| REQ-REFL-03-10 | U | 30-second combined timeout per reflection round |

### Module 4 — Backward Compatibility & Migration
| REQ ID | Type | Summary |
|--------|------|---------|
| REQ-COMPAT-04-1 | U | Feature flag `TOOL_CALLING_ENABLED` (default: false) |
| REQ-COMPAT-04-2 | U | Feature flag `REFLECTION_LOOP_ENABLED` (default: false) |
| REQ-COMPAT-04-3 | E | Activation writes audit_log event |
| REQ-COMPAT-04-4 | E | 3 consecutive tool failures -> per-invocation fallback to bulk injection |
| REQ-COMPAT-04-5 | U | Phased migration: A (deploy) -> B (Micro) -> C (all) -> D (reflection) |
| REQ-COMPAT-04-6 | N | context.py functions NOT removed during Phase A-C |
| REQ-COMPAT-04-7 | E | Telegram `/tool-calling on|off`, `/reflection on|off` commands |

### Non-Functional Requirements
| REQ ID | Type | Summary |
|--------|------|---------|
| REQ-NFR-09-1 | U | Token reduction: tool-calling mode <= 80% of bulk injection |
| REQ-NFR-09-2 | U | Latency: not exceed current + 5 seconds |
| REQ-NFR-09-3 | U | Reflection cost <= 50,000 KRW/month |
| REQ-NFR-09-4 | U | Observability: cost report includes tool + reflection metrics |

---

## 3. Task Decomposition

### TASK-001: Test Infrastructure Setup

- **Description**: Create `tests/` directory with conftest.py, DB fixtures (mock connection context manager), Anthropic API mock fixtures, and base test patterns for the persona pipeline.
- **Requirement Mapping**: Foundation for all REQs (DDD ANALYZE-PRESERVE prerequisite)
- **Dependencies**: None (foundation task)
- **Files to Create**:
  - `tests/__init__.py`
  - `tests/conftest.py` (DB mock, Anthropic mock, settings override)
  - `tests/personas/__init__.py`
  - `tests/personas/test_base_characterization.py` (characterization tests for `call_persona`)
  - `tests/personas/test_orchestrator_characterization.py` (characterization tests for run_pre_market_cycle REJECT path)
  - `tests/personas/test_context_characterization.py` (characterization tests for assemble_* functions)
- **Acceptance Criteria**:
  - `pytest tests/` runs successfully with 0 failures
  - Characterization tests capture current behavior of `call_persona()`, `assemble_macro_input()`, `assemble_micro_input()`, and orchestrator REJECT path
  - Mock fixtures allow testing without live DB or Anthropic API
- **Effort**: M

---

### TASK-002: DB Migration 010 — Tool Calling Schema

- **Description**: Create SQL migration adding `reflection_rounds` table, `tool_call_log` table, `persona_runs` additional columns (tool_calls_count, tool_input_tokens, tool_output_tokens), and `system_state` additional columns (tool_calling_enabled, reflection_loop_enabled).
- **Requirement Mapping**: REQ-REFL-03-6, REQ-TOOL-01-7, REQ-PTOOL-02-7, REQ-COMPAT-04-1, REQ-COMPAT-04-2
- **Dependencies**: None
- **Files to Create**:
  - `src/trading/db/migrations/010_tool_calling.sql`
- **Acceptance Criteria**:
  - Migration applies cleanly on existing schema (after 009)
  - `reflection_rounds` table created with all columns per SPEC
  - `tool_call_log` table created with indexes
  - `persona_runs` has new nullable columns with defaults
  - `system_state` has new boolean columns defaulting to `false`
  - Migration is reversible (provide rollback comment block)
- **Effort**: S

---

### TASK-003: Tool Registry — Schema Definitions

- **Description**: Implement `src/trading/tools/registry.py` defining all 10 tools in Anthropic API `tools` schema format (name, description, input_schema as JSON Schema Draft 7+).
- **Requirement Mapping**: REQ-TOOL-01-1, REQ-TOOL-01-2, REQ-TOOL-01-6
- **Dependencies**: TASK-001 (test infrastructure)
- **Files to Create**:
  - `src/trading/tools/__init__.py`
  - `src/trading/tools/registry.py`
  - `tests/tools/__init__.py`
  - `tests/tools/test_registry.py`
- **Acceptance Criteria**:
  - `get_all_tool_definitions()` returns list of 10 tool dicts
  - Each dict has `name`, `description`, `input_schema` fields
  - `input_schema` validates as JSON Schema Draft 7+
  - Descriptions are Korean, under 50 characters
  - `get_tools_for_persona(persona_name)` returns correct subset per REQ-PTOOL-02-3~6
  - Each tool definition includes `cache_control: {"type": "ephemeral"}`
  - 100% test coverage for registry.py
- **Effort**: M

---

### TASK-004: Tool Implementations — Data Fetchers

- **Description**: Implement the 10 tool functions as standalone pure data fetchers wrapping existing `context.py` logic. Tools are located in `market_tools.py`, `context_tools.py`, and `portfolio_tools.py`.
- **Requirement Mapping**: REQ-TOOL-01-2, REQ-TOOL-01-3, REQ-TOOL-01-8
- **Dependencies**: TASK-003 (tool registry defines interfaces)
- **Files to Create**:
  - `src/trading/tools/market_tools.py` (get_macro_indicators, get_global_assets, get_ticker_technicals, get_ticker_fundamentals, get_ticker_flows, get_recent_disclosures)
  - `src/trading/tools/context_tools.py` (get_static_context, get_active_memory)
  - `src/trading/tools/portfolio_tools.py` (get_portfolio_status, get_watchlist)
  - `tests/tools/test_market_tools.py`
  - `tests/tools/test_context_tools.py`
  - `tests/tools/test_portfolio_tools.py`
- **Acceptance Criteria**:
  - Each tool wraps corresponding `context.py` function (e.g., `get_ticker_technicals` wraps `_technicals()`)
  - Tools are pure data fetchers — no Anthropic API imports, no `call_persona` references
  - Static analysis confirms no LLM calls in tool functions (REQ-TOOL-01-8)
  - Functions accept parameters matching their `input_schema` and return dicts
  - 85%+ test coverage for tool implementations
- **Effort**: L

---

### TASK-005: Tool Executor — Dispatch, Timeout, Logging

- **Description**: Implement `src/trading/tools/executor.py` with dispatch logic (name -> function), 5-second timeout enforcement, exception handling, and `tool_call_log` audit recording.
- **Requirement Mapping**: REQ-TOOL-01-4, REQ-TOOL-01-5, REQ-TOOL-01-7
- **Dependencies**: TASK-003, TASK-004 (needs registry + tool functions), TASK-002 (tool_call_log table)
- **Files to Create**:
  - `src/trading/tools/executor.py`
  - `tests/tools/test_executor.py`
- **Acceptance Criteria**:
  - `execute_tool(name, params, persona_run_id)` dispatches to correct function
  - Timeout at 5 seconds returns `{"error": "timeout", "tool": "<name>"}`
  - Exceptions return `{"error": "<type>", "message": "<desc>"}`
  - Every call (success or failure) writes to `tool_call_log` table
  - Log includes: persona_run_id, tool_name, input_hash (SHA-256), execution_ms, success, result_bytes, error
  - 100% test coverage
- **Effort**: M

---

### TASK-006: Tool Fallback — Consecutive Failure Detection

- **Description**: Implement `src/trading/tools/fallback.py` with per-invocation failure counter. When 3 consecutive tool calls fail within one persona invocation, trigger fallback to bulk injection.
- **Requirement Mapping**: REQ-COMPAT-04-4
- **Dependencies**: TASK-005 (executor provides failure signals)
- **Files to Create**:
  - `src/trading/tools/fallback.py`
  - `tests/tools/test_fallback.py`
- **Acceptance Criteria**:
  - `FallbackTracker` class counts consecutive failures per persona invocation
  - After 3 consecutive failures, `should_fallback()` returns True
  - Non-consecutive failures (success in between) reset the counter
  - When fallback triggers, audit_log event `TOOL_FALLBACK_TRIGGERED` is written
  - Fallback is per-invocation, not global (next invocation starts fresh)
  - 100% test coverage
- **Effort**: S

---

### TASK-007: base.py Extension — Tool-Use Multi-Round Loop

- **Description**: Extend `call_persona()` in `base.py` to support tool-use mode. When `tools` parameter is provided, implement the multi-turn loop: send -> check stop_reason -> execute tools -> append tool_result -> re-send -> repeat until end_turn or max rounds.
- **Requirement Mapping**: REQ-PTOOL-02-1, REQ-PTOOL-02-2, REQ-PTOOL-02-7
- **Dependencies**: TASK-005 (tool executor), TASK-006 (fallback tracker)
- **Files to Modify**:
  - `src/trading/personas/base.py` (extend `call_persona` or add `call_persona_with_tools`)
- **Files to Create/Modify**:
  - `tests/personas/test_base_tool_use.py`
- **Acceptance Criteria**:
  - `call_persona()` accepts optional `tools` parameter (list of tool defs)
  - When tools provided and response has `stop_reason="tool_use"`, executes requested tool(s)
  - Appends `tool_result` message block and re-sends
  - Repeats until `stop_reason="end_turn"` or 8 rounds reached
  - At max 8 rounds: force-terminate, use last text, write `TOOL_LOOP_EXCEEDED` audit event, send Telegram alert
  - Token accounting: records total tool_calls_count, tool_input_tokens, tool_output_tokens in `persona_runs`
  - When fallback triggers (3 consecutive failures), falls back to bulk injection for that invocation
  - Existing non-tool calls continue to work unchanged (backward compatible)
  - 100% test coverage for tool loop paths
- **Effort**: L

---

### TASK-008: Feature Flags — system_state Integration

- **Description**: Add getter/setter for `tool_calling_enabled` and `reflection_loop_enabled` to `db/session.py` (or config.py). Wire into orchestrator decision points.
- **Requirement Mapping**: REQ-COMPAT-04-1, REQ-COMPAT-04-2, REQ-COMPAT-04-3
- **Dependencies**: TASK-002 (DB columns exist)
- **Files to Modify**:
  - `src/trading/db/session.py` (extend `get_system_state`, `update_system_state`)
- **Files to Create**:
  - `tests/test_feature_flags.py`
- **Acceptance Criteria**:
  - `get_system_state()` returns `tool_calling_enabled` and `reflection_loop_enabled` booleans
  - `update_system_state(tool_calling_enabled=True)` updates DB + writes audit_log event `TOOL_CALLING_ACTIVATED` / `TOOL_CALLING_DEACTIVATED`
  - Same for `reflection_loop_enabled` with events `REFLECTION_LOOP_ACTIVATED` / `REFLECTION_LOOP_DEACTIVATED`
  - Default values are `false` after migration
- **Effort**: S

---

### TASK-009: Persona Tool Integration — Per-Persona Tool Sets + Orchestrator Wiring

- **Description**: Modify orchestrator to check `tool_calling_enabled` flag and conditionally pass tool definitions to each persona call. Use `get_tools_for_persona()` from registry.
- **Requirement Mapping**: REQ-PTOOL-02-3, REQ-PTOOL-02-4, REQ-PTOOL-02-5, REQ-PTOOL-02-6, REQ-COMPAT-04-5
- **Dependencies**: TASK-007 (base.py tool loop), TASK-008 (feature flags), TASK-003 (registry)
- **Files to Modify**:
  - `src/trading/personas/orchestrator.py` (conditional tool passing)
  - `src/trading/personas/micro.py` (accept tools parameter)
  - `src/trading/personas/decision.py` (accept tools parameter)
  - `src/trading/personas/risk.py` (accept tools parameter)
  - `src/trading/personas/macro.py` (accept tools parameter)
- **Files to Create**:
  - `tests/personas/test_tool_integration.py`
- **Acceptance Criteria**:
  - When `tool_calling_enabled=true`: persona calls include tool definitions via registry
  - When `tool_calling_enabled=false`: persona calls use existing bulk injection (no tools parameter)
  - Macro gets: get_macro_indicators, get_global_assets, get_static_context, get_active_memory
  - Micro gets: get_ticker_technicals, get_ticker_fundamentals, get_ticker_flows, get_recent_disclosures, get_static_context, get_active_memory, get_watchlist
  - Decision gets: get_portfolio_status, get_ticker_technicals, get_ticker_fundamentals, get_static_context, get_active_memory
  - Risk gets: get_portfolio_status, get_ticker_technicals, get_ticker_flows
  - Phase B support: per-persona enablement (Micro first, others later)
- **Effort**: L

---

### TASK-010: Reflection Loop — Decision Re-invoke + Risk Re-evaluate

- **Description**: Implement the REJECT reflection loop in `orchestrator.py`. When Risk returns REJECT and `reflection_loop_enabled=true`, extract feedback, re-invoke Decision with context, re-invoke Risk on revised signal. Max 2 rounds, 30-second timeout, withdrawal support.
- **Requirement Mapping**: REQ-REFL-03-1 through REQ-REFL-03-10
- **Dependencies**: TASK-008 (feature flag), TASK-007 (stable persona calls)
- **Files to Modify**:
  - `src/trading/personas/orchestrator.py` (add reflection logic after REJECT)
- **Files to Create**:
  - `tests/personas/test_reflection_loop.py`
- **Acceptance Criteria**:
  - On REJECT: extract `rationale` + `concerns` from Risk response
  - Build `rejection_feedback` dict with: round, risk_verdict, risk_rationale, risk_concerns, original_signal, instruction
  - Re-invoke Decision with original input + rejection_feedback
  - If Decision returns `signals: []` or `withdraw: true` -> immediate termination, event `REFLECTION_WITHDRAWN`
  - If Decision returns revised signal -> re-invoke Risk (Risk is unaware of reflection)
  - If Risk APPROVES -> proceed to code-rule check and execution
  - If Risk REJECTs again -> increment round, repeat (max 2 rounds)
  - After 2 rounds of REJECT -> final rejection, no further retry
  - Combined timeout 30s per round; on timeout -> abort, keep original REJECT, event `REFLECTION_TIMEOUT`
  - No concurrent reflection for same cycle (sequential per signal)
  - All rounds persisted to `reflection_rounds` table
  - Telegram briefing: `"[Risk -> REJECT -> Reflection Round N -> APPROVE/REJECT]"`
  - When `reflection_loop_enabled=false`: existing behavior (immediate rejection)
  - 100% test coverage for reflection module
- **Effort**: XL

---

### TASK-011: Telegram Commands — /tool-calling and /reflection

- **Description**: Add `/tool-calling on|off` and `/reflection on|off` command handlers to `telegram_bot.py`. Only from authorized chat_id (60443392).
- **Requirement Mapping**: REQ-COMPAT-04-7
- **Dependencies**: TASK-008 (feature flag setters)
- **Files to Modify**:
  - `src/trading/bot/telegram_bot.py` (add command handlers)
- **Files to Create**:
  - `tests/bot/test_telegram_commands.py`
- **Acceptance Criteria**:
  - `/tool-calling on` sets `tool_calling_enabled=true`, writes audit event, confirms via Telegram within 5s
  - `/tool-calling off` sets `tool_calling_enabled=false`, writes audit event, confirms
  - `/reflection on` sets `reflection_loop_enabled=true`, writes audit event, confirms
  - `/reflection off` sets `reflection_loop_enabled=false`, writes audit event, confirms
  - Unauthorized chat_id is ignored (existing behavior)
  - Response time <= 5 seconds
- **Effort**: S

---

### TASK-012: Daily Report Extensions — Tool + Reflection Stats

- **Description**: Extend `daily_report.py` to include tool usage summary and reflection statistics in the daily report.
- **Requirement Mapping**: REQ-PTOOL-02-9, REQ-REFL-03-7, REQ-NFR-09-4
- **Dependencies**: TASK-009 (tool data flowing), TASK-010 (reflection data flowing)
- **Files to Modify**:
  - `src/trading/reports/daily_report.py`
- **Files to Create**:
  - `tests/reports/test_daily_report_extensions.py`
- **Acceptance Criteria**:
  - Report includes: `"Tool 호출: 총 X회, 평균 Y회/페르소나, 실패 Z건"`
  - Report includes: `"Reflection: 시도 X건, 성공(APPROVE) Y건, 최종 REJECT Z건, 철회 W건"`
  - SPEC-008 cost section additionally includes: tool_calls_total, tool_failures, reflection_rounds, reflection_success_rate
  - Zero-state graceful (if no tool calls or reflections, show 0 values)
- **Effort**: M

---

### TASK-013: Prompt Updates — Tool-Use Instructions

- **Description**: Add tool-use instruction to persona Jinja templates. One line per template guiding the persona to actively query tools.
- **Requirement Mapping**: REQ-PTOOL-02-8
- **Dependencies**: TASK-009 (persona integration)
- **Files to Modify**:
  - `src/trading/personas/prompts/macro.jinja`
  - `src/trading/personas/prompts/micro.jinja`
  - `src/trading/personas/prompts/decision.jinja`
  - `src/trading/personas/prompts/risk.jinja`
- **Acceptance Criteria**:
  - Each template conditionally includes tool-use instruction when tools are provided
  - Instruction text: "필요한 정보가 있으면 제공된 Tool을 호출하여 조회하세요. 불필요한 정보를 미리 요청하지 말고, 분석에 꼭 필요한 데이터만 능동적으로 가져오세요."
  - Existing non-tool prompts remain unchanged (no regression)
- **Effort**: S

---

## 4. Implementation Phases

### Phase A — Infrastructure (Week 1)

**Goal**: Deploy Tool Registry + infrastructure with all feature flags OFF. Zero behavior change.

| Task | Description | Effort |
|------|-------------|--------|
| TASK-001 | Test infrastructure + characterization tests | M |
| TASK-002 | DB Migration 010 | S |
| TASK-003 | Tool Registry schema definitions | M |
| TASK-004 | Tool implementations (10 functions) | L |
| TASK-005 | Tool executor (dispatch, timeout, logging) | M |
| TASK-006 | Tool fallback logic | S |
| TASK-008 | Feature flags (default OFF) | S |

**Validation**: All tests pass. Feature flags OFF. Existing pipeline unchanged. Migration applied cleanly.

**Execution Order**: TASK-001 -> TASK-002 (parallel) -> TASK-003 -> TASK-004 -> TASK-005 -> TASK-006 -> TASK-008

---

### Phase B — Micro Tool-Calling (Week 2)

**Goal**: Enable tool-calling for Micro persona only. Monitor token savings + latency.

| Task | Description | Effort |
|------|-------------|--------|
| TASK-007 | base.py tool-use loop | L |
| TASK-009 (partial) | Persona integration — Micro only | M |
| TASK-013 (partial) | Prompt update — micro.jinja | S |
| TASK-011 (partial) | Telegram /tool-calling command | S |

**Validation**: Micro runs with tool-calling. Other personas unchanged. Token reduction measurable. Fallback triggers correctly on tool failures.

**Execution Order**: TASK-007 -> TASK-009 (Micro) -> TASK-013 (micro.jinja) -> TASK-011

---

### Phase C — Full Tool-Calling (Week 3)

**Goal**: Enable tool-calling for Macro + Decision + Risk personas.

| Task | Description | Effort |
|------|-------------|--------|
| TASK-009 (complete) | Extend to all personas | M |
| TASK-013 (complete) | All prompt updates | S |

**Validation**: All 4 personas use tool-calling. Full pipeline end-to-end. Token reduction across all personas. SPEC-008 cache hit rate maintained.

**Execution Order**: TASK-009 (remaining) -> TASK-013 (remaining)

---

### Phase D — Reflection Loop (Week 4)

**Goal**: Enable Reflection Loop. Monitor reflection rate + outcomes + cost.

| Task | Description | Effort |
|------|-------------|--------|
| TASK-010 | Reflection Loop implementation | XL |
| TASK-011 (complete) | /reflection command | S |
| TASK-012 | Daily report extensions | M |

**Validation**: Reflection triggers on REJECT. Max 2 rounds enforced. Timeout works. Daily report shows stats. Cost within budget.

**Execution Order**: TASK-010 -> TASK-011 (complete) -> TASK-012

---

## 5. Dependency Diagram

```
TASK-001 (tests) ──┐
                   ├──→ TASK-003 (registry) ──→ TASK-004 (tools) ──→ TASK-005 (executor) ──→ TASK-006 (fallback)
TASK-002 (migration)──┘                                                    │                         │
       │                                                                   ▼                         ▼
       └──→ TASK-008 (flags) ──────────────────────────────────────→ TASK-007 (base.py loop)
                   │                                                       │
                   │                                                       ▼
                   ├──────────────────────────────────────────────→ TASK-009 (persona integration)
                   │                                                       │
                   ├──────────────────────────────────────────────→ TASK-010 (reflection loop)
                   │                                                       │
                   └──→ TASK-011 (telegram cmds)                           ▼
                                                                   TASK-013 (prompts)
                                                                           │
                                                                           ▼
                                                                   TASK-012 (daily report)
```

---

## 6. Risk Assessment

### Technical Risks

| Risk | Impact | Probability | Mitigation |
|------|--------|-------------|------------|
| No existing test suite — characterization tests effort | Schedule slip | HIGH | TASK-001 allocated as M effort; focus on critical paths only |
| Anthropic tool-use API multi-round behavior edge cases | Incorrect loop handling | LOW | Well-documented API; max 8 rounds hard limit prevents runaway |
| Tool 호출 latency 누적 (multi-round * 500ms/round) | 60-second event trigger breach | LOW | 5s timeout per tool + 8-round limit = max 40s; pre-market has 90min |
| Reflection oscillation (Decision keeps revising, Risk keeps rejecting) | Wasted tokens + delay | LOW | Hard limit of 2 rounds; after 2nd REJECT, final rejection |
| Fallback path regression after tool-calling deployment | System failure on tool outage | LOW | context.py functions preserved; fallback uses exact same code path |
| DB migration failure on production | System downtime | LOW | ALTER TABLE ADD COLUMN is non-blocking; reversible with DROP COLUMN |

### Operational Risks

| Risk | Impact | Probability | Mitigation |
|------|--------|-------------|------------|
| Feature flag accidental toggle | Unintended behavior switch | LOW | Telegram confirmation + audit_log + authorized chat_id only |
| Reflection causing pre-market timing miss | Delayed market entry | LOW | Pre-market starts 07:30, executes at 09:00 (90min buffer) |
| Monthly cost exceeding budget with reflection | Financial overrun | MEDIUM | Max 2 rounds + Sonnet pricing; estimated 3-5만원/month; daily monitoring |

---

## 7. Technology Stack

### New Dependencies

None required. All functionality uses existing stack:
- `anthropic` SDK (already installed) — native `tools` parameter support
- `asyncio` or `concurrent.futures` — for 5-second timeout enforcement
- `hashlib` — for input_hash (SHA-256) in tool_call_log

### Existing Libraries (no updates needed)

| Library | Current Usage | SPEC-009 Usage |
|---------|---------------|----------------|
| `anthropic` | Single API call | Multi-round tool-use loop |
| `jinja2` | Prompt templates | Conditional tool-use instruction |
| `psycopg` v3 | DB operations | New tables + columns |
| `structlog` | Logging | Tool execution logging |

---

## 8. Design Decisions

### D-1: Extend call_persona vs. New Function

**Decision**: Extend existing `call_persona()` with optional `tools` parameter.
**Rationale**: Backward compatible; existing calls without `tools` work unchanged. Avoids code duplication.

### D-2: Timeout Implementation

**Decision**: Use `concurrent.futures.ThreadPoolExecutor` with `timeout=5` for tool execution.
**Rationale**: Simple, well-tested pattern. Tools are I/O-bound (DB queries). No async required.

### D-3: Per-Invocation Fallback (not Global)

**Decision**: Fallback counter resets per persona invocation, not globally.
**Rationale**: Transient failures should not permanently disable tool-calling. Next invocation starts fresh.

### D-4: Reflection Loop in Orchestrator (not Separate Module)

**Decision**: Reflection logic lives in `orchestrator.py` near the existing REJECT handling.
**Rationale**: Minimal code movement. The reflection is a direct extension of the REJECT path (line 253-254). Colocation improves readability.

### D-5: Risk SoD Preservation

**Decision**: Risk persona receives revised signal with identical input structure as original.
**Rationale**: No metadata leak (no "this is round 2"). Risk evaluates independently every time.

---

## 9. Quality Gates

### Coverage Requirements

| Module | Target | Rationale |
|--------|--------|-----------|
| `src/trading/tools/registry.py` | 100% | Schema accuracy is critical |
| `src/trading/tools/executor.py` | 100% | Timeout/fallback logic must be reliable |
| `src/trading/tools/fallback.py` | 100% | Fallback path is safety-critical |
| `src/trading/tools/market_tools.py` | 85% | Wrapper functions; some edge cases |
| `src/trading/tools/context_tools.py` | 85% | Wrapper functions |
| `src/trading/tools/portfolio_tools.py` | 85% | Wrapper functions |
| `src/trading/personas/base.py` (tool loop) | 100% | Multi-round loop must be robust |
| `src/trading/personas/orchestrator.py` (reflection) | 100% | State machine must be correct |

### Integration Test Requirements

- [ ] Full Pre-market cycle (Tool-calling ON + Reflection ON) -> trade execution
- [ ] Full Pre-market cycle (Tool-calling OFF + Reflection OFF) -> existing behavior
- [ ] Tool 3x consecutive failure -> fallback -> bulk injection completion
- [ ] Reflection 2 rounds -> final REJECT -> signal discarded + audit complete
- [ ] Feature flag toggle via Telegram -> immediate reflection
- [ ] SPEC-008 cache + tool-calling coexistence -> cache hit rate >= 50%

---

## 10. Handover to manager-ddd

Upon approval, the following is passed to the DDD implementation agent:

- **TAG Chain**: TASK-001 through TASK-013 in dependency order
- **Library Versions**: No new libraries; existing `anthropic`, `psycopg`, `jinja2`, `structlog`
- **Key Decisions**: D-1 through D-5 above
- **Critical Constraints**:
  - context.py functions MUST NOT be removed (fallback path)
  - Risk persona prompt MUST NOT include reflection awareness
  - Feature flags default to false
  - Max 8 tool rounds, max 2 reflection rounds (hard limits)
  - DB migration must be reversible
  - Tests written BEFORE or alongside implementation (DDD PRESERVE phase)
