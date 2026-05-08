# SPEC-TRADING-015 Research -- Codebase Analysis

## 1. Current API Call Architecture

### 1.1 Entry Point: `base.py::call_persona()`

**Location**: `src/trading/personas/base.py:110-370`

The central function handles all persona API calls. Key characteristics:

- Creates Anthropic client with API key from settings
- Supports multi-turn tool-use loop (up to `MAX_TOOL_ROUNDS=8`)
- Applies prompt caching (`cache_control: ephemeral`) on system prompt
- Tracks token usage (input, output, cache_read, cache_create)
- Computes KRW cost from token counts
- Persists everything to `persona_runs` table
- Handles JSON extraction with retry logic
- Executes memory_ops from response JSON (SPEC-007)

**Migration impact**: This is the PRIMARY target. We need a parallel code path (`cli_bridge.py`) that produces the same `PersonaResult` dataclass but via file-based CLI calls instead of API calls.

### 1.2 Tool-Use Loop (lines 177-254)

Current flow:
1. Initial API call with tools parameter
2. If `stop_reason="tool_use"`, extract tool_use blocks
3. Call `execute_tool()` for each requested tool
4. Append tool_result messages, re-send to API
5. Repeat until `stop_reason="end_turn"` or MAX_TOOL_ROUNDS

**Migration strategy**: Eliminate this loop entirely. Pre-compute ALL tools assigned to the persona and embed results in prompt. The LLM gets the same data in a single turn.

**Advantage**: Deterministic tool execution (same tools always run). No parsing of tool_use blocks. No multi-turn token accumulation. Faster (no API round-trips).

**Risk**: LLM currently chooses WHICH tools to call based on context. Pre-computing ALL tools may include unnecessary data, but prompt size impact is manageable (see S-3 in spec.md).

### 1.3 PersonaResult Dataclass (line 96)

```python
@dataclass
class PersonaResult:
    persona_run_id: int
    response_text: str
    response_json: dict[str, Any] | None
    input_tokens: int
    output_tokens: int
    cost_krw: float
    latency_ms: int
    tool_calls_count: int = 0
    tool_input_tokens: int = 0
    tool_output_tokens: int = 0
```

CLI bridge must return this same dataclass with:
- `input_tokens=0`, `output_tokens=0`, `cost_krw=0.0` (no API cost)
- `tool_calls_count=0` (pre-computed, not LLM-driven)
- `latency_ms` = round-trip time (export + CLI + import)

### 1.4 JSON Extraction (lines 373-400)

`_extract_json()` handles messy LLM output:
- Direct parse if starts with `{`
- Strip ```json fences
- Find first `{` and match braces

This function must be reused by CLI bridge for response parsing.

---

## 2. Orchestrator Architecture

### 2.1 Pipeline Flow (orchestrator.py)

**Pre-market cycle** (`run_pre_market_cycle`, line 486):
1. Load cached Macro result
2. Build Micro input (watchlist + screened tickers)
3. Call Micro persona -> briefing
4. Build Decision input (Macro cache + Micro result + assets)
5. Call Decision persona -> extract signals
6. Per signal: Call Risk persona -> APPROVE/HOLD/REJECT
7. If REJECT + reflection enabled: reflection loop
8. If APPROVE: execute order via KIS

**Event cycle** (`run_event_trigger_cycle`, line 751):
- CAR filter -> Decision -> Risk -> Execute

**Intraday** (`run_intraday_cycle`, line 894):
- Delegates to pre-market with `cycle_kind="intraday"`

**Weekly macro** (`run_weekly_macro`, line 904):
- Single Macro persona call

### 2.2 Inter-persona Data Flow

```
Micro.response_json.candidates -> Decision.dec_input.micro_candidates
Decision.response_json.signals -> Risk.rk_input.decision_signals
Risk.verdict -> Execute or Reject
```

This flow must be preserved exactly in CLI mode. The CLI bridge must return parseable JSON so downstream personas receive correct input.

### 2.3 Reflection Loop (lines 221-426)

Complex flow: Risk REJECT -> build rejection_feedback -> re-invoke Decision -> re-invoke Risk. Up to 2 rounds with 45s timeout per round.

CLI migration: Each reflection round involves 2 CLI calls (Decision + Risk). Total reflection budget: 4 CLI calls max. Each must go through the full export-CLI-import cycle.

### 2.4 Model Router Integration

`resolve_model()` returns model string per persona. In CLI mode, this value is recorded in audit (`model_for_audit`) but does not affect CLI execution (CLI uses whatever model the subscription provides).

---

## 3. Tool System Analysis

### 3.1 Tool Registry (registry.py)

14 tools defined in `TOOL_DEFINITIONS`. Per-persona assignments in `PERSONA_TOOLS`:

| Persona | Tools | Ticker-specific? |
|---|---|---|
| macro | get_macro_indicators, get_global_assets, get_static_context, get_active_memory | No |
| micro | get_ticker_technicals, get_ticker_fundamentals, get_ticker_flows, get_recent_disclosures, get_static_context, get_active_memory, get_watchlist, get_delta_events, get_intraday_price_history | Yes (per ticker) |
| decision | get_portfolio_status, get_ticker_technicals, get_ticker_fundamentals, get_static_context, get_active_memory, get_delta_events, get_dynamic_thresholds | Yes (per ticker) |
| risk | get_portfolio_status, get_ticker_technicals, get_ticker_flows, get_delta_events, get_market_prototype_similarity | Yes (per ticker) |

**Pre-computation complexity**:
- Macro: 4 tools, no ticker iteration. Simple.
- Micro: 9 tools, but ticker-specific tools run for ~20 tickers. ~60-80 tool executions.
- Decision: 7 tools, ticker-specific for candidate tickers (3-5). ~15-25 tool executions.
- Risk: 5 tools, ticker-specific for signal ticker (1-2). ~7-10 tool executions.

### 3.2 Feature-flagged Tools

Conditional tools based on system_state:
- `get_delta_events`, `get_intraday_price_history` -- requires `jit_pipeline_enabled`
- `get_market_prototype_similarity` -- requires `prototype_risk_enabled`
- `get_dynamic_thresholds` -- requires `dynamic_thresholds_enabled`

Pre-computation must respect these flags (reuse `get_tools_for_persona()` logic).

### 3.3 Tool Execution (executor.py)

`execute_tool()` already handles:
- Dispatch by name
- 5-second timeout (15s for portfolio/watchlist)
- Error handling (returns structured error dict)
- Audit logging to `tool_call_log`

Pre-computation can call `execute_tool()` directly, collecting all results into a dict.

---

## 4. Proven CLI Pattern (SPEC-014)

### 4.1 analyze_news.sh

**Location**: `scripts/analyze_news.sh`

Pattern:
1. Read `data/pending_analysis.json`
2. Extract prompt text to temp file
3. Pipe to `claude -p --tools ""` via stdin
4. Write response to `data/analysis_results.json`
5. Remove pending file on success

Key flags:
- `-p`: non-interactive print mode
- `--tools ""`: disable all tool usage (pure text analysis)
- Reads prompt from stdin (pipe)

### 4.2 Adaptation for Persona Watcher

The persona watcher generalizes this pattern:
- Multiple call files (not just one)
- Continuous monitoring (not one-shot)
- `--max-turns 1` instead of `--tools ""` (explicit single-turn)
- Error result files (not just success/failure exit code)
- Heartbeat file for liveness detection

---

## 5. Prompt Templates

### 5.1 Jinja2 Templates

Six templates in `personas/prompts/`:
- `macro.jinja` -- weekly market analysis
- `micro.jinja` -- daily ticker analysis
- `decision.jinja` -- trading signal generation
- `risk.jinja` -- signal verification
- `portfolio.jinja` -- position sizing (M5+)
- `retrospective.jinja` -- weekly review (M5+)

Template rendering: `render_prompt(template_name, **ctx)` in base.py.

### 5.2 Prompt Builder Design

The prompt builder wraps existing template rendering and appends pre-computed data:

```
[System Prompt from Jinja2 template]

[User Message with context variables]

=== PRE-COMPUTED TOOL DATA ===
[Tool results embedded here]
=== END TOOL DATA ===

IMPORTANT: Respond in valid JSON matching the specified schema.
```

This preserves the existing prompt structure while adding tool data.

---

## 6. Database Persistence

### 6.1 persona_runs Table

All persona calls write to `persona_runs`. CLI mode writes with:
- `model = 'cli-claude-max'`
- `input_tokens = 0`, `output_tokens = 0`
- `cost_krw = 0.0`
- `cache_read_tokens = 0`, `cache_creation_tokens = 0`
- `tool_calls_count = 0`
- `latency_ms` = full CLI round-trip

### 6.2 Memory Ops

SPEC-007 memory_ops execution happens post-response in `call_persona()`. CLI bridge must also execute memory_ops if response_json contains them.

---

## 7. Implementation Plan

### Priority: Primary Goals

**M1: Foundation (Modules 1, 2, 5)**
- `prompt_builder.py` -- tool pre-computation + prompt rendering
- `cli_bridge.py` -- file export/import with polling
- Unit tests for both modules

**M2: Host Infrastructure (Module 3)**
- `persona_watcher.sh` -- host watcher with logging
- Heartbeat mechanism
- systemd/tmux setup

**M3: Integration (Modules 4, 6, 7)**
- Orchestrator refactoring with feature flag
- Fallback to Haiku API
- Scheduler timing adjustments

### Priority: Secondary Goals

- End-to-end testing with all 5 personas
- Performance benchmarking (API vs CLI latency/quality)
- Rate limit monitoring dashboard

### Priority: Optional Goals

- Per-persona CLI enable/disable (granular control)
- Prompt size optimization (exclude low-value tool data)
- Response quality comparison framework

---

## 8. Risk Analysis

### R-1: CLI Response Quality (Medium)

**Risk**: CLI may produce different quality responses than API with same prompt.
**Mitigation**: Side-by-side comparison during rollout. Feature flag allows instant rollback.

### R-2: Rate Limiting (Low)

**Risk**: Max subscription hourly limit may be hit during high-activity periods.
**Mitigation**: ~15 calls/day is well within limits. Monitor via watcher log.

### R-3: JSON Parsing Reliability (Medium)

**Risk**: CLI plain text output may not consistently produce valid JSON.
**Mitigation**: Explicit JSON instructions in prompt. `_extract_json()` already handles messy output. Retry + Haiku fallback.

### R-4: Watcher Process Stability (Low)

**Risk**: Host watcher may crash or be killed.
**Mitigation**: Heartbeat detection. systemd auto-restart. Haiku fallback on stale heartbeat.

### R-5: Reflection Loop Latency (Low)

**Risk**: Reflection loop requires 2-4 extra CLI calls, potentially exceeding timeout.
**Mitigation**: 45s per-round timeout already exists. CLI calls (~30s each) fit within budget. Worst case: reflection fails and original REJECT stands.

### R-6: Prompt Size Explosion (Low)

**Risk**: Pre-computing all tools for 20 tickers may produce large prompts.
**Mitigation**: Tool results are compact JSON (~500 bytes each). 20 tickers x 5 tools = ~50KB. Well within CLI input limits.

---

## 9. Files to Create

| File | Purpose |
|---|---|
| `src/trading/personas/prompt_builder.py` | Pre-compute tools + render full prompt |
| `src/trading/personas/cli_bridge.py` | File-based IPC with host CLI |
| `scripts/persona_watcher.sh` | Host-side watcher script |
| `scripts/persona_watcher.service` | systemd unit file (optional) |
| `tests/personas/test_prompt_builder.py` | Prompt builder unit tests |
| `tests/personas/test_cli_bridge.py` | CLI bridge unit tests |

## 10. Files to Modify

| File | Changes |
|---|---|
| `src/trading/personas/base.py` | Add `call_persona_cli()` or route in `call_persona()` |
| `src/trading/personas/orchestrator.py` | Route through CLI bridge when flag enabled |
| `src/trading/bot/telegram_bot.py` | Add `/cli_on`, `/cli_off` commands |
| `src/trading/db/migrations/` | Add system_state default for `cli_personas_enabled` |
