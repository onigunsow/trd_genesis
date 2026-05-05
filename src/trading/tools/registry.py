"""Tool Registry — Anthropic API tools schema definitions.

REQ-TOOL-01-1: All tools defined in Anthropic `tools` parameter format.
REQ-TOOL-01-2: 10 tools covering macro, market, context, portfolio.
REQ-TOOL-01-6: Each definition includes cache_control for SPEC-008 compatibility.

Usage:
    from trading.tools.registry import get_all_tool_definitions, get_tools_for_persona
"""

from __future__ import annotations

from typing import Any

# Tool definitions in Anthropic API tools schema format.
# Each tool has: name, description (Korean, <50 chars), input_schema (JSON Schema Draft 7+)

TOOL_DEFINITIONS: list[dict[str, Any]] = [
    {
        "name": "get_macro_indicators",
        "description": "FRED/ECOS 거시 지표 조회",
        "input_schema": {
            "type": "object",
            "properties": {
                "series_ids": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "FRED or ECOS series identifiers to query",
                }
            },
            "required": ["series_ids"],
        },
        "cache_control": {"type": "ephemeral"},
    },
    {
        "name": "get_global_assets",
        "description": "글로벌 자산 시세 조회 (S&P500, VIX 등)",
        "input_schema": {
            "type": "object",
            "properties": {
                "symbols": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Yahoo Finance symbols (e.g. ^GSPC, ^VIX)",
                },
                "days": {
                    "type": "integer",
                    "description": "Number of recent days to fetch",
                    "default": 10,
                },
            },
            "required": ["symbols"],
        },
        "cache_control": {"type": "ephemeral"},
    },
    {
        "name": "get_ticker_technicals",
        "description": "종목 기술적 지표 조회 (MA/RSI/MACD)",
        "input_schema": {
            "type": "object",
            "properties": {
                "ticker": {
                    "type": "string",
                    "description": "KRX stock code (e.g. 005930)",
                },
                "lookback_days": {
                    "type": "integer",
                    "description": "Days of OHLCV data for calculation",
                    "default": 150,
                },
            },
            "required": ["ticker"],
        },
        "cache_control": {"type": "ephemeral"},
    },
    {
        "name": "get_ticker_fundamentals",
        "description": "종목 펀더멘털 조회 (PER/PBR/ROE/시총)",
        "input_schema": {
            "type": "object",
            "properties": {
                "ticker": {
                    "type": "string",
                    "description": "KRX stock code",
                },
            },
            "required": ["ticker"],
        },
        "cache_control": {"type": "ephemeral"},
    },
    {
        "name": "get_ticker_flows",
        "description": "종목 외국인/기관/개인 수급 조회",
        "input_schema": {
            "type": "object",
            "properties": {
                "ticker": {
                    "type": "string",
                    "description": "KRX stock code",
                },
                "days": {
                    "type": "integer",
                    "description": "Lookback days for flow accumulation",
                    "default": 5,
                },
            },
            "required": ["ticker"],
        },
        "cache_control": {"type": "ephemeral"},
    },
    {
        "name": "get_recent_disclosures",
        "description": "DART 공시 목록 조회",
        "input_schema": {
            "type": "object",
            "properties": {
                "tickers": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "KRX stock codes to query disclosures for",
                },
                "days": {
                    "type": "integer",
                    "description": "Recent days to search",
                    "default": 3,
                },
            },
            "required": ["tickers"],
        },
        "cache_control": {"type": "ephemeral"},
    },
    {
        "name": "get_static_context",
        "description": "Static .md 컨텍스트 파일 로드",
        "input_schema": {
            "type": "object",
            "properties": {
                "name": {
                    "type": "string",
                    "description": "Context file name (macro_context, micro_context, macro_news, micro_news)",
                    "enum": ["macro_context", "micro_context", "macro_news", "micro_news"],
                },
            },
            "required": ["name"],
        },
        "cache_control": {"type": "ephemeral"},
    },
    {
        "name": "get_active_memory",
        "description": "Dynamic Memory 테이블 조회",
        "input_schema": {
            "type": "object",
            "properties": {
                "table": {
                    "type": "string",
                    "description": "Memory table name (macro_memory or micro_memory)",
                    "enum": ["macro_memory", "micro_memory"],
                },
                "limit": {
                    "type": "integer",
                    "description": "Maximum rows to return",
                    "default": 20,
                },
                "scope_filter": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Optional scope_id filter (e.g. ticker codes)",
                },
            },
            "required": ["table"],
        },
        "cache_control": {"type": "ephemeral"},
    },
    {
        "name": "get_portfolio_status",
        "description": "현재 포지션 및 자산 현황 조회",
        "input_schema": {
            "type": "object",
            "properties": {},
            "required": [],
        },
        "cache_control": {"type": "ephemeral"},
    },
    {
        "name": "get_watchlist",
        "description": "현재 워치리스트 종목 조회",
        "input_schema": {
            "type": "object",
            "properties": {},
            "required": [],
        },
        "cache_control": {"type": "ephemeral"},
    },
]

# Per-persona tool assignments (REQ-PTOOL-02-3 through REQ-PTOOL-02-6)
PERSONA_TOOLS: dict[str, list[str]] = {
    "macro": [
        "get_macro_indicators",
        "get_global_assets",
        "get_static_context",
        "get_active_memory",
    ],
    "micro": [
        "get_ticker_technicals",
        "get_ticker_fundamentals",
        "get_ticker_flows",
        "get_recent_disclosures",
        "get_static_context",
        "get_active_memory",
        "get_watchlist",
    ],
    "decision": [
        "get_portfolio_status",
        "get_ticker_technicals",
        "get_ticker_fundamentals",
        "get_static_context",
        "get_active_memory",
    ],
    "risk": [
        "get_portfolio_status",
        "get_ticker_technicals",
        "get_ticker_flows",
    ],
}


def get_all_tool_definitions() -> list[dict[str, Any]]:
    """Return all 10 tool definitions in Anthropic API format."""
    return TOOL_DEFINITIONS.copy()


def get_tools_for_persona(persona_name: str) -> list[dict[str, Any]]:
    """Return tool definitions assigned to a specific persona.

    Args:
        persona_name: One of 'macro', 'micro', 'decision', 'risk'.

    Returns:
        List of tool definition dicts for the persona's allowed tools.
        Empty list if persona has no tool assignments.
    """
    allowed_names = PERSONA_TOOLS.get(persona_name, [])
    return [t for t in TOOL_DEFINITIONS if t["name"] in allowed_names]
