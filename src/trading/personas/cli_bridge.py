"""CLI Bridge — file-based IPC between Docker container and host Claude CLI.

SPEC-015 REQ-BRIDGE-02-*: Exports prompt files, polls for results, parses responses.

The bridge writes call files to data/persona_calls/ and polls data/persona_results/
for the host watcher to process via `claude -p --max-turns 1`.
"""

from __future__ import annotations

import json
import logging
import time
from pathlib import Path
from typing import Any

from trading.config import project_root
from trading.personas.base import PersonaResult, _extract_json

LOG = logging.getLogger(__name__)

# REQ-BRIDGE-02-1: IPC directories on shared volume
CALLS_DIR = Path(project_root()) / "data" / "persona_calls"
RESULTS_DIR = Path(project_root()) / "data" / "persona_results"

# REQ-BRIDGE-02-2: Default timeout for polling (seconds)
DEFAULT_TIMEOUT: int = 180

# Poll interval in seconds (REQ-SCHED-07-5 / A-6)
POLL_INTERVAL: float = 2.0

# REQ-SCHED-07-4: Heartbeat file for watcher liveness detection
HEARTBEAT_FILE = Path(project_root()) / "data" / "persona_watcher.heartbeat"

# REQ-SCHED-07-5: Heartbeat staleness threshold (seconds)
HEARTBEAT_STALE_SECONDS: float = 60.0

# SPEC-TRADING-016 REQ-016-1-4: Whitelist of models permitted on the fallback
# path. The fallback log message and the actual API call must reference the
# same model — see assert_fallback_model() below.
#
# @MX:ANCHOR: Fallback model contract — only Haiku variants permitted here.
# @MX:REASON: Past incident saw "falling back to Haiku" logged while the API
# was actually called with Sonnet. Centralising the whitelist prevents that
# divergence from re-occurring.
ALLOWED_FALLBACK_MODELS: frozenset[str] = frozenset({
    "claude-haiku-4-5",
    "claude-haiku-4-5-20251001",
})


def assert_fallback_model(model: str) -> None:
    """Raise ValueError if `model` is not a permitted fallback model.

    SPEC-TRADING-016 REQ-016-1-4: Guards the Haiku fallback path against being
    invoked with a non-Haiku model. Callers (e.g. ``call_persona_via_cli`` in
    ``personas/base.py``) MUST call this immediately before invoking the
    Anthropic API in the fallback branch so that any drift between the log
    message and the actual call is caught at runtime.

    Args:
        model: The model identifier the caller intends to pass to the API.

    Raises:
        ValueError: If ``model`` is not in :data:`ALLOWED_FALLBACK_MODELS`.
    """
    if model not in ALLOWED_FALLBACK_MODELS:
        raise ValueError(
            f"fallback_to_haiku invoked with non-Haiku model: {model!r}. "
            f"Allowed: {sorted(ALLOWED_FALLBACK_MODELS)}"
        )


class CLITimeoutError(Exception):
    """Raised when CLI result is not received within timeout (REQ-BRIDGE-02-5)."""


class CLICallError(Exception):
    """Raised when CLI call fails (non-zero exit, parse error)."""


def is_watcher_alive() -> bool:
    """Check watcher heartbeat for liveness (REQ-SCHED-07-3/5).

    Returns True if heartbeat file exists and was updated within HEARTBEAT_STALE_SECONDS.
    """
    if not HEARTBEAT_FILE.exists():
        return False
    try:
        age = time.time() - HEARTBEAT_FILE.stat().st_mtime
        return age < HEARTBEAT_STALE_SECONDS
    except OSError:
        return False


def call_persona_cli(
    persona_name: str,
    prompt: str,
    cycle_kind: str = "pre_market",
    model_for_audit: str = "cli-claude-max",
    timeout: int = DEFAULT_TIMEOUT,
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    """Export prompt to call file, wait for host CLI result, return parsed response.

    REQ-BRIDGE-02-1: Writes call file with prompt and metadata.
    REQ-BRIDGE-02-2: Polls for result with configurable timeout.
    REQ-BRIDGE-02-3: Parses response and extracts JSON.
    REQ-BRIDGE-02-5: Raises CLITimeoutError on timeout.
    REQ-BRIDGE-02-7: Cleans up call and result files after import.

    Args:
        persona_name: The persona being invoked.
        prompt: Complete single-turn prompt (from cli_prompt_builder).
        cycle_kind: Cycle type for audit trail.
        model_for_audit: Model name recorded in persona_runs.
        timeout: Maximum seconds to wait for result.
        metadata: Additional metadata for the call file.

    Returns:
        Parsed response dict (the JSON from CLI output), or None on failure.

    Raises:
        CLITimeoutError: When poll timeout expires.
        CLICallError: When CLI returns an error result.
    """
    # REQ-BRIDGE-02-1: Create call file
    CALLS_DIR.mkdir(parents=True, exist_ok=True)
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)

    call_id = f"{persona_name}_{cycle_kind}_{int(time.time() * 1000)}"
    call_file = CALLS_DIR / f"{call_id}.json"
    result_file = RESULTS_DIR / f"{call_id}.json"

    # S-1: Call file JSON schema
    call_data = {
        "persona": persona_name,
        "cycle_kind": cycle_kind,
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "prompt": prompt,
        "expect_json": True,
        "timeout_seconds": timeout,
        "metadata": {
            "model_for_audit": model_for_audit,
            **(metadata or {}),
        },
    }

    call_file.write_text(json.dumps(call_data, ensure_ascii=False, default=str))
    LOG.info("CLI call exported: %s (%d bytes)", call_file.name, call_file.stat().st_size)

    # REQ-BRIDGE-02-2: Poll for result
    start = time.time()
    deadline = start + timeout

    while time.time() < deadline:
        if result_file.exists():
            try:
                raw = result_file.read_text()
                result_data = json.loads(raw)

                # REQ-BRIDGE-02-7: Clean up files
                result_file.unlink(missing_ok=True)
                call_file.unlink(missing_ok=True)

                execution_seconds = result_data.get("execution_seconds", 0)
                exit_code = result_data.get("exit_code", -1)
                error = result_data.get("error")

                if error:
                    raise CLICallError(
                        f"CLI call failed for {persona_name}: {error} (exit={exit_code})"
                    )

                response_text = result_data.get("response_text", "")
                LOG.info(
                    "CLI result imported: %s (%.1fs, %d bytes)",
                    persona_name, execution_seconds, len(response_text),
                )

                return {
                    "response_text": response_text,
                    "execution_seconds": execution_seconds,
                }

            except (json.JSONDecodeError, CLICallError) as e:
                # Clean up on parse/call error
                result_file.unlink(missing_ok=True)
                call_file.unlink(missing_ok=True)
                raise CLICallError(f"Failed to parse CLI result for {persona_name}: {e}") from e

        time.sleep(POLL_INTERVAL)

    # REQ-BRIDGE-02-5: Timeout
    call_file.unlink(missing_ok=True)
    raise CLITimeoutError(
        f"CLI call timed out for {persona_name} after {timeout}s"
    )


def parse_cli_response(response_text: str) -> dict[str, Any] | None:
    """Parse CLI response text to extract JSON.

    REQ-BRIDGE-02-3: Reuses existing _extract_json logic for compatibility.

    Returns:
        Parsed JSON dict, or None if parsing fails.
    """
    if not response_text or not response_text.strip():
        return None
    try:
        return _extract_json(response_text)
    except (ValueError, json.JSONDecodeError) as e:
        LOG.warning("CLI response JSON extraction failed: %s", e)
        return None
