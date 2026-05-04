"""SPEC-007 — Memory ops handler.

REQ-MEM-03-1~4: 페르소나 응답의 memory_ops 배열을 단일 트랜잭션에서 실행.

Op kinds:
- create   : INSERT new memory
- update   : 기존 id의 summary/importance/valid_until 수정
- archive  : status='archived'
- supersede: 기존 id status='superseded' + 새 row 생성 (supersedes_id 링크)

Ownership (REQ-MEM-03-3):
- macro persona → only macro_memory
- micro persona → only micro_memory
- decision/risk/portfolio/retrospective → write 거부 (read-only via REQ-MEM-04-2a/2b)

Source refs (REQ-MEM-02-3):
- create 시 persona_run_id 자동 첨부
"""

from __future__ import annotations

import json
import logging
from typing import Any

from trading.db.session import audit, connection

LOG = logging.getLogger(__name__)

WRITE_ALLOWED = {"macro", "micro"}
TABLE_BY_PERSONA = {
    "macro": "macro_memory",
    "micro": "micro_memory",
}


def _audit(event_subtype: str, persona: str, details: dict[str, Any]) -> None:
    audit(f"MEMORY_OP_{event_subtype}", actor=f"persona.{persona}", details=details)


def _normalize_source_refs(refs: Any, persona_run_id: int) -> dict[str, Any]:
    """Always inject persona_run_id. Accept dict or None from persona response."""
    if not isinstance(refs, dict):
        refs = {}
    refs["persona_run_id"] = persona_run_id
    return refs


def _execute_create(cur, table: str, op: dict[str, Any], persona_run_id: int, persona: str) -> int | None:
    summary = (op.get("summary") or "").strip()
    if not summary:
        _audit("CREATE_FAIL", persona, {"reason": "empty_summary", "op": op})
        return None
    source_refs = _normalize_source_refs(op.get("source_refs"), persona_run_id)
    importance = int(op.get("importance", 3) or 3)
    if importance < 1 or importance > 5:
        importance = 3

    if table == "macro_memory":
        cols = "(scope, scope_id, kind, summary, importance, source_refs, valid_until)"
        vals = "(%s, %s, %s, %s, %s, %s::jsonb, %s)"
        params = (
            op.get("scope", "global"),
            op.get("scope_id"),
            op.get("kind", "event"),
            summary,
            importance,
            json.dumps(source_refs),
            op.get("valid_until"),
        )
    else:
        cols = "(scope, scope_id, kind, summary, importance, source_refs, valid_until)"
        vals = "(%s, %s, %s, %s, %s, %s::jsonb, %s)"
        params = (
            op.get("scope", "ticker"),
            op.get("scope_id", ""),
            op.get("kind", "thematic"),
            summary,
            importance,
            json.dumps(source_refs),
            op.get("valid_until"),
        )

    cur.execute(f"INSERT INTO {table} {cols} VALUES {vals} RETURNING id", params)
    new_id = cur.fetchone()["id"]
    _audit("CREATE_OK", persona, {"table": table, "id": new_id, "summary": summary[:80]})
    return new_id


def _execute_update(cur, table: str, op: dict[str, Any], persona: str) -> bool:
    target = op.get("id")
    if not target:
        _audit("UPDATE_FAIL", persona, {"reason": "missing_id", "op": op})
        return False
    fields = []
    params: list[Any] = []
    if "summary" in op and op["summary"]:
        fields.append("summary = %s")
        params.append(op["summary"])
    if "importance" in op and op["importance"] is not None:
        imp = max(1, min(5, int(op["importance"])))
        fields.append("importance = %s")
        params.append(imp)
    if "valid_until" in op:
        fields.append("valid_until = %s")
        params.append(op["valid_until"])
    if not fields:
        _audit("UPDATE_FAIL", persona, {"reason": "no_changes", "id": target})
        return False
    fields.append("updated_at = NOW()")
    params.extend([target])
    cur.execute(
        f"UPDATE {table} SET {', '.join(fields)} WHERE id = %s AND status = 'active' RETURNING id",
        params,
    )
    row = cur.fetchone()
    if row:
        _audit("UPDATE_OK", persona, {"table": table, "id": target})
        return True
    _audit("UPDATE_FAIL", persona, {"reason": "not_found_or_inactive", "id": target})
    return False


def _execute_archive(cur, table: str, op: dict[str, Any], persona: str) -> bool:
    target = op.get("id")
    if not target:
        _audit("ARCHIVE_FAIL", persona, {"reason": "missing_id", "op": op})
        return False
    reason = op.get("reason", "")[:300]
    cur.execute(
        f"UPDATE {table} SET status='archived', updated_at=NOW() "
        "WHERE id = %s AND status = 'active' RETURNING id",
        (target,),
    )
    row = cur.fetchone()
    if row:
        _audit("ARCHIVE_OK", persona, {"table": table, "id": target, "reason": reason})
        return True
    _audit("ARCHIVE_FAIL", persona, {"reason": "not_found_or_inactive", "id": target})
    return False


def _execute_supersede(cur, table: str, op: dict[str, Any], persona_run_id: int, persona: str) -> int | None:
    old_id = op.get("old_id")
    if not old_id:
        _audit("SUPERSEDE_FAIL", persona, {"reason": "missing_old_id", "op": op})
        return None

    new_op = dict(op)
    new_op.pop("op", None)
    new_op.pop("old_id", None)
    new_id = _execute_create(cur, table, new_op, persona_run_id, persona)
    if not new_id:
        return None

    cur.execute(
        f"UPDATE {table} SET status='superseded', updated_at=NOW() WHERE id = %s",
        (old_id,),
    )
    cur.execute(
        f"UPDATE {table} SET supersedes_id = %s WHERE id = %s",
        (old_id, new_id),
    )
    _audit("SUPERSEDE_OK", persona, {"table": table, "old_id": old_id, "new_id": new_id})
    return new_id


def execute_memory_ops(
    *,
    persona: str,
    persona_run_id: int,
    response_json: dict[str, Any] | None,
) -> dict[str, int]:
    """Execute all memory_ops in a single transaction. Returns counts per op type."""
    counts = {"create": 0, "update": 0, "archive": 0, "supersede": 0, "rejected": 0}
    if not response_json:
        return counts

    ops = response_json.get("memory_ops") or []
    if not isinstance(ops, list) or not ops:
        return counts

    # Ownership check (REQ-MEM-03-3)
    if persona not in WRITE_ALLOWED:
        # Decision/Risk/Portfolio/Retrospective shouldn't write
        _audit("DECISION_WRITE_REJECTED", persona, {
            "reason": "persona_not_authorized",
            "ops_count": len(ops),
        })
        counts["rejected"] = len(ops)
        return counts

    table = TABLE_BY_PERSONA[persona]

    try:
        with connection() as conn, conn.cursor() as cur:
            for op in ops:
                kind = (op.get("op") or "").lower()
                if kind == "create":
                    if _execute_create(cur, table, op, persona_run_id, persona):
                        counts["create"] += 1
                    else:
                        counts["rejected"] += 1
                elif kind == "update":
                    if _execute_update(cur, table, op, persona):
                        counts["update"] += 1
                    else:
                        counts["rejected"] += 1
                elif kind == "archive":
                    if _execute_archive(cur, table, op, persona):
                        counts["archive"] += 1
                    else:
                        counts["rejected"] += 1
                elif kind == "supersede":
                    if _execute_supersede(cur, table, op, persona_run_id, persona):
                        counts["supersede"] += 1
                    else:
                        counts["rejected"] += 1
                else:
                    _audit("UNKNOWN_OP", persona, {"op": op})
                    counts["rejected"] += 1
    except Exception as e:  # noqa: BLE001
        LOG.exception("memory ops transaction failed (persona=%s)", persona)
        # Re-raise so caller can decide; transaction already rolled back at context exit.
        raise

    return counts
