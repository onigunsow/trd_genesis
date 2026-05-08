---
id: SPEC-TRADING-015
version: 0.1.0
status: draft
created: 2026-05-08
updated: 2026-05-08
author: onigunsow
priority: high
issue_number: 0
domain: TRADING
title: "All Personas to Claude Code CLI (Zero API Cost)"
related_specs:
  - SPEC-TRADING-014
  - SPEC-TRADING-009
  - SPEC-TRADING-008
  - SPEC-TRADING-010
  - SPEC-TRADING-001
---

# SPEC-TRADING-015 -- All Personas to Claude Code CLI (Zero API Cost)

## HISTORY

| Date | Version | Changes | Author |
|---|---|---|---|
| 2026-05-08 | 0.1.0 | Initial draft -- 7 modules, pre-compute tool data, CLI bridge, host watcher | onigunsow |

## Scope Summary

Migrate all 5 persona API calls from direct Anthropic API (`anthropic` SDK) to Claude Code CLI (`claude -p`) running on the host, achieving **zero daily API cost** (~1,850 KRW/day -> 0 KRW/day) by leveraging the existing Max 5x subscription.

Current state: 5 personas call Anthropic API directly inside Docker container.
- Micro (Sonnet), Decision (Sonnet), Risk (Sonnet) -- per-cycle
- Macro (Opus) -- weekly
- DailyReport (Haiku) -- daily

Target state: Container builds prompts with pre-computed tool data, exports to shared volume. Host watcher detects files and runs `claude -p`. Container imports results and continues pipeline.

**Key architectural shift**: Replace multi-turn tool-calling (LLM requests tools -> code executes -> LLM responds) with single-turn pre-computed prompts (code pre-runs ALL tools -> embeds results in prompt -> CLI responds). This is deterministic, faster (no round-trips), and eliminates tool-calling parsing complexity.

### Cost Impact

| Item | Before (API) | After (CLI) | Savings |
|---|---|---|---|
| Daily persona calls | ~1,850 KRW | 0 KRW | 100% |
| Monthly total | ~55,000 KRW | 0 KRW | ~55,000 KRW/month |
| Yearly | ~660,000 KRW | 0 KRW | ~660,000 KRW/year |

### Rate Limit Consideration

Max 5x subscription provides sufficient throughput for the persona pipeline. Typical daily usage: ~10-15 CLI calls (5 pre-market + 4-5 intraday + 1 daily report + 1 weekly macro). Max subscription's per-hour limit comfortably covers this pattern.

---

## Environment

- Existing SPEC-TRADING-001 infrastructure -- Docker compose, Postgres 16-alpine, Telegram
- Existing 5-persona system -- base.py `call_persona()`, orchestrator.py, 14 tools in registry
- Existing prompt templates -- 6 Jinja2 templates in `personas/prompts/`
- Existing tool executor -- `tools/executor.py` with `execute_tool()` function
- Existing CLI pattern -- `scripts/analyze_news.sh` (SPEC-014, proven)
- Claude Code CLI at `/home/onigunsow/.nvm/versions/node/v24.13.0/bin/claude`
- Max 5x subscription (no per-call API cost)
- Shared volume: `data/` directory mounted in both container and host
- New directories: `data/persona_calls/`, `data/persona_results/`
- New host script: `scripts/persona_watcher.sh`
- New module: `src/trading/personas/cli_bridge.py`
- New module: `src/trading/personas/prompt_builder.py`
- Feature flag: `cli_personas_enabled` in `system_state` table

## Assumptions

- A-1: Max 5x subscription rate limits (messages/hour) are sufficient for persona pipeline throughput (~15 calls/day)
- A-2: Claude Code CLI (`claude -p`) produces equivalent analysis quality to Anthropic API for the same prompt content
- A-3: Single-turn pre-computed prompts provide the same or better analysis quality as multi-turn tool-calling, because all data is still available to the LLM
- A-4: File-based IPC (shared volume) latency is negligible (<100ms for read/write)
- A-5: Host watcher process can be kept running persistently (tmux/systemd)
- A-6: Docker container can poll `data/persona_results/` with acceptable latency (2-second poll interval)
- A-7: CLI response parsing (plain text -> structured JSON) is reliable when prompt explicitly requests JSON output
- A-8: The `--max-turns 1` flag prevents any tool-calling behavior in CLI mode

---

## Requirements

### Module 1: Prompt Builder (REQ-BUILDER-01)

**REQ-BUILDER-01-1** (Event-Driven): **When** a persona call is requested, **then** the prompt builder shall pre-execute ALL tools assigned to that persona in the tool registry and embed results in the prompt text.

**REQ-BUILDER-01-2** (Event-Driven): **When** building a prompt for a persona, **then** the builder shall render the existing Jinja2 system prompt template with the standard context variables AND append a `[PRE-COMPUTED TOOL DATA]` section containing all tool execution results.

**REQ-BUILDER-01-3** (State-Driven): **While** a tool execution fails during pre-computation, **then** the builder shall include the tool name with an `(unavailable)` marker and continue with remaining tools.

**REQ-BUILDER-01-4** (Ubiquitous): The prompt builder shall use the same tool registry (`PERSONA_TOOLS` in `registry.py`) as the current API-based system to determine which tools to execute per persona.

**REQ-BUILDER-01-5** (Event-Driven): **When** building a Decision persona prompt after a Micro result, **then** the builder shall inject the Micro response summary into the prompt context alongside pre-computed tool data.

**REQ-BUILDER-01-6** (Event-Driven): **When** building a Risk persona prompt, **then** the builder shall inject the Decision signal being evaluated into the prompt alongside pre-computed tool data.

**REQ-BUILDER-01-7** (Ubiquitous): The prompt builder shall include explicit JSON output instructions in the user message section, matching the schema expected by each persona's response parser.

**REQ-BUILDER-01-8** (Event-Driven): **When** a reflection loop re-invokes Decision, **then** the builder shall append `rejection_feedback` context to the Decision prompt, preserving the reflection loop mechanism.

### Module 2: CLI Bridge (REQ-BRIDGE-02)

**REQ-BRIDGE-02-1** (Event-Driven): **When** a persona call is dispatched via CLI mode, **then** the CLI bridge shall write a JSON call file to `data/persona_calls/{persona}_{cycle}_{timestamp}.json` containing the full prompt text, persona name, expected response format, and timeout.

**REQ-BRIDGE-02-2** (Event-Driven): **When** a call file is written, **then** the CLI bridge shall poll `data/persona_results/{same_filename}.json` for the response, with a configurable timeout (default 180 seconds).

**REQ-BRIDGE-02-3** (Event-Driven): **When** a result file is detected, **then** the CLI bridge shall parse the response text, attempt JSON extraction using the existing `_extract_json()` logic, and return a `PersonaResult` object compatible with the current orchestrator.

**REQ-BRIDGE-02-4** (Unwanted): The CLI bridge shall NOT persist token usage or cost data to `persona_runs` table, since CLI calls have zero cost. Fields `input_tokens`, `output_tokens`, `cost_krw` shall be recorded as 0.

**REQ-BRIDGE-02-5** (Event-Driven): **When** the poll timeout expires without a result file, **then** the CLI bridge shall raise a `CLITimeoutError` and the fallback mechanism (REQ-FALLBACK-06) shall activate.

**REQ-BRIDGE-02-6** (Ubiquitous): The CLI bridge shall persist every persona call to `persona_runs` table with `model='cli-claude-max'` to maintain full audit trail.

**REQ-BRIDGE-02-7** (Event-Driven): **When** a result file is successfully imported, **then** the CLI bridge shall delete both the call file and result file to prevent stale data accumulation.

### Module 3: Host Runner Script (REQ-RUNNER-03)

**REQ-RUNNER-03-1** (Ubiquitous): A host-side watcher script (`scripts/persona_watcher.sh`) shall continuously monitor `data/persona_calls/` for new `.json` files and execute `claude -p` for each detected file.

**REQ-RUNNER-03-2** (Event-Driven): **When** a new call file is detected, **then** the watcher shall extract the prompt, pipe it to `claude -p --max-turns 1`, and write the CLI response to `data/persona_results/{same_filename}.json`.

**REQ-RUNNER-03-3** (Event-Driven): **When** the Claude CLI call fails (non-zero exit code), **then** the watcher shall write an error result file with `{"error": "<message>", "exit_code": <code>}` to allow the container to detect and handle the failure.

**REQ-RUNNER-03-4** (State-Driven): **While** the watcher is running, **then** it shall log all operations (file detection, CLI invocation start/end, byte counts, errors) to `logs/persona_watcher.log`.

**REQ-RUNNER-03-5** (Ubiquitous): The watcher shall poll every 2 seconds and process files in FIFO order (sorted by filename timestamp).

**REQ-RUNNER-03-6** (Event-Driven): **When** the watcher process dies, **then** it shall be auto-restarted by the host supervisor (systemd or tmux respawn).

**REQ-RUNNER-03-7** (Event-Driven): **When** processing a call file, **then** the watcher shall remove the call file from `data/persona_calls/` after writing the result to prevent duplicate processing.

### Module 4: Orchestrator Refactor (REQ-ORCH-04)

**REQ-ORCH-04-1** (State-Driven): **While** `cli_personas_enabled` is `true` in system_state, **then** the orchestrator shall route all persona calls through the CLI bridge instead of the Anthropic API.

**REQ-ORCH-04-2** (State-Driven): **While** `cli_personas_enabled` is `false`, **then** the orchestrator shall use the existing Anthropic API `call_persona()` path with no behavioral change.

**REQ-ORCH-04-3** (Ubiquitous): The orchestrator refactoring shall preserve the existing persona sequencing: Micro -> Decision -> Risk -> Execute (pre-market), and Decision -> Risk -> Execute (intraday/event).

**REQ-ORCH-04-4** (Ubiquitous): The orchestrator shall preserve the existing Telegram briefing format and content for all persona calls, regardless of whether CLI or API mode is used.

**REQ-ORCH-04-5** (Event-Driven): **When** the CLI bridge returns a `PersonaResult`, **then** the orchestrator shall process it identically to an API-returned result: extract signals, invoke downstream personas, execute orders.

**REQ-ORCH-04-6** (Event-Driven): **When** a Risk persona returns REJECT and `reflection_loop_enabled` is true, **then** the orchestrator shall run the existing reflection loop using CLI bridge calls for both Decision re-invoke and Risk re-evaluate.

**REQ-ORCH-04-7** (Ubiquitous): The orchestrator shall pass the `model` parameter from Model Router to the CLI bridge for audit logging, even though CLI does not use model selection.

### Module 5: Tool Pre-computation (REQ-PRECOMP-05)

**REQ-PRECOMP-05-1** (Event-Driven): **When** pre-computing tools for a persona, **then** the system shall execute each tool function from the dispatch table (`executor.py`) with the persona's default parameters.

**REQ-PRECOMP-05-2** (Ubiquitous): Tool pre-computation shall respect existing feature flags (`jit_pipeline_enabled`, `prototype_risk_enabled`, `dynamic_thresholds_enabled`) when determining which tools to execute.

**REQ-PRECOMP-05-3** (Event-Driven): **When** a tool requires ticker-specific parameters (e.g., `get_ticker_technicals`), **then** the pre-computation shall execute the tool for ALL tickers in the current context (watchlist tickers for Micro, signal tickers for Decision/Risk).

**REQ-PRECOMP-05-4** (Ubiquitous): Tool pre-computation shall use the existing 5-second timeout per tool (`TOOL_TIMEOUT_SECONDS`) and include timeout/error information in the embedded results.

**REQ-PRECOMP-05-5** (Ubiquitous): Tool pre-computation results shall be logged to `tool_call_log` table with `persona_run_id=NULL` (since the persona run is not yet created).

**REQ-PRECOMP-05-6** (Event-Driven): **When** pre-computing tools for the Micro persona, **then** the system shall execute ticker-specific tools for the expanded watchlist (DEFAULT_WATCHLIST + screened tickers, up to 20 tickers).

**REQ-PRECOMP-05-7** (Event-Driven): **When** pre-computing tools for the Decision persona, **then** the system shall execute ticker-specific tools for the buy/sell candidate tickers from the Micro result.

### Module 6: Fallback and Feature Flag (REQ-FALLBACK-06)

**REQ-FALLBACK-06-1** (Ubiquitous): A feature flag `cli_personas_enabled` shall exist in the `system_state` table, defaulting to `false`.

**REQ-FALLBACK-06-2** (Event-Driven): **When** a CLI call fails (timeout, watcher down, parse error), **then** the system shall fall back to direct Haiku API call (`claude-haiku-4-5`) for that specific persona invocation.

**REQ-FALLBACK-06-3** (Event-Driven): **When** fallback to Haiku API occurs, **then** the system shall send a Telegram alert: `"CLI fallback: {persona} -> Haiku API ({reason})"`.

**REQ-FALLBACK-06-4** (Event-Driven): **When** 3 consecutive CLI failures occur within a single cycle, **then** the system shall disable `cli_personas_enabled` and send a Telegram alert: `"CLI mode auto-disabled after 3 consecutive failures"`.

**REQ-FALLBACK-06-5** (State-Driven): **While** `cli_personas_enabled` is being toggled via Telegram `/cli_on` or `/cli_off` commands, **then** the system shall update system_state and audit the change.

**REQ-FALLBACK-06-6** (Unwanted): The fallback shall NOT use Sonnet/Opus API (high cost). Fallback is strictly Haiku only, as the cheapest available model for degraded service.

**REQ-FALLBACK-06-7** (Event-Driven): **When** fallback Haiku API call also fails, **then** the system shall skip that persona invocation, log the double-failure, and send a Telegram error alert.

### Module 7: Scheduler Integration (REQ-SCHED-07)

**REQ-SCHED-07-1** (Ubiquitous): The pre-market pipeline timing shall account for CLI round-trip latency: container export (~1s) + watcher detection (~2s) + CLI execution (~30s) + result import (~1s) per persona.

**REQ-SCHED-07-2** (State-Driven): **While** using CLI mode, **then** the scheduler shall allocate 3 minutes for the full pre-market pipeline (Micro + Decision + Risk) instead of the current 3 minutes (no net increase).

**REQ-SCHED-07-3** (Event-Driven): **When** the watcher is not running (detected by absence of heartbeat file), **then** the scheduler shall log a warning and proceed with API fallback for that cycle.

**REQ-SCHED-07-4** (Ubiquitous): The host watcher heartbeat shall be a file `data/persona_watcher.heartbeat` updated every 30 seconds by the watcher process.

**REQ-SCHED-07-5** (Event-Driven): **When** starting a cycle, **then** the container shall check watcher heartbeat staleness (>60 seconds = stale) before choosing CLI vs API path.

---

## Specifications

### S-1: Call File JSON Schema

```json
{
  "persona": "micro|decision|risk|macro|daily_report",
  "cycle_kind": "pre_market|intraday|event|weekly|manual",
  "timestamp": "2026-05-08T07:30:00+09:00",
  "prompt": "<full prompt text with pre-computed tool data>",
  "expect_json": true,
  "timeout_seconds": 180,
  "metadata": {
    "model_for_audit": "claude-sonnet-4-6",
    "trigger_context": {},
    "run_context": {
      "macro_run_id": null,
      "micro_run_id": 123,
      "decision_run_id": null
    }
  }
}
```

### S-2: Result File JSON Schema

```json
{
  "persona": "micro",
  "timestamp": "2026-05-08T07:30:35+09:00",
  "response_text": "<raw CLI output>",
  "execution_seconds": 28.5,
  "exit_code": 0,
  "error": null
}
```

### S-3: Pre-computed Tool Data Prompt Section

```
=== PRE-COMPUTED TOOL DATA ===

[get_ticker_technicals: 005930]
{"ticker": "005930", "last_close": 72500, "ma20": 71200, "ma60": 69800, "rsi14": 58.3, ...}

[get_ticker_technicals: 000660]
{"ticker": "000660", ...}

[get_ticker_flows: 005930]
{"ticker": "005930", "foreign_net_5d": 15200, ...}

[get_portfolio_status]
{"total_assets": 10000000, "cash_d2": 9500000, "holdings": [...]}

[get_static_context: intelligence_micro] (unavailable)

=== END TOOL DATA ===
```

### S-4: Pipeline Timing (Pre-market Example)

```
07:30:00  Container: pre-compute Micro tools (all tickers) -> 3-5s
07:30:05  Container: render Micro prompt + export call file
07:30:07  Host watcher: detect file -> claude -p (~30s)
07:30:37  Container: detect result -> import -> parse JSON
07:30:40  Container: pre-compute Decision tools (candidate tickers)
07:30:42  Container: render Decision prompt (with Micro result) -> export
07:30:44  Host watcher: detect file -> claude -p (~30s)
07:31:14  Container: detect result -> import -> parse JSON -> extract signals
07:31:17  Container: pre-compute Risk tools (signal tickers)
07:31:19  Container: render Risk prompt (with signal) -> export
07:31:21  Host watcher: detect file -> claude -p (~20s)
07:31:41  Container: detect result -> import -> APPROVE/REJECT -> execute

Total: ~100 seconds for full pipeline
```

### S-5: Feature Flag Management

| Flag | Default | Toggle Command | Scope |
|---|---|---|---|
| `cli_personas_enabled` | `false` | `/cli_on`, `/cli_off` | All personas |

### S-6: Fallback Decision Matrix

| Condition | Action | Alert |
|---|---|---|
| Watcher heartbeat stale (>60s) | Use Haiku API for entire cycle | Telegram warning |
| Single CLI call timeout (>180s) | Haiku fallback for that persona only | Telegram info |
| CLI parse error (no valid JSON) | Retry once, then Haiku fallback | Telegram warning |
| 3 consecutive failures in cycle | Disable cli_personas_enabled | Telegram critical |
| Haiku fallback also fails | Skip persona, continue pipeline | Telegram error |

### S-7: Audit Trail

All persona calls (CLI or API) are logged to `persona_runs` with:

| Field | CLI Mode Value | API Mode Value |
|---|---|---|
| `model` | `cli-claude-max` | `claude-sonnet-4-6` etc. |
| `input_tokens` | 0 | actual count |
| `output_tokens` | 0 | actual count |
| `cost_krw` | 0.0 | calculated |
| `latency_ms` | CLI round-trip time | API latency |
| `tool_calls_count` | 0 (pre-computed) | actual count |

---

## Constraints

- C-1: Docker container has no Node.js -- cannot run `claude` CLI directly
- C-2: Max subscription rate limits apply -- monitor usage against hourly limits
- C-3: Single-turn only in CLI mode -- no multi-turn tool-calling
- C-4: Timeout per persona CLI call: 180 seconds (hard limit)
- C-5: Fallback uses Haiku API only (not Sonnet/Opus) to minimize cost
- C-6: Host watcher must auto-restart if killed
- C-7: Preserve current Telegram briefing format exactly
- C-8: Pre-computed tool data must not exceed reasonable prompt size (~50K tokens)
- C-9: Feature flag defaults to `false` -- opt-in activation after testing

## Traceability

| Requirement | Module | Acceptance Criteria |
|---|---|---|
| REQ-BUILDER-01-* | Module 1: Prompt Builder | AC-BUILDER-01~08 |
| REQ-BRIDGE-02-* | Module 2: CLI Bridge | AC-BRIDGE-01~07 |
| REQ-RUNNER-03-* | Module 3: Host Runner | AC-RUNNER-01~07 |
| REQ-ORCH-04-* | Module 4: Orchestrator | AC-ORCH-01~07 |
| REQ-PRECOMP-05-* | Module 5: Pre-computation | AC-PRECOMP-01~07 |
| REQ-FALLBACK-06-* | Module 6: Fallback | AC-FALLBACK-01~07 |
| REQ-SCHED-07-* | Module 7: Scheduler | AC-SCHED-01~05 |
