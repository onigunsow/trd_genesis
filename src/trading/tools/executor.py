"""Tool Executor — dispatch, timeout enforcement, error handling, logging.

REQ-TOOL-01-4: 5-second timeout per tool invocation.
REQ-TOOL-01-5: Exception handling returns structured error to LLM.
REQ-TOOL-01-7: Every tool call logged to tool_call_log table.
"""

from __future__ import annotations

import hashlib
import json
import logging
import time
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FuturesTimeout
from typing import Any

from trading.db.session import connection

LOG = logging.getLogger(__name__)

# Tool execution timeout in seconds (REQ-TOOL-01-4)
TOOL_TIMEOUT_SECONDS: int = 5
TOOL_TIMEOUT_OVERRIDES: dict[str, int] = {
    "get_portfolio_status": 15,
    "get_watchlist": 15,
}

# Import tool functions lazily to avoid circular imports
_TOOL_DISPATCH: dict[str, Any] | None = None


def _get_dispatch_table() -> dict[str, Any]:
    """Lazy-load tool function dispatch table."""
    global _TOOL_DISPATCH
    if _TOOL_DISPATCH is None:
        from trading.tools.context_tools import get_active_memory, get_static_context
        from trading.tools.market_tools import (
            get_macro_indicators,
            get_global_assets,
            get_recent_disclosures,
            get_ticker_flows,
            get_ticker_fundamentals,
            get_ticker_technicals,
        )
        from trading.tools.portfolio_tools import get_portfolio_status, get_watchlist
        # SPEC-011 REQ-TOOLINT-05-3: New JIT/prototype tools
        from trading.tools.jit_tools import (
            get_delta_events,
            get_intraday_price_history,
            get_market_prototype_similarity,
        )
        # SPEC-012 REQ-DYNTH-05-1: Dynamic threshold tool
        from trading.strategy.volatility.thresholds import get_dynamic_thresholds

        _TOOL_DISPATCH = {
            "get_macro_indicators": get_macro_indicators,
            "get_global_assets": get_global_assets,
            "get_ticker_technicals": get_ticker_technicals,
            "get_ticker_fundamentals": get_ticker_fundamentals,
            "get_ticker_flows": get_ticker_flows,
            "get_recent_disclosures": get_recent_disclosures,
            "get_static_context": get_static_context,
            "get_active_memory": get_active_memory,
            "get_portfolio_status": get_portfolio_status,
            "get_watchlist": get_watchlist,
            # SPEC-011 tools
            "get_delta_events": get_delta_events,
            "get_market_prototype_similarity": get_market_prototype_similarity,
            "get_intraday_price_history": get_intraday_price_history,
            # SPEC-012 tools
            "get_dynamic_thresholds": get_dynamic_thresholds,
        }
    return _TOOL_DISPATCH


def _hash_input(params: dict[str, Any]) -> str:
    """SHA-256 hash of tool input parameters for audit log (privacy)."""
    raw = json.dumps(params, sort_keys=True, default=str)
    return hashlib.sha256(raw.encode()).hexdigest()[:16]


def _log_tool_call(
    persona_run_id: int | None,
    tool_name: str,
    input_hash: str,
    execution_ms: int,
    success: bool,
    result_bytes: int | None = None,
    error: str | None = None,
) -> None:
    """Persist tool call to tool_call_log table (REQ-TOOL-01-7)."""
    try:
        sql = """
            INSERT INTO tool_call_log
                (persona_run_id, tool_name, input_hash, execution_ms, success, result_bytes, error)
            VALUES (%s, %s, %s, %s, %s, %s, %s)
        """
        with connection() as conn, conn.cursor() as cur:
            cur.execute(sql, (
                persona_run_id,
                tool_name,
                input_hash,
                execution_ms,
                success,
                result_bytes,
                error,
            ))
    except Exception as e:  # noqa: BLE001
        LOG.warning("Failed to log tool call: %s", e)


def execute_tool(
    name: str,
    params: dict[str, Any],
    persona_run_id: int | None = None,
) -> dict[str, Any]:
    """Execute a tool by name with timeout and error handling.

    Args:
        name: Tool function name (must match registry).
        params: Input parameters matching the tool's input_schema.
        persona_run_id: Optional persona_runs.id for audit trail.

    Returns:
        Tool result dict on success, or structured error dict on failure.
        Error format: {"error": "<type>", "tool": "<name>", "message": "<desc>"}
    """
    dispatch = _get_dispatch_table()
    if name not in dispatch:
        error_result = {"error": "unknown_tool", "tool": name, "message": f"Tool '{name}' not registered"}
        _log_tool_call(persona_run_id, name, _hash_input(params), 0, False, error=error_result["message"])
        return error_result

    fn = dispatch[name]
    input_hash = _hash_input(params)
    start = time.time()

    try:
        # Execute with timeout (REQ-TOOL-01-4)
        with ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(fn, **params)
            timeout = TOOL_TIMEOUT_OVERRIDES.get(name, TOOL_TIMEOUT_SECONDS)
            result = future.result(timeout=timeout)

        execution_ms = int((time.time() - start) * 1000)
        result_bytes = len(json.dumps(result, default=str).encode()) if result else 0

        _log_tool_call(
            persona_run_id, name, input_hash, execution_ms,
            success=True, result_bytes=result_bytes,
        )
        return result

    except FuturesTimeout:
        # REQ-TOOL-01-4: Timeout returns structured error
        execution_ms = int((time.time() - start) * 1000)
        error_result = {"error": "timeout", "tool": name}
        _log_tool_call(
            persona_run_id, name, input_hash, execution_ms,
            success=False, error="timeout",
        )
        LOG.warning("Tool %s timed out after %dms", name, execution_ms)
        return error_result

    except Exception as e:  # noqa: BLE001
        # REQ-TOOL-01-5: Exception returns structured error to LLM
        execution_ms = int((time.time() - start) * 1000)
        error_type = type(e).__name__
        error_msg = str(e)[:200]
        error_result = {"error": error_type, "tool": name, "message": error_msg}
        _log_tool_call(
            persona_run_id, name, input_hash, execution_ms,
            success=False, error=f"{error_type}: {error_msg}",
        )
        LOG.warning("Tool %s failed: %s: %s", name, error_type, error_msg)
        return error_result
