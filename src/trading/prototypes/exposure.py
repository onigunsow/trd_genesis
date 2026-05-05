"""Dynamic Risk Exposure — prototype-based exposure ceiling calculation.

REQ-DYNRISK-04-1: Compute dynamic exposure ceiling based on prototype similarity.
REQ-DYNRISK-04-3: Similarity-to-ceiling mapping table.
REQ-DYNRISK-04-4: Dynamic ceiling only TIGHTENS limits; never exceeds static 80%.
REQ-DYNRISK-04-5: Advisory only — does NOT auto-execute trades.
"""

from __future__ import annotations

import logging
from typing import Any

from trading.config import RISK_TOTAL_INVESTED_MAX

LOG = logging.getLogger(__name__)

# REQ-DYNRISK-04-3: Similarity threshold to exposure ceiling mapping
# Evaluated in order: first match wins for each prototype
CRASH_CEILING_RULES: list[tuple[float, float]] = [
    (0.85, 30.0),  # >= 0.85 crash similarity -> 30% ceiling
    (0.80, 50.0),  # >= 0.80 crash similarity -> 50% ceiling
    (0.75, 60.0),  # >= 0.75 crash/correction -> 60% ceiling
]

RALLY_CEILING: float = 90.0  # >= 0.85 rally -> 90% (advisory only)
RALLY_THRESHOLD: float = 0.85

# Static limit from SPEC-001 (80%)
STATIC_LIMIT_PCT: float = RISK_TOTAL_INVESTED_MAX * 100  # 80.0


def compute_ceiling(matches: list[dict[str, Any]]) -> float | None:
    """Compute the dynamic exposure ceiling from prototype similarity matches.

    REQ-DYNRISK-04-4: Rules:
    - Dynamic ceiling can only TIGHTEN below static 80% (never loosen)
    - Exception: rally >= 0.85 may recommend 90% (advisory only)
    - Multiple matches: use MOST restrictive ceiling among all >= 0.75

    Args:
        matches: List of prototype match dicts with 'category' and 'similarity'.

    Returns:
        Applied ceiling percentage, or None if no adjustment needed.
    """
    if not matches:
        return None

    ceilings: list[float] = []

    for match in matches:
        similarity = match.get("similarity", 0)
        category = match.get("category", "")

        if similarity < 0.75:
            continue  # Below threshold — no adjustment

        if category in ("crash", "correction"):
            ceiling = _crash_correction_ceiling(similarity)
            if ceiling is not None:
                ceilings.append(ceiling)

        elif category == "rally" and similarity >= RALLY_THRESHOLD:
            # Rally: advisory only, does not reduce ceiling
            # We still note it but do NOT add to restrictive ceilings
            pass

    if not ceilings:
        return None

    # REQ-DYNRISK-04-4: Use most restrictive ceiling
    applied = min(ceilings)

    # Never exceed static limit (80%)
    if applied > STATIC_LIMIT_PCT:
        applied = STATIC_LIMIT_PCT

    return applied


def _crash_correction_ceiling(similarity: float) -> float | None:
    """Map crash/correction similarity to exposure ceiling."""
    for threshold, ceiling in CRASH_CEILING_RULES:
        if similarity >= threshold:
            return ceiling
    return None


def get_risk_advisory(matches: list[dict[str, Any]]) -> dict[str, Any]:
    """Generate risk advisory context for the Risk persona.

    REQ-DYNRISK-04-8: Inject ProtoHedge context into Risk's input.

    Returns:
        Dict with advisory text, applied ceiling, and match details.
    """
    applied_ceiling = compute_ceiling(matches)

    # Build advisory text
    lines = ["[ProtoHedge Context]", "Current market similarity analysis:"]

    for i, match in enumerate(matches, 1):
        sim_pct = int(match["similarity"] * 100)
        name = match["name"]
        ceiling = match.get("ceiling_pct")
        if match["similarity"] >= 0.75:
            ceiling_text = f"Recommended ceiling: {ceiling}%" if ceiling else "Advisory"
            lines.append(f"{i}. {name}: {sim_pct}% similar - {ceiling_text}")
        else:
            lines.append(f"{i}. {name}: {sim_pct}% similar - Below threshold")

    if applied_ceiling is not None:
        lines.append(f"Applied dynamic ceiling: {applied_ceiling:.0f}% (vs static {STATIC_LIMIT_PCT:.0f}%)")
    else:
        lines.append(f"No dynamic adjustment. Static limit: {STATIC_LIMIT_PCT:.0f}%")

    # Add reasoning from top match
    if matches and matches[0].get("risk_recommendation"):
        rec = matches[0]["risk_recommendation"]
        if isinstance(rec, dict) and rec.get("reasoning"):
            lines.append(f"Reasoning: {rec['reasoning']}")

    advisory_text = "\n".join(lines)

    return {
        "text": advisory_text,
        "applied_ceiling_pct": applied_ceiling,
        "static_limit_pct": STATIC_LIMIT_PCT,
        "top_matches": [
            {
                "name": m["name"],
                "category": m["category"],
                "similarity": m["similarity"],
                "ceiling_pct": m.get("ceiling_pct"),
            }
            for m in matches
        ],
        "has_significant_match": any(m["similarity"] >= 0.75 for m in matches),
    }


def format_prototype_status(matches: list[dict[str, Any]]) -> str:
    """Format prototype status for Telegram /prototype-status command.

    REQ-DYNRISK-04-10: Reply format for Telegram command.
    """
    applied_ceiling = compute_ceiling(matches)

    lines = [
        "[ProtoHedge Status]",
        f"Last computed: {_now_kst()}",
        "",
        "Top-3 matches:",
    ]

    for i, match in enumerate(matches[:3], 1):
        sim_pct = int(match["similarity"] * 100)
        name = match["name"]
        category = match["category"]
        if match["similarity"] >= 0.75:
            ceiling = match.get("ceiling_pct", "?")
            lines.append(f"{i}. {name} ({category}): {sim_pct}% [ceiling: {ceiling}%]")
        else:
            lines.append(f"{i}. {name} ({category}): {sim_pct}% [below threshold]")

    lines.append("")
    if applied_ceiling is not None:
        lines.append(f"Applied ceiling: {applied_ceiling:.0f}% (static: {STATIC_LIMIT_PCT:.0f}%)")
    else:
        lines.append(f"No dynamic adjustment (static: {STATIC_LIMIT_PCT:.0f}%)")

    return "\n".join(lines)


def _now_kst() -> str:
    """Current time in KST for display."""
    from datetime import datetime
    import pytz
    kst = pytz.timezone("Asia/Seoul")
    return datetime.now(kst).strftime("%H:%M KST")
