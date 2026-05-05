"""Model Router — per-persona model resolution from system_state.

REQ-ROUTER-01-1: Determines LLM model for each persona invocation.
REQ-ROUTER-01-2: Default model routing configuration.
REQ-ROUTER-01-4: Resolution logic (haiku_eligible + haiku_enabled).
REQ-ROUTER-01-7: Decision/Risk NEVER route to Haiku.
"""

from __future__ import annotations

import json
import logging
import time
from typing import Any

from trading.db.session import connection

LOG = logging.getLogger(__name__)

# Default fallback model when haiku is eligible but disabled.
FALLBACK_MODEL: str = "claude-sonnet-4-6"

# Default model routing configuration (matches migration 011 default).
DEFAULT_MODEL_ROUTING: dict[str, dict[str, Any]] = {
    "macro": {"model": "claude-opus-4-7", "haiku_eligible": False},
    "micro": {"model": "claude-haiku-4-5", "haiku_eligible": True, "haiku_enabled": True},
    "decision": {"model": "claude-sonnet-4-6", "haiku_eligible": False},
    "risk": {"model": "claude-sonnet-4-6", "haiku_eligible": False},
    "portfolio": {"model": "claude-sonnet-4-6", "haiku_eligible": False},
    "retrospective": {"model": "claude-sonnet-4-6", "haiku_eligible": False},
    "daily_report": {"model": "claude-haiku-4-5", "haiku_eligible": True, "haiku_enabled": True},
    "macro_news": {"model": "claude-haiku-4-5", "haiku_eligible": True, "haiku_enabled": True},
}

# Personas that MUST NEVER use Haiku (REQ-ROUTER-01-7).
HAIKU_BLOCKED_PERSONAS: frozenset[str] = frozenset({"decision", "risk"})

# Cache for model routing config (TTL ~60s to avoid per-call DB reads).
_cache: dict[str, Any] = {"data": None, "expires_at": 0.0}
_CACHE_TTL_SECONDS: float = 60.0


class ModelRoutingError(Exception):
    """Raised when model routing configuration is invalid."""


def _load_model_routing() -> dict[str, dict[str, Any]]:
    """Load model_routing from system_state with TTL caching."""
    now = time.time()
    if _cache["data"] is not None and now < _cache["expires_at"]:
        return _cache["data"]

    try:
        with connection() as conn, conn.cursor() as cur:
            cur.execute("SELECT model_routing FROM system_state WHERE id = 1")
            row = cur.fetchone()
            if row and row.get("model_routing"):
                data = row["model_routing"]
                # Handle case where DB returns string instead of dict
                if isinstance(data, str):
                    data = json.loads(data)
                _cache["data"] = data
                _cache["expires_at"] = now + _CACHE_TTL_SECONDS
                return data
    except Exception as e:  # noqa: BLE001
        LOG.warning("Failed to load model_routing from DB, using defaults: %s", e)

    # Fallback to defaults if DB unavailable
    _cache["data"] = DEFAULT_MODEL_ROUTING
    _cache["expires_at"] = now + _CACHE_TTL_SECONDS
    return DEFAULT_MODEL_ROUTING


def resolve_model(persona_name: str) -> str:
    """Resolve the target model for a persona invocation.

    REQ-ROUTER-01-4 Logic:
    1. Read persona config from model_routing
    2. If haiku_eligible=True AND haiku_enabled=True -> configured model (Haiku)
    3. If haiku_eligible=True AND haiku_enabled=False -> fallback to Sonnet
    4. If haiku_eligible=False -> configured model (Sonnet or Opus)

    Args:
        persona_name: Persona identifier (macro, micro, decision, risk, etc.)

    Returns:
        Model identifier string (e.g., "claude-sonnet-4-6").
    """
    routing = _load_model_routing()
    config = routing.get(persona_name)

    if config is None:
        LOG.warning("No routing config for persona '%s', using Sonnet default", persona_name)
        return FALLBACK_MODEL

    haiku_eligible = config.get("haiku_eligible", False)
    haiku_enabled = config.get("haiku_enabled", False)
    configured_model = config.get("model", FALLBACK_MODEL)

    if haiku_eligible and haiku_enabled:
        return configured_model
    elif haiku_eligible and not haiku_enabled:
        return FALLBACK_MODEL
    else:
        # Not haiku eligible — use configured model (Sonnet or Opus)
        return configured_model


def update_model_routing(persona_name: str, **updates: Any) -> dict[str, Any]:
    """Update model routing for a specific persona.

    Args:
        persona_name: Persona to update.
        **updates: Fields to update (model, haiku_enabled).

    Returns:
        Updated config for the persona.

    Raises:
        ModelRoutingError: If attempting to enable Haiku for blocked personas.
    """
    # REQ-ROUTER-01-7: Block Haiku for Decision/Risk
    if persona_name in HAIKU_BLOCKED_PERSONAS and updates.get("haiku_enabled"):
        raise ModelRoutingError(
            "Decision/Risk personas require Sonnet or higher for quality assurance."
        )

    routing = _load_model_routing()
    config = routing.get(persona_name)
    if config is None:
        raise ModelRoutingError(f"Unknown persona: {persona_name}")

    # Verify haiku_eligible before allowing haiku_enabled toggle
    if "haiku_enabled" in updates and not config.get("haiku_eligible", False):
        raise ModelRoutingError(
            "Decision/Risk personas require Sonnet or higher for quality assurance."
        )

    # Apply updates
    new_config = {**config, **updates}
    routing[persona_name] = new_config

    # Persist to DB
    with connection() as conn, conn.cursor() as cur:
        cur.execute(
            "UPDATE system_state SET model_routing = %s::jsonb, updated_at = NOW() WHERE id = 1",
            (json.dumps(routing),),
        )

    # Invalidate cache
    _cache["data"] = None
    _cache["expires_at"] = 0.0

    return new_config


def invalidate_cache() -> None:
    """Force cache invalidation (useful for testing)."""
    _cache["data"] = None
    _cache["expires_at"] = 0.0
