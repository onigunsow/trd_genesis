"""Persona base — Anthropic API client + audit + token cost in KRW.

Pricing as of 2026-05 (USD/M tokens):
- Sonnet 4.6: $3 in / $15 out
- Opus 4.7  : $15 in / $75 out
KRW conversion uses approx 1380 KRW/USD (override via ANTHROPIC_KRW_PER_USD env).
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
from trading.db.session import connection

LOG = logging.getLogger(__name__)

KRW_PER_USD = float(os.environ.get("ANTHROPIC_KRW_PER_USD", "1380"))

PRICING_USD_PER_MTOK = {
    "claude-sonnet-4-6": (3.0, 15.0),
    "claude-opus-4-7":   (15.0, 75.0),
    # Fallbacks for dated model IDs.
    "claude-sonnet-4.6": (3.0, 15.0),
    "claude-opus-4.7":   (15.0, 75.0),
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
) -> PersonaResult:
    """Single persona invocation. Persists to persona_runs (REQ-PERSONA-04-2)."""
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
    try:
        # SPEC-008 REQ-CACHE-01-1/2 — Mark long, stable system prompt as cacheable.
        # Anthropic API expects either a string (no cache) or a list of content blocks
        # (with optional cache_control on the last block to cache up to that point).
        system_blocks = [
            {"type": "text", "text": system_prompt, "cache_control": {"type": "ephemeral"}}
        ]
        msg = client.messages.create(
            model=model,
            max_tokens=max_tokens,
            system=system_blocks,
            messages=[{"role": "user", "content": user_message}],
        )
        # Concatenate all text blocks
        for blk in msg.content:
            if getattr(blk, "type", "") == "text":
                text += blk.text
        usage = msg.usage
        if usage:
            in_tok = usage.input_tokens
            out_tok = usage.output_tokens
            # Optional fields (may be 0/None depending on SDK version)
            cache_read = getattr(usage, "cache_read_input_tokens", 0) or 0
            cache_create = getattr(usage, "cache_creation_input_tokens", 0) or 0
    except Exception as e:  # noqa: BLE001
        error = f"{type(e).__name__}: {e}"
        LOG.exception("persona call failed: %s", persona_name)
    latency_ms = int((time.time() - start) * 1000)
    cost = _cost_krw(model, in_tok, out_tok, cache_read=cache_read, cache_create=cache_create)

    response_json = None
    if expect_json and text:
        # Try to extract first JSON object from response.
        try:
            response_json = _extract_json(text)
        except Exception as e:  # noqa: BLE001
            LOG.warning("could not parse persona JSON: %s", e)

    sql = """
        INSERT INTO persona_runs
            (persona_name, model, cycle_kind, trigger_context,
             prompt, response, response_json,
             input_tokens, output_tokens, cost_krw, latency_ms, error,
             cache_read_tokens, cache_creation_tokens)
        VALUES (%s,%s,%s,%s::jsonb,%s,%s,%s::jsonb,%s,%s,%s,%s,%s,%s,%s)
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
