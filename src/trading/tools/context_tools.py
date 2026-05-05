"""Context tool functions — static .md files and dynamic memory.

REQ-TOOL-01-3: Standalone functions wrapping existing context.py logic.
REQ-TOOL-01-8: No Anthropic API or persona calls. Data-only.
"""

from __future__ import annotations

from typing import Any

from trading.personas.context import _format_memory, _load_memory, _read_md


def get_static_context(name: str) -> dict[str, Any]:
    """Load a static context .md file from data/contexts/.

    Args:
        name: Context file identifier. One of:
            macro_context, micro_context, macro_news, micro_news

    Returns:
        Dict with 'content' key containing the .md file text,
        or 'error' if file cannot be loaded.
    """
    # Map short name to actual filename
    filename_map = {
        "macro_context": "macro_context.md",
        "micro_context": "micro_context.md",
        "macro_news": "macro_news.md",
        "micro_news": "micro_news.md",
    }
    filename = filename_map.get(name)
    if not filename:
        return {"error": "invalid_name", "valid_names": list(filename_map.keys())}

    content = _read_md(filename)
    return {"name": name, "content": content}


def get_active_memory(
    table: str,
    limit: int = 20,
    scope_filter: list[str] | None = None,
) -> dict[str, Any]:
    """Query dynamic memory rows from macro_memory or micro_memory.

    Args:
        table: Memory table name ('macro_memory' or 'micro_memory').
        limit: Maximum rows to return.
        scope_filter: Optional list of scope_id values to filter by.

    Returns:
        Dict with 'rows' (list of memory entries) and 'formatted' (display string).
    """
    # Validate table name to prevent SQL injection
    allowed_tables = {"macro_memory", "micro_memory"}
    if table not in allowed_tables:
        return {"error": "invalid_table", "valid_tables": list(allowed_tables)}

    rows = _load_memory(table, limit=limit, scope_filter=scope_filter)
    formatted = _format_memory(rows)

    # Serialize date fields for JSON
    cleaned_rows = []
    for r in rows:
        cleaned = {}
        for k, v in r.items():
            if hasattr(v, "isoformat"):
                cleaned[k] = v.isoformat()
            else:
                cleaned[k] = v
        cleaned_rows.append(cleaned)

    return {"table": table, "count": len(cleaned_rows), "rows": cleaned_rows, "formatted": formatted}
