"""Persona base — Anthropic API client + audit + token cost in KRW.

Pricing as of 2026-05 (USD/M tokens):
- Sonnet 4.6: $3 in / $15 out
- Opus 4.7  : $15 in / $75 out
KRW conversion uses approx 1380 KRW/USD (override via ANTHROPIC_KRW_PER_USD env).

SPEC-009 REQ-PTOOL-02-1: Tool-use multi-turn loop support.
SPEC-015 REQ-ORCH-04-1/2: CLI routing via cli_personas_enabled feature flag.
"""

from __future__ import annotations

import json
import logging
import os
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from anthropic import Anthropic
from jinja2 import Environment, FileSystemLoader, StrictUndefined

from trading.config import get_settings, project_root
from trading.db.session import audit, connection

LOG = logging.getLogger(__name__)

KRW_PER_USD = float(os.environ.get("ANTHROPIC_KRW_PER_USD", "1380"))

# SPEC-009 REQ-PTOOL-02-2: Maximum tool rounds per single persona invocation.
MAX_TOOL_ROUNDS: int = 8

PRICING_USD_PER_MTOK = {
    "claude-sonnet-4-6": (3.0, 15.0),
    "claude-opus-4-7":   (15.0, 75.0),
    # SPEC-010 REQ-COST-04-2: Haiku pricing
    "claude-haiku-4-5":  (0.80, 4.0),
    # Fallbacks for dated model IDs.
    "claude-sonnet-4.6": (3.0, 15.0),
    "claude-opus-4.7":   (15.0, 75.0),
    "claude-haiku-4.5":  (0.80, 4.0),
}

# SPEC-008 REQ-CACHE-01-* — Anthropic prompt cache pricing multipliers.
# - cache_creation: input_rate × 1.25 (첫 캐시 작성 시 25% premium)
# - cache_read:     input_rate × 0.10 (재사용 시 90% 할인)
CACHE_CREATE_MULTIPLIER = 1.25
CACHE_READ_MULTIPLIER = 0.10


def _cost_krw(
    model: str,
    in_tok: int,
    out_tok: int,
    cache_read: int = 0,
    cache_create: int = 0,
) -> float:
    """Compute KRW cost honoring Anthropic prompt cache pricing.

    Total input rate splits into:
    - regular input (in_tok - cache_read - cache_create) at full price
    - cache_create at 1.25x
    - cache_read at 0.10x
    """
    pricing = PRICING_USD_PER_MTOK.get(model)
    if not pricing:
        return 0.0
    in_rate, out_rate = pricing
    regular_in = max(0, in_tok - cache_read - cache_create)
    usd = (
        (regular_in / 1_000_000) * in_rate
        + (cache_create / 1_000_000) * in_rate * CACHE_CREATE_MULTIPLIER
        + (cache_read / 1_000_000) * in_rate * CACHE_READ_MULTIPLIER
        + (out_tok / 1_000_000) * out_rate
    )
    return usd * KRW_PER_USD


def _prompt_dir() -> Path:
    return Path(__file__).resolve().parent / "prompts"


def render_prompt(template_name: str, **ctx: Any) -> str:
    """Render a Jinja2 system prompt template under personas/prompts/."""
    env = Environment(
        loader=FileSystemLoader(str(_prompt_dir())),
        undefined=StrictUndefined,
        autoescape=False,
        keep_trailing_newline=True,
    )
    return env.get_template(template_name).render(**ctx)


@dataclass
class PersonaResult:
    persona_run_id: int
    response_text: str
    response_json: dict[str, Any] | None
    input_tokens: int
    output_tokens: int
    cost_krw: float
    latency_ms: int
    # SPEC-009 REQ-PTOOL-02-7: Tool usage accounting.
    tool_calls_count: int = 0
    tool_input_tokens: int = 0
    tool_output_tokens: int = 0


def call_persona(
    *,
    persona_name: str,
    model: str,
    cycle_kind: str,
    system_prompt: str,
    user_message: str,
    trigger_context: dict[str, Any] | None = None,
    max_tokens: int = 4096,
    expect_json: bool = False,
    apply_memory_ops: bool = True,
    tools: list[dict[str, Any]] | None = None,
) -> PersonaResult:
    """Single persona invocation with optional tool-use loop.

    REQ-PTOOL-02-1: When `tools` is provided, implements multi-turn tool-use loop:
    1. Send initial messages with tools parameter
    2. When response has stop_reason="tool_use", execute the requested tool(s)
    3. Append tool_result message and re-send
    4. Repeat until stop_reason="end_turn" or MAX_TOOL_ROUNDS reached

    Persists to persona_runs (REQ-PERSONA-04-2).
    """
    s = get_settings()
    if s.anthropic.api_key is None:
        raise RuntimeError("ANTHROPIC_API_KEY missing — cannot call persona")
    client = Anthropic(api_key=s.anthropic.api_key.get_secret_value())

    start = time.time()
    error: str | None = None
    text = ""
    in_tok = 0
    out_tok = 0
    cache_read = 0
    cache_create = 0
    tool_calls_count = 0
    tool_input_tokens = 0
    tool_output_tokens = 0

    try:
        # SPEC-008 REQ-CACHE-01-1/2 — Mark long, stable system prompt as cacheable.
        system_blocks = [
            {"type": "text", "text": system_prompt, "cache_control": {"type": "ephemeral"}}
        ]
        messages: list[dict[str, Any]] = [{"role": "user", "content": user_message}]

        # Build API call kwargs (include tools only when provided)
        api_kwargs: dict[str, Any] = {
            "model": model,
            "max_tokens": max_tokens,
            "system": system_blocks,
            "messages": messages,
        }
        if tools:
            api_kwargs["tools"] = tools

        msg = client.messages.create(**api_kwargs)

        # Accumulate usage from first call
        usage = msg.usage
        if usage:
            in_tok += usage.input_tokens
            out_tok += usage.output_tokens
            cache_read += getattr(usage, "cache_read_input_tokens", 0) or 0
            cache_create += getattr(usage, "cache_creation_input_tokens", 0) or 0

        # SPEC-009 REQ-PTOOL-02-1: Tool-use multi-turn loop
        if tools:
            from trading.tools.executor import execute_tool
            from trading.tools.fallback import FallbackTracker

            tracker = FallbackTracker(persona_name=persona_name)
            round_count = 0

            while msg.stop_reason == "tool_use" and round_count < MAX_TOOL_ROUNDS:
                round_count += 1

                # Extract tool_use blocks from response
                tool_results: list[dict[str, Any]] = []
                for blk in msg.content:
                    if getattr(blk, "type", "") == "tool_use":
                        tool_calls_count += 1
                        tool_id = blk.id
                        tool_name = blk.name
                        tool_input = blk.input if hasattr(blk, "input") else {}

                        # Track input tokens consumed by tool params
                        input_str = json.dumps(tool_input, default=str)
                        tool_input_tokens += len(input_str) // 4  # Rough estimate

                        # Execute tool with timeout (REQ-TOOL-01-4)
                        result = execute_tool(tool_name, tool_input, persona_run_id=None)

                        # Track fallback (REQ-COMPAT-04-4)
                        success = "error" not in result
                        tracker.record(success)

                        if tracker.should_fallback():
                            # Abort tool loop, caller should fall back to bulk injection
                            LOG.warning(
                                "Tool fallback triggered for %s after %d calls",
                                persona_name, tool_calls_count,
                            )
                            break

                        result_str = json.dumps(result, default=str, ensure_ascii=False)
                        tool_output_tokens += len(result_str) // 4  # Rough estimate

                        tool_results.append({
                            "type": "tool_result",
                            "tool_use_id": tool_id,
                            "content": result_str,
                        })

                if tracker.should_fallback():
                    # Collect any text from last response before breaking
                    for blk in msg.content:
                        if getattr(blk, "type", "") == "text":
                            text += blk.text
                    break

                if not tool_results:
                    break

                # Append assistant message + tool results, re-send
                messages.append({"role": "assistant", "content": msg.content})
                messages.append({"role": "user", "content": tool_results})

                api_kwargs["messages"] = messages
                msg = client.messages.create(**api_kwargs)

                # Accumulate usage from tool round
                usage = msg.usage
                if usage:
                    in_tok += usage.input_tokens
                    out_tok += usage.output_tokens
                    cache_read += getattr(usage, "cache_read_input_tokens", 0) or 0
                    cache_create += getattr(usage, "cache_creation_input_tokens", 0) or 0

            # REQ-PTOOL-02-2: Max tool rounds exceeded
            if tools and msg.stop_reason == "tool_use" and round_count >= MAX_TOOL_ROUNDS:
                LOG.warning(
                    "TOOL_LOOP_EXCEEDED: persona=%s reached %d rounds",
                    persona_name, MAX_TOOL_ROUNDS,
                )
                try:
                    audit(
                        "TOOL_LOOP_EXCEEDED",
                        actor="call_persona",
                        details={
                            "persona_name": persona_name,
                            "max_rounds": MAX_TOOL_ROUNDS,
                            "tool_calls_count": tool_calls_count,
                        },
                    )
                    from trading.alerts import telegram as tg
                    tg.system_briefing(
                        "Tool Loop 초과",
                        f"{persona_name} 페르소나가 {MAX_TOOL_ROUNDS}회 tool 호출 한도 초과. "
                        f"총 호출: {tool_calls_count}회",
                    )
                except Exception:  # noqa: BLE001
                    pass

        # Extract final text from the last message
        for blk in msg.content:
            if getattr(blk, "type", "") == "text":
                text += blk.text

    except Exception as e:  # noqa: BLE001
        error = f"{type(e).__name__}: {e}"
        LOG.exception("persona call failed: %s", persona_name)

    latency_ms = int((time.time() - start) * 1000)
    cost = _cost_krw(model, in_tok, out_tok, cache_read=cache_read, cache_create=cache_create)

    response_json = None
    if expect_json and text:
        try:
            response_json = _extract_json(text)
        except Exception as e:  # noqa: BLE001
            LOG.warning("could not parse persona JSON (attempt 1): %s", e)
            # Retry: ask LLM to fix its JSON (single retry, no tool-use)
            try:
                retry_msg = client.messages.create(
                    model=model,
                    max_tokens=max_tokens,
                    messages=[
                        {"role": "user", "content": "Your previous response had invalid JSON. Return ONLY valid JSON, nothing else."},
                        {"role": "assistant", "content": text[:500]},
                        {"role": "user", "content": "Fix the JSON above. Output ONLY the corrected JSON object."},
                    ],
                )
                retry_text = retry_msg.content[0].text if retry_msg.content else ""
                response_json = _extract_json(retry_text)
                LOG.info("JSON retry succeeded for %s", persona_name)
            except Exception:  # noqa: BLE001
                LOG.warning("JSON retry also failed for %s — proceeding with response_json=None", persona_name)

    # Persist to persona_runs with tool usage accounting (REQ-PTOOL-02-7)
    sql = """
        INSERT INTO persona_runs
            (persona_name, model, cycle_kind, trigger_context,
             prompt, response, response_json,
             input_tokens, output_tokens, cost_krw, latency_ms, error,
             cache_read_tokens, cache_creation_tokens,
             tool_calls_count, tool_input_tokens, tool_output_tokens)
        VALUES (%s,%s,%s,%s::jsonb,%s,%s,%s::jsonb,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
        RETURNING id
    """
    with connection() as conn, conn.cursor() as cur:
        cur.execute(sql, (
            persona_name,
            model,
            cycle_kind,
            json.dumps(trigger_context or {}),
            system_prompt + "\n\n[USER]\n" + user_message,
            text,
            json.dumps(response_json) if response_json is not None else None,
            in_tok,
            out_tok,
            cost,
            latency_ms,
            error,
            cache_read,
            cache_create,
            tool_calls_count,
            tool_input_tokens,
            tool_output_tokens,
        ))
        row = cur.fetchone()
        run_id = row["id"]

    if error:
        raise RuntimeError(error)

    # SPEC-007 — execute memory_ops if persona response contains them.
    if apply_memory_ops and response_json:
        try:
            from trading.personas.memory import execute_memory_ops
            execute_memory_ops(
                persona=persona_name,
                persona_run_id=run_id,
                response_json=response_json,
            )
        except Exception as e:  # noqa: BLE001
            LOG.warning("memory_ops execution failed for persona %s run %s: %s",
                        persona_name, run_id, e)

    return PersonaResult(
        persona_run_id=run_id,
        response_text=text,
        response_json=response_json,
        input_tokens=in_tok,
        output_tokens=out_tok,
        cost_krw=cost,
        latency_ms=latency_ms,
        tool_calls_count=tool_calls_count,
        tool_input_tokens=tool_input_tokens,
        tool_output_tokens=tool_output_tokens,
    )


def _extract_json(text: str) -> dict[str, Any]:
    """Pull the first JSON object out of a free-form text response."""
    text = text.strip()
    # Try direct parse first.
    if text.startswith("{") and text.rstrip().endswith("}"):
        return json.loads(text)
    # Strip ```json fences if present.
    if "```" in text:
        parts = text.split("```")
        for p in parts:
            p = p.strip()
            if p.startswith("json"):
                p = p[len("json"):].strip()
            if p.startswith("{") and p.rstrip().endswith("}"):
                return json.loads(p)
    # Find the first { and matching }
    start = text.find("{")
    if start == -1:
        raise ValueError("no JSON object found")
    depth = 0
    for i, ch in enumerate(text[start:], start=start):
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return json.loads(text[start : i + 1])
    raise ValueError("unbalanced JSON braces")


# ---------------------------------------------------------------------------
# SPEC-015: CLI persona invocation
# ---------------------------------------------------------------------------

# REQ-FALLBACK-06-4: Consecutive CLI failure counter for auto-disable
_cli_failure_count: int = 0
_CLI_AUTO_DISABLE_THRESHOLD: int = 3


def _reset_cli_failures() -> None:
    """Reset consecutive failure counter on CLI success."""
    global _cli_failure_count
    _cli_failure_count = 0


def _record_cli_failure(persona_name: str, reason: str) -> None:
    """Record a CLI failure and auto-disable if threshold reached.

    REQ-FALLBACK-06-3: Sends Telegram alert on each failure.
    REQ-FALLBACK-06-4: Auto-disables cli_personas_enabled after 3 consecutive failures.
    """
    global _cli_failure_count
    _cli_failure_count += 1

    try:
        from trading.alerts import telegram as tg
        tg.system_briefing(
            "CLI fallback",
            f"{persona_name} -> Haiku API ({reason})",
        )
    except Exception:  # noqa: BLE001
        pass

    if _cli_failure_count >= _CLI_AUTO_DISABLE_THRESHOLD:
        try:
            from trading.db.session import update_system_state
            update_system_state(cli_personas_enabled=False, updated_by="auto_disable")
            audit(
                "CLI_AUTO_DISABLED",
                actor="call_persona",
                details={"consecutive_failures": _cli_failure_count},
            )
            from trading.alerts import telegram as tg
            tg.system_briefing(
                "CLI auto-disabled",
                f"CLI mode auto-disabled after {_cli_failure_count} consecutive failures",
            )
        except Exception:  # noqa: BLE001
            pass
        _cli_failure_count = 0


def call_persona_via_cli(
    *,
    persona_name: str,
    model: str,
    cycle_kind: str,
    system_prompt: str,
    user_message: str,
    trigger_context: dict[str, Any] | None = None,
    expect_json: bool = False,
    apply_memory_ops: bool = True,
    tickers: list[str] | None = None,
    input_data: dict[str, Any] | None = None,
    run_context: dict[str, Any] | None = None,
) -> PersonaResult:
    """Invoke a persona via CLI bridge with pre-computed tool data.

    SPEC-015 REQ-ORCH-04-1: Routes persona calls through CLI bridge when enabled.
    REQ-BRIDGE-02-4: Records tokens=0, cost=0 for CLI calls.
    REQ-BRIDGE-02-6: Persists with model='cli-claude-max'.
    REQ-FALLBACK-06-2: Falls back to Haiku API on CLI failure.
    REQ-FALLBACK-06-6: Fallback uses Haiku only (cheapest available).

    Args:
        persona_name: The persona being invoked.
        model: Model name (recorded for audit, not used by CLI).
        cycle_kind: Cycle type.
        system_prompt: Rendered Jinja2 system prompt.
        user_message: User message portion.
        trigger_context: Trigger context for audit trail.
        expect_json: Whether to parse response as JSON.
        apply_memory_ops: Whether to execute SPEC-007 memory_ops.
        tickers: Tickers for tool pre-computation.
        input_data: Raw input data for prompt builder context.
        run_context: Additional metadata (macro_run_id, micro_run_id, etc.).

    Returns:
        PersonaResult with CLI execution results.
    """
    from trading.personas.cli_bridge import (
        CLICallError,
        CLITimeoutError,
        call_persona_cli,
        parse_cli_response,
    )
    from trading.personas.cli_prompt_builder import build_cli_prompt

    start = time.time()

    try:
        # Build single-turn prompt with pre-computed tool data
        full_prompt = build_cli_prompt(
            persona_name=persona_name,
            input_data=input_data or {},
            system_prompt=system_prompt,
            user_message=user_message,
            tickers=tickers,
        )

        # Call CLI bridge (export file, wait for result)
        cli_result = call_persona_cli(
            persona_name=persona_name,
            prompt=full_prompt,
            cycle_kind=cycle_kind,
            model_for_audit=model,
            metadata={
                "trigger_context": trigger_context or {},
                "run_context": run_context or {},
            },
        )

        if cli_result is None:
            raise CLICallError("CLI returned None result")

        response_text = cli_result.get("response_text", "")
        _reset_cli_failures()

    except (CLITimeoutError, CLICallError) as e:
        # REQ-FALLBACK-06-2: Fall back to Haiku API
        reason = str(e)[:100]
        _record_cli_failure(persona_name, reason)

        LOG.warning(
            "CLI failed for %s (%s), falling back to Haiku API",
            persona_name, reason,
        )

        # REQ-FALLBACK-06-6: Haiku only fallback
        try:
            return call_persona(
                persona_name=persona_name,
                model="claude-haiku-4-5",
                cycle_kind=cycle_kind,
                system_prompt=system_prompt,
                user_message=user_message,
                trigger_context=trigger_context,
                expect_json=expect_json,
                apply_memory_ops=apply_memory_ops,
                tools=None,  # No tools in fallback (simpler call)
            )
        except Exception as fallback_err:  # noqa: BLE001
            # REQ-FALLBACK-06-7: Double failure — skip persona
            LOG.exception(
                "Haiku fallback also failed for %s: %s",
                persona_name, fallback_err,
            )
            try:
                from trading.alerts import telegram as tg
                tg.system_briefing(
                    "Double failure",
                    f"{persona_name}: CLI + Haiku both failed. Skipping.",
                )
            except Exception:  # noqa: BLE001
                pass
            raise RuntimeError(
                f"Double failure for {persona_name}: CLI ({reason}) + Haiku ({fallback_err})"
            ) from fallback_err

    latency_ms = int((time.time() - start) * 1000)

    # Parse response JSON
    response_json = None
    if expect_json and response_text:
        response_json = parse_cli_response(response_text)

    # REQ-BRIDGE-02-4/6: Persist to persona_runs with zero cost
    sql = """
        INSERT INTO persona_runs
            (persona_name, model, cycle_kind, trigger_context,
             prompt, response, response_json,
             input_tokens, output_tokens, cost_krw, latency_ms, error,
             cache_read_tokens, cache_creation_tokens,
             tool_calls_count, tool_input_tokens, tool_output_tokens)
        VALUES (%s,%s,%s,%s::jsonb,%s,%s,%s::jsonb,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
        RETURNING id
    """
    with connection() as conn, conn.cursor() as cur:
        cur.execute(sql, (
            persona_name,
            "cli-claude-max",  # REQ-BRIDGE-02-6: Audit model name
            cycle_kind,
            json.dumps(trigger_context or {}),
            system_prompt + "\n\n[USER]\n" + user_message,
            response_text,
            json.dumps(response_json) if response_json is not None else None,
            0,   # input_tokens = 0 (CLI)
            0,   # output_tokens = 0 (CLI)
            0.0, # cost_krw = 0 (CLI)
            latency_ms,
            None,  # no error
            0,   # cache_read_tokens = 0
            0,   # cache_creation_tokens = 0
            0,   # tool_calls_count = 0 (pre-computed)
            0,   # tool_input_tokens = 0
            0,   # tool_output_tokens = 0
        ))
        row = cur.fetchone()
        run_id = row["id"]

    # SPEC-007: Execute memory_ops if response contains them
    if apply_memory_ops and response_json:
        try:
            from trading.personas.memory import execute_memory_ops
            execute_memory_ops(
                persona=persona_name,
                persona_run_id=run_id,
                response_json=response_json,
            )
        except Exception as e:  # noqa: BLE001
            LOG.warning(
                "memory_ops execution failed for CLI persona %s run %s: %s",
                persona_name, run_id, e,
            )

    return PersonaResult(
        persona_run_id=run_id,
        response_text=response_text,
        response_json=response_json,
        input_tokens=0,
        output_tokens=0,
        cost_krw=0.0,
        latency_ms=latency_ms,
        tool_calls_count=0,
        tool_input_tokens=0,
        tool_output_tokens=0,
    )


def is_cli_mode_active() -> bool:
    """Check if CLI persona mode is enabled and watcher is alive.

    REQ-ORCH-04-1/2: Reads cli_personas_enabled from system_state.
    REQ-SCHED-07-3: Checks watcher heartbeat before choosing CLI path.
    """
    try:
        from trading.db.session import get_system_state
        state = get_system_state()
        if not state.get("cli_personas_enabled", False):
            return False

        # REQ-SCHED-07-5: Check watcher heartbeat staleness
        from trading.personas.cli_bridge import is_watcher_alive
        if not is_watcher_alive():
            LOG.warning("CLI mode enabled but watcher heartbeat stale — using API fallback")
            try:
                from trading.alerts import telegram as tg
                tg.system_briefing(
                    "Watcher stale",
                    "cli_personas_enabled=true but watcher heartbeat stale. Using API.",
                )
            except Exception:  # noqa: BLE001
                pass
            return False

        return True
    except Exception:  # noqa: BLE001
        return False
