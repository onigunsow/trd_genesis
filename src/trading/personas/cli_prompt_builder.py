"""CLI Prompt Builder — pre-compute tools + render single-turn prompt for CLI.

SPEC-015 REQ-BUILDER-01-*: Builds complete prompts with all tool data embedded,
eliminating the need for multi-turn tool-calling in CLI mode.

REQ-PRECOMP-05-*: Tool pre-computation respects feature flags and per-persona
tool assignments from the registry.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from trading.tools.executor import execute_tool
from trading.tools.registry import PERSONA_TOOLS

LOG = logging.getLogger(__name__)

# Tools that require a 'ticker' parameter for per-ticker execution
TICKER_PARAM_TOOLS: set[str] = {
    "get_ticker_technicals",
    "get_ticker_fundamentals",
    "get_ticker_flows",
    "get_delta_events",
    "get_intraday_price_history",
    "get_dynamic_thresholds",
}

# Tools that require a 'tickers' parameter (list of tickers)
TICKERS_PARAM_TOOLS: set[str] = {
    "get_recent_disclosures",
}

# Default params for non-ticker tools that need specific arguments
DEFAULT_TOOL_PARAMS: dict[str, dict[str, Any]] = {
    "get_macro_indicators": {"series_ids": [
        "DFF", "T10Y2Y", "UNRATE", "CPIAUCSL",
    ]},
    "get_global_assets": {"symbols": [
        "^GSPC", "^VIX", "^KS11", "^KQ11", "CL=F", "GC=F",
    ]},
    "get_static_context": {"name": "intelligence_macro", "mode": "full"},
    "get_active_memory": {"table": "macro_memory", "limit": 20},
}

# Per-persona static_context name overrides
_PERSONA_CONTEXT_NAME: dict[str, str] = {
    "macro": "intelligence_macro",
    "micro": "intelligence_micro",
    "decision": "intelligence_micro",
    "risk": "intelligence_micro",
}

# Per-persona active_memory table overrides
_PERSONA_MEMORY_TABLE: dict[str, str] = {
    "macro": "macro_memory",
    "micro": "micro_memory",
    "decision": "micro_memory",
    "risk": "micro_memory",
}


def _get_active_tool_names(persona_name: str) -> list[str]:
    """Get tool names for a persona, respecting feature flags.

    REQ-BUILDER-01-4: Uses the same PERSONA_TOOLS registry as the API path.
    REQ-PRECOMP-05-2: Respects feature flags via get_tools_for_persona logic.
    """
    from trading.tools.registry import get_tools_for_persona

    tool_defs = get_tools_for_persona(persona_name)
    return [t["name"] for t in tool_defs]


def _pre_compute_tools(
    persona_name: str,
    tool_names: list[str],
    tickers: list[str] | None = None,
) -> dict[str, Any]:
    """Execute all tools and collect results.

    REQ-PRECOMP-05-1: Executes each tool from the dispatch table.
    REQ-PRECOMP-05-3: For ticker-specific tools, iterates over all tickers.
    REQ-PRECOMP-05-4: Uses existing execute_tool with its built-in timeout.
    REQ-BUILDER-01-3: Failed tools are included with '(unavailable)' marker.

    Returns:
        Dict mapping "tool_name" or "tool_name: ticker" to result dict.
    """
    results: dict[str, Any] = {}
    tickers = tickers or []

    for tool_name in tool_names:
        if tool_name in TICKER_PARAM_TOOLS:
            # REQ-PRECOMP-05-3: Execute for each ticker in context
            for ticker in tickers:
                key = f"{tool_name}: {ticker}"
                try:
                    result = execute_tool(tool_name, {"ticker": ticker}, persona_run_id=None)
                    results[key] = result
                except Exception as e:  # noqa: BLE001
                    LOG.warning("Tool %s failed for ticker %s: %s", tool_name, ticker, e)
                    results[key] = {"status": "(unavailable)", "error": str(e)[:200]}

        elif tool_name in TICKERS_PARAM_TOOLS:
            # get_recent_disclosures takes a list of tickers
            key = f"{tool_name}: {','.join(tickers[:20])}" if tickers else tool_name
            try:
                params = {"tickers": tickers[:20]} if tickers else {"tickers": []}
                result = execute_tool(tool_name, params, persona_run_id=None)
                results[key] = result
            except Exception as e:  # noqa: BLE001
                LOG.warning("Tool %s failed: %s", tool_name, e)
                results[key] = {"status": "(unavailable)", "error": str(e)[:200]}

        else:
            # Non-ticker tools: use persona-aware defaults
            params = _resolve_tool_params(persona_name, tool_name)
            try:
                result = execute_tool(tool_name, params, persona_run_id=None)
                results[tool_name] = result
            except Exception as e:  # noqa: BLE001
                LOG.warning("Tool %s failed: %s", tool_name, e)
                results[tool_name] = {"status": "(unavailable)", "error": str(e)[:200]}

    return results


def _resolve_tool_params(persona_name: str, tool_name: str) -> dict[str, Any]:
    """Resolve default parameters for a non-ticker tool, with persona awareness."""
    params = dict(DEFAULT_TOOL_PARAMS.get(tool_name, {}))

    # Per-persona overrides
    if tool_name == "get_static_context":
        params["name"] = _PERSONA_CONTEXT_NAME.get(persona_name, "intelligence_micro")
    elif tool_name == "get_active_memory":
        params["table"] = _PERSONA_MEMORY_TABLE.get(persona_name, "micro_memory")

    return params


def _format_tool_section(tool_results: dict[str, Any]) -> str:
    """Format pre-computed tool data into a prompt section.

    Follows S-3 format from SPEC-015.
    """
    if not tool_results:
        return ""

    lines = ["\n=== PRE-COMPUTED TOOL DATA ===\n"]
    for key, result in tool_results.items():
        # REQ-BUILDER-01-3: Mark failed tools
        if isinstance(result, dict) and result.get("status") == "(unavailable)":
            lines.append(f"[{key}] (unavailable)\n")
        else:
            result_str = json.dumps(result, ensure_ascii=False, default=str)
            lines.append(f"[{key}]\n{result_str}\n")
    lines.append("=== END TOOL DATA ===\n")
    return "\n".join(lines)


def build_cli_prompt(
    persona_name: str,
    input_data: dict[str, Any],
    system_prompt: str,
    user_message: str,
    tickers: list[str] | None = None,
) -> str:
    """Build a complete single-turn prompt with pre-computed tool data.

    REQ-BUILDER-01-1: Pre-executes ALL assigned tools and embeds results.
    REQ-BUILDER-01-2: Renders existing Jinja2 system prompt + appends tool data.
    REQ-BUILDER-01-7: Includes explicit JSON output instructions.

    Args:
        persona_name: One of 'macro', 'micro', 'decision', 'risk'.
        input_data: Context data (used for extracting tickers, etc.).
        system_prompt: Rendered Jinja2 system prompt.
        user_message: The user message portion of the prompt.
        tickers: List of ticker codes for ticker-specific tool execution.
            For Micro: expanded watchlist (REQ-PRECOMP-05-6).
            For Decision: candidate tickers from Micro result (REQ-PRECOMP-05-7).
            For Risk: signal ticker(s).

    Returns:
        Complete prompt string ready for `claude -p`.
    """
    # Get active tool names respecting feature flags
    tool_names = _get_active_tool_names(persona_name)

    # Pre-compute all tools
    tool_results = _pre_compute_tools(persona_name, tool_names, tickers=tickers)

    # Format the tool data section
    tool_section = _format_tool_section(tool_results)

    # REQ-BUILDER-01-5/06/08: Context injection is handled by the caller
    # (orchestrator passes Micro result into Decision input, rejection_feedback, etc.)
    # The system_prompt and user_message already contain this context from Jinja rendering.

    # Build the complete prompt
    prompt_parts = [
        system_prompt,
        tool_section,
        user_message,
        (
            "\n\nIMPORTANT: Respond with valid JSON only. "
            "No markdown fences, no explanation outside JSON. "
            "The JSON must match the schema described in the system prompt."
        ),
    ]

    return "\n".join(prompt_parts)
