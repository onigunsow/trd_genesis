# SPEC-TRADING-015 Acceptance Criteria

## Module 1: Prompt Builder (AC-BUILDER)

### AC-BUILDER-01: Tool Pre-computation

**Given** the Micro persona is assigned tools [get_ticker_technicals, get_ticker_fundamentals, get_ticker_flows, get_recent_disclosures, get_static_context, get_active_memory, get_watchlist]
**When** the prompt builder is invoked for the Micro persona with watchlist ["005930", "000660", "035420"]
**Then** all ticker-specific tools execute for each ticker (3 tickers x 4 ticker-tools = 12 calls)
**And** non-ticker tools execute once each (3 calls)
**And** the total tool execution count is 15
**And** all results are embedded in the prompt text under `=== PRE-COMPUTED TOOL DATA ===` section

### AC-BUILDER-02: Tool Failure Handling

**Given** tool `get_ticker_technicals` fails with timeout for ticker "035420"
**When** the prompt builder pre-computes tools for the Micro persona
**Then** the prompt includes `[get_ticker_technicals: 035420] (unavailable: timeout)` marker
**And** all other tool results are included normally
**And** the prompt builder does NOT raise an exception

### AC-BUILDER-03: Jinja2 Template Rendering

**Given** a valid Micro persona context with macro_summary and watchlist
**When** the prompt builder renders the prompt
**Then** the system prompt section matches the output of `render_prompt("micro.jinja", **ctx)`
**And** the pre-computed tool data section is appended after the user message

### AC-BUILDER-04: JSON Output Instructions

**Given** persona `expect_json=true`
**When** the prompt builder renders any persona prompt
**Then** the prompt ends with explicit JSON schema instructions matching the persona's expected output format
**And** the instruction includes "Respond with ONLY valid JSON"

### AC-BUILDER-05: Decision Prompt with Micro Result

**Given** Micro persona returned a response with candidates {buy: [{ticker: "005930"}], sell: [], hold: []}
**When** the prompt builder builds the Decision persona prompt
**Then** the Micro candidates are injected into the Decision context
**And** ticker-specific tools are pre-computed for ticker "005930"
**And** portfolio_status and static_context tools are pre-computed once

### AC-BUILDER-06: Risk Prompt with Decision Signal

**Given** Decision persona returned signal {ticker: "005930", side: "buy", qty: 5}
**When** the prompt builder builds the Risk persona prompt
**Then** the decision signal is injected into the Risk context
**And** ticker-specific tools are pre-computed for ticker "005930"

### AC-BUILDER-07: Feature Flag Filtering

**Given** `jit_pipeline_enabled=false` and `prototype_risk_enabled=false` in system_state
**When** the prompt builder determines tools for the Risk persona
**Then** `get_delta_events` and `get_market_prototype_similarity` are excluded
**And** only [get_portfolio_status, get_ticker_technicals, get_ticker_flows] are pre-computed

### AC-BUILDER-08: Reflection Loop Prompt

**Given** Risk returned REJECT with rationale "sector concentration exceeds 30%"
**When** the prompt builder builds the Decision re-invoke prompt for reflection round 1
**Then** the prompt includes `rejection_feedback` section with round=1, risk_rationale, risk_concerns
**And** the instruction text asks Decision to revise or withdraw the signal

---

## Module 2: CLI Bridge (AC-BRIDGE)

### AC-BRIDGE-01: Call File Export

**Given** a fully rendered prompt for the Micro persona in pre_market cycle
**When** the CLI bridge exports the call
**Then** a JSON file is written to `data/persona_calls/micro_pre_market_{timestamp}.json`
**And** the file contains fields: persona, cycle_kind, timestamp, prompt, expect_json, timeout_seconds, metadata
**And** the file size is logged

### AC-BRIDGE-02: Result Import (Success)

**Given** a call file was exported for persona "decision"
**And** the host watcher writes a result file to `data/persona_results/decision_pre_market_{timestamp}.json`
**When** the CLI bridge polls for the result
**Then** the result is detected within 5 seconds of file creation
**And** the response_text is extracted from the result JSON
**And** JSON extraction via `_extract_json()` produces a valid dict
**And** a `PersonaResult` is returned with `persona_run_id` from DB insertion

### AC-BRIDGE-03: Result Import (Timeout)

**Given** a call file was exported with timeout_seconds=180
**And** no result file appears within 180 seconds
**When** the CLI bridge poll loop expires
**Then** a `CLITimeoutError` is raised
**And** the error is logged with persona name and elapsed time

### AC-BRIDGE-04: Zero Cost Recording

**Given** a CLI persona call completes successfully
**When** the result is persisted to persona_runs
**Then** `input_tokens=0`, `output_tokens=0`, `cost_krw=0.0`
**And** `model='cli-claude-max'`
**And** `tool_calls_count=0`, `tool_input_tokens=0`, `tool_output_tokens=0`

### AC-BRIDGE-05: File Cleanup

**Given** a CLI call completes successfully (result imported)
**When** post-processing finishes
**Then** the call file in `data/persona_calls/` is deleted
**And** the result file in `data/persona_results/` is deleted

### AC-BRIDGE-06: Memory Ops Execution

**Given** a CLI response contains `memory_ops` in the response JSON
**When** the CLI bridge processes the result
**Then** `execute_memory_ops()` is called with the persona name, run_id, and response_json
**And** memory_ops failures are logged but do not block the pipeline

### AC-BRIDGE-07: Error Result Handling

**Given** the host watcher writes an error result: `{"error": "CLI exit code 1", "exit_code": 1}`
**When** the CLI bridge reads the result file
**Then** the bridge raises an exception triggering the fallback mechanism
**And** the error details are logged

---

## Module 3: Host Runner (AC-RUNNER)

### AC-RUNNER-01: File Detection

**Given** the watcher is running and monitoring `data/persona_calls/`
**When** a new call file `micro_pre_market_20260508073000.json` is created
**Then** the watcher detects it within 2 seconds (one poll cycle)
**And** logs "Detected call file: micro_pre_market_20260508073000.json"

### AC-RUNNER-02: CLI Execution

**Given** a call file with prompt text of 5000 characters
**When** the watcher processes the file
**Then** the prompt is piped to `claude -p --max-turns 1` via stdin
**And** the CLI response is captured
**And** a result file is written to `data/persona_results/` with response_text and execution_seconds
**And** the original call file is removed from `data/persona_calls/`

### AC-RUNNER-03: CLI Failure Handling

**Given** the Claude CLI returns exit code 1 (failure)
**When** the watcher processes the result
**Then** an error result file is written: `{"error": "...", "exit_code": 1}`
**And** the call file is removed to prevent retry loops
**And** the error is logged to `logs/persona_watcher.log`

### AC-RUNNER-04: FIFO Ordering

**Given** 3 call files exist: micro (07:30:00), decision (07:30:40), risk (07:31:17)
**When** the watcher processes files
**Then** files are processed in timestamp order (micro first, then decision, then risk)

### AC-RUNNER-05: Logging

**Given** the watcher processes a call file for the Decision persona
**When** the CLI returns after 28.5 seconds
**Then** the log file contains: detection time, persona name, prompt size, CLI start/end times, response size, total execution time

### AC-RUNNER-06: Heartbeat

**Given** the watcher is running
**When** 30 seconds have elapsed since the last heartbeat
**Then** the file `data/persona_watcher.heartbeat` is updated with the current timestamp

### AC-RUNNER-07: Call File Removal After Processing

**Given** a call file has been successfully processed (result written)
**When** the watcher completes processing
**Then** the call file is removed from `data/persona_calls/`
**And** only the result file remains in `data/persona_results/`

---

## Module 4: Orchestrator (AC-ORCH)

### AC-ORCH-01: CLI Mode Routing

**Given** `cli_personas_enabled=true` in system_state
**When** `run_pre_market_cycle()` invokes the Micro persona
**Then** the call is routed through `cli_bridge` (not `call_persona` API)
**And** the returned PersonaResult has `cost_krw=0.0`

### AC-ORCH-02: API Mode Unchanged

**Given** `cli_personas_enabled=false` in system_state
**When** `run_pre_market_cycle()` invokes the Micro persona
**Then** the call goes through the existing `call_persona()` API path
**And** token counts and costs are recorded as before

### AC-ORCH-03: Pipeline Sequence Preserved

**Given** CLI mode is enabled
**When** `run_pre_market_cycle()` executes
**Then** the sequence is: Micro -> Decision -> Risk -> Execute
**And** each persona receives the correct upstream data (Micro result -> Decision, Decision signal -> Risk)

### AC-ORCH-04: Telegram Briefing Preserved

**Given** CLI mode is enabled
**When** the Micro persona completes via CLI
**Then** the Telegram briefing message format is identical to API mode:
```
[Micro . cli-claude-max . 07:30]
<summary>
0 in / 0 out / 0 KRW
```

### AC-ORCH-05: Signal Extraction

**Given** Decision persona responds via CLI with JSON containing `signals: [{ticker: "005930", side: "buy", qty: 5}]`
**When** the orchestrator processes the CLI result
**Then** the signal is extracted and passed to Risk persona correctly
**And** `persona_decisions` table records are created

### AC-ORCH-06: Reflection Loop via CLI

**Given** CLI mode is enabled and `reflection_loop_enabled=true`
**And** Risk returns REJECT for a signal
**When** the reflection loop activates
**Then** Decision is re-invoked via CLI bridge (with rejection_feedback in prompt)
**And** Risk is re-invoked via CLI bridge (with revised signal)
**And** the loop respects MAX_REFLECTION_ROUNDS=2 and REFLECTION_ROUND_TIMEOUT=45s

### AC-ORCH-07: Order Execution After CLI APPROVE

**Given** CLI mode is enabled
**And** Risk returns APPROVE via CLI
**When** the orchestrator processes the approval
**Then** the order is submitted via KIS API (same as API mode)
**And** trade briefing is sent via Telegram

---

## Module 5: Tool Pre-computation (AC-PRECOMP)

### AC-PRECOMP-01: Macro Tools Pre-computation

**Given** the Macro persona is requested
**When** tools are pre-computed
**Then** `get_macro_indicators`, `get_global_assets`, `get_static_context`, `get_active_memory` are each called once
**And** results are returned as a dict keyed by tool name

### AC-PRECOMP-02: Micro Tools Per-Ticker

**Given** the Micro persona with expanded watchlist of 20 tickers
**When** tools are pre-computed
**Then** `get_ticker_technicals` is called 20 times (once per ticker)
**And** `get_ticker_fundamentals` is called 20 times
**And** `get_ticker_flows` is called 20 times
**And** `get_recent_disclosures` is called once with all 20 tickers
**And** non-ticker tools are called once each
**And** total pre-computation completes within 30 seconds

### AC-PRECOMP-03: Timeout Handling

**Given** `get_ticker_technicals("035420")` times out after 5 seconds
**When** pre-computation processes this failure
**Then** the result for that tool+ticker combination is `{"error": "timeout", "tool": "get_ticker_technicals"}`
**And** pre-computation continues with remaining tools/tickers
**And** the failure is logged to `tool_call_log`

### AC-PRECOMP-04: Feature Flag Respect

**Given** `jit_pipeline_enabled=false` in system_state
**When** pre-computing tools for the Decision persona
**Then** `get_delta_events` is NOT executed
**And** `get_dynamic_thresholds` is NOT executed (if `dynamic_thresholds_enabled=false`)

### AC-PRECOMP-05: Decision Ticker Scope

**Given** Micro returned buy candidates: ["005930", "000660"] and sell candidates: ["035720"]
**When** pre-computing tools for Decision
**Then** ticker-specific tools run for ["005930", "000660", "035720"]
**And** NOT for the entire Micro watchlist (20 tickers)

### AC-PRECOMP-06: Risk Ticker Scope

**Given** Decision signal: {ticker: "005930", side: "buy", qty: 5}
**When** pre-computing tools for Risk
**Then** ticker-specific tools run for ["005930"] only

### AC-PRECOMP-07: Tool Call Logging

**Given** tools are pre-computed for the Micro persona
**When** pre-computation completes
**Then** each tool execution is logged to `tool_call_log` with `persona_run_id=NULL`
**And** success/failure, execution_ms, and result_bytes are recorded

---

## Module 6: Fallback (AC-FALLBACK)

### AC-FALLBACK-01: Feature Flag Default

**Given** a fresh deployment with no system_state entry for `cli_personas_enabled`
**When** the orchestrator checks the flag
**Then** the flag defaults to `false` (API mode)

### AC-FALLBACK-02: CLI Timeout Fallback

**Given** CLI mode is enabled
**And** the CLI call for Decision persona times out after 180 seconds
**When** the timeout is detected
**Then** the system retries with Haiku API (`claude-haiku-4-5`)
**And** Telegram alert is sent: "CLI fallback: Decision -> Haiku API (timeout)"
**And** the Haiku result is used for downstream processing

### AC-FALLBACK-03: Parse Error Fallback

**Given** CLI mode returns text that cannot be parsed as JSON
**When** JSON extraction fails after retry
**Then** the system falls back to Haiku API for that persona
**And** Telegram alert: "CLI fallback: Decision -> Haiku API (parse_error)"

### AC-FALLBACK-04: Auto-disable on Consecutive Failures

**Given** CLI mode is enabled
**And** 3 consecutive CLI failures occur within a single cycle (e.g., Micro fails, Decision fails, Risk fails)
**When** the 3rd failure is detected
**Then** `cli_personas_enabled` is set to `false` in system_state
**And** audit log entry: "CLI_AUTO_DISABLED"
**And** Telegram critical alert: "CLI mode auto-disabled after 3 consecutive failures"

### AC-FALLBACK-05: Telegram Toggle Commands

**Given** user sends `/cli_on` via Telegram
**When** the bot processes the command
**Then** `cli_personas_enabled` is set to `true` in system_state
**And** audit log entry: "CLI_MODE_ON"
**And** Telegram confirmation: "CLI mode enabled"

**Given** user sends `/cli_off` via Telegram
**When** the bot processes the command
**Then** `cli_personas_enabled` is set to `false`
**And** audit log entry: "CLI_MODE_OFF"
**And** Telegram confirmation: "CLI mode disabled"

### AC-FALLBACK-06: Haiku-only Fallback

**Given** a CLI failure triggers fallback
**When** the system selects the fallback model
**Then** the model is always `claude-haiku-4-5`
**And** never Sonnet or Opus (cost constraint)

### AC-FALLBACK-07: Double Failure Handling

**Given** CLI call fails for the Micro persona
**And** Haiku API fallback also fails (e.g., API key issue)
**When** both failures are detected
**Then** the Micro persona invocation is skipped for this cycle
**And** Telegram error alert: "Double failure: Micro CLI + Haiku API both failed"
**And** the pipeline continues without Micro result (Decision runs with cached/empty micro data)

---

## Module 7: Scheduler Integration (AC-SCHED)

### AC-SCHED-01: Heartbeat Check Before Cycle

**Given** CLI mode is enabled
**And** `data/persona_watcher.heartbeat` was last updated 90 seconds ago (stale)
**When** the scheduler starts a pre-market cycle
**Then** the system detects stale heartbeat (>60s)
**And** logs warning: "Watcher heartbeat stale, falling back to API"
**And** uses API mode for the entire cycle

### AC-SCHED-02: Heartbeat Check (Fresh)

**Given** CLI mode is enabled
**And** `data/persona_watcher.heartbeat` was updated 10 seconds ago (fresh)
**When** the scheduler starts a pre-market cycle
**Then** CLI mode is used for persona calls

### AC-SCHED-03: Pipeline Timing Budget

**Given** CLI mode is enabled
**When** the full pre-market pipeline runs (Micro + Decision + Risk)
**Then** total elapsed time is under 180 seconds (3 minutes)
**And** each persona CLI call completes within 60 seconds

### AC-SCHED-04: Watcher Heartbeat Freshness

**Given** the watcher is running
**When** 30 seconds have elapsed
**Then** `data/persona_watcher.heartbeat` is updated
**And** the timestamp in the file is within 5 seconds of the current system time

### AC-SCHED-05: Missing Heartbeat File

**Given** CLI mode is enabled
**And** `data/persona_watcher.heartbeat` file does not exist
**When** the scheduler checks heartbeat
**Then** the file is treated as stale (watcher not running)
**And** API fallback is used

---

## End-to-End Acceptance Tests

### E2E-01: Full Pre-market Pipeline via CLI

**Given** CLI mode is enabled and watcher is running
**When** `run_pre_market_cycle()` executes
**Then** Micro call goes through CLI bridge -> result imported
**And** Decision call goes through CLI bridge (with Micro result) -> signals extracted
**And** Risk call goes through CLI bridge (per signal) -> APPROVE/REJECT
**And** APPROVE signals are executed via KIS
**And** Telegram briefings are sent for each persona
**And** All persona_runs records have model='cli-claude-max' and cost_krw=0.0
**And** Total pipeline cost is 0 KRW

### E2E-02: Mixed Mode (CLI + Fallback)

**Given** CLI mode is enabled
**And** Micro CLI call succeeds
**And** Decision CLI call times out
**When** the pipeline processes the timeout
**Then** Decision falls back to Haiku API
**And** Haiku result is used for Risk invocation (via CLI)
**And** Pipeline completes with mixed mode
**And** Telegram shows: Micro=cli-claude-max, Decision=claude-haiku-4-5 (fallback), Risk=cli-claude-max

### E2E-03: Reflection Loop via CLI

**Given** CLI mode is enabled and `reflection_loop_enabled=true`
**And** Risk returns REJECT for a signal
**When** reflection loop runs
**Then** Decision re-invoke goes through CLI bridge (with rejection_feedback)
**And** Risk re-invoke goes through CLI bridge (with revised signal)
**And** If APPROVE after reflection: order executes
**And** All reflection rounds are persisted to `reflection_rounds` table

### E2E-04: Feature Flag Toggle During Operation

**Given** CLI mode is enabled and a cycle is in progress
**And** user sends `/cli_off` via Telegram
**When** the next persona call is made (within the same cycle)
**Then** the current cycle continues in CLI mode (flag change takes effect next cycle)
**And** the next cycle uses API mode

---

## Quality Gate Criteria

| Criterion | Target |
|---|---|
| Test coverage for new modules | >= 85% |
| Pre-market pipeline completion time (CLI mode) | < 180 seconds |
| Zero API cost for persona calls in CLI mode | cost_krw = 0.0 for all CLI runs |
| Fallback activation time | < 5 seconds after failure detection |
| Watcher detection latency | < 3 seconds (poll interval + processing) |
| Existing Telegram message format preserved | 100% format compatibility |
| Existing persona_runs audit trail | 100% coverage (CLI and API) |

## Definition of Done

- [ ] All 7 modules implemented and tested
- [ ] Feature flag `cli_personas_enabled` works correctly (on/off/auto-disable)
- [ ] Host watcher runs reliably with heartbeat
- [ ] Fallback to Haiku API works for all failure modes
- [ ] Telegram briefing format unchanged
- [ ] All persona_runs records have correct model and cost fields
- [ ] End-to-end test passes for full pre-market cycle in CLI mode
- [ ] Reflection loop works through CLI bridge
- [ ] systemd/tmux auto-restart for watcher process documented
