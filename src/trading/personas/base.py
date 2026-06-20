"""Persona base — Anthropic API client + audit + token cost in KRW.

Pricing as of 2026-05 (USD/M tokens):
- Sonnet 4.6: $3 in / $15 out
- Opus 4.7  : $15 in / $75 out
KRW conversion uses approx 1380 KRW/USD (override via ANTHROPIC_KRW_PER_USD env).

SPEC-009 REQ-PTOOL-02-1: Tool-use multi-turn loop support.
SPEC-015 REQ-ORCH-04-1/2: CLI routing via cli_personas_enabled feature flag.
SPEC-TRADING-016 REQ-016-1-3: block_if_cli_only_mode decorator + fallback
model guard to prevent direct Sonnet API calls when cli_only_mode is active.
"""

from __future__ import annotations

import functools
import json
import logging
import os
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from anthropic import Anthropic
from jinja2 import Environment, FileSystemLoader, StrictUndefined

from trading.config import get_settings, project_root
from trading.db.session import audit, connection, get_system_state, update_system_state

LOG = logging.getLogger(__name__)

# SPEC-TRADING-016 REQ-016-1-4: Single source of truth for the Haiku fallback
# model. The log message and the API call must both use this constant so
# they cannot drift apart.
_HAIKU_FALLBACK_MODEL = "claude-haiku-4-5"

# SPEC-TRADING-016 REQ-016-1-3/4: Module-level placeholders for symbols that
# live in ``trading.personas.cli_bridge``. cli_bridge imports from this module
# (PersonaResult, _extract_json) so we cannot import its symbols at the top
# level — that would create a circular import. Instead we re-bind these names
# lazily on first use of ``call_persona_via_cli`` so tests can ``patch.object``
# them on ``trading.personas.base`` directly.
call_persona_cli = None  # type: ignore[assignment]
parse_cli_response = None  # type: ignore[assignment]
build_cli_prompt = None  # type: ignore[assignment]
CLICallError = None  # type: ignore[assignment]
CLITimeoutError = None  # type: ignore[assignment]
assert_fallback_model = None  # type: ignore[assignment]


def _ensure_cli_imports() -> None:
    """Bind cli_bridge / cli_prompt_builder symbols on this module.

    Deferred to avoid a circular import (cli_bridge imports from base).
    Called by ``call_persona_via_cli``. Idempotent for callable symbols, but
    exception classes and ``assert_fallback_model`` are always rebound so
    that tests can patch the callables on this module without losing the
    real exception types used in the ``except`` clause.
    """
    global call_persona_cli, parse_cli_response, build_cli_prompt
    global CLICallError, CLITimeoutError, assert_fallback_model
    from trading.personas import cli_bridge as _cb
    from trading.personas import cli_prompt_builder as _cpb
    # Always rebind exception classes + the fallback guard so the
    # ``except (CLITimeoutError, CLICallError)`` clause has real classes,
    # even if a test has monkey-patched the callables on this module.
    CLICallError = _cb.CLICallError
    CLITimeoutError = _cb.CLITimeoutError
    assert_fallback_model = _cb.assert_fallback_model
    # Only fill these in if a test has not already patched them.
    if call_persona_cli is None:
        call_persona_cli = _cb.call_persona_cli
    if parse_cli_response is None:
        parse_cli_response = _cb.parse_cli_response
    if build_cli_prompt is None:
        build_cli_prompt = _cpb.build_cli_prompt


def is_cli_only_mode() -> bool:
    """Return True when the system is in ``cli_only_mode`` (single mode source).

    SPEC-TRADING-043 REQ-043-A5: the news-import fallback guard reuses this
    predicate — the SAME mechanism as :func:`block_if_cli_only_mode` — so there
    is no second mode-detection source. It reads ``system_state`` and treats the
    SPEC-016 column (``cli_only_mode``) and the legacy SPEC-015 column
    (``cli_personas_enabled``) as equivalent.

    SPEC-TRADING-053 REQ-053-B1 (ADR-004 strict 인지화): ``strict_cost_zero_mode``
    ON인 경우에도 True 반환 — 단일 SSOT 수정으로 데코레이터(analyzer/_llm_text)와
    scheduler.py:204가 strict ON에서 동시에 차단된다. blast-radius: 4개 호출자
    (block_if_cli_only_mode x2, scheduler:204, 직접 호출).

    Fall-open behaviour: if ``get_system_state`` itself raises (e.g. DB outage),
    this returns ``False``. That mirrors the decorator's fail-open — a DB problem
    must not wedge the only working path (here, the Haiku fallback proceeds).
    """
    try:
        state = get_system_state()
    except Exception as exc:  # noqa: BLE001 — fail open on DB outage
        LOG.warning(
            "is_cli_only_mode: get_system_state failed (%s) — reporting not-cli-only",
            exc,
        )
        return False
    # REQ-053-B1: strict_cost_zero_mode ON도 차단 조건에 포함 (ADR-004 SSOT)
    return bool(
        state.get("cli_only_mode")
        or state.get("cli_personas_enabled")
        or state.get("strict_cost_zero_mode")
    )


def block_if_cli_only_mode(fn: Callable[..., Any]) -> Callable[..., Any]:
    """Decorator: raise RuntimeError if ``cli_only_mode`` is active.

    SPEC-TRADING-016 REQ-016-1-3: Apply this decorator to any function that
    calls the Anthropic API directly *outside* the persona pipeline (e.g.
    one-off summarisers in news/intelligence or reports). It does NOT belong
    on the intentional Haiku fallback in ``call_persona_via_cli`` — that path
    is the single sanctioned exception.

    The decorator reads ``system_state`` and treats both the SPEC-016 column
    name (``cli_only_mode``) and the legacy SPEC-015 column name
    (``cli_personas_enabled``) as equivalent — see SPEC-TRADING-016
    REQ-016-1-3(d). If ``get_system_state`` itself fails (e.g. DB outage) the
    decorator falls open: the wrapped function executes normally so that a
    DB problem cannot wedge the only working code path.

    Raises:
        RuntimeError: When the system is in cli-only mode and the wrapped
            function attempts a direct API call.
    """

    @functools.wraps(fn)
    def wrapper(*args: Any, **kwargs: Any) -> Any:
        # SPEC-TRADING-043 REQ-043-A5: single mode source. ``is_cli_only_mode``
        # fails open to False on DB outage, so a DB problem lets ``fn`` run
        # (preserves the decorator's original fail-open contract).
        if is_cli_only_mode():
            raise RuntimeError(
                f"cli_only_mode=True but {fn.__qualname__} attempted a "
                "direct Anthropic API call. Use the CLI bridge "
                "(trading.personas.base.call_persona_via_cli) instead. "
                "See SPEC-TRADING-016 REQ-016-1-3."
            )
        return fn(*args, **kwargs)

    return wrapper

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
    # REQ-053-G1 (ADR-006 주 방어, D-CRIT-1): should_defer_paid_call() 진입점 가드.
    # [HARD] 배치 제약(D3): 반드시 try:(263) 이전에 위치해야 한다.
    # try: 안에서 raise하면 내부 except(393)이 RuntimeError를 삼켜 PersonaResult(error=...)로
    # 변환하고 디스패처가 빈 결과를 유효 결정으로 오인한다(REQ-053-G2가 막으려는 것).
    # 6개 디스패처는 call_persona에 로컬 try/except가 없으므로 raise가 상위 경계
    # (scheduler _wrap runner.py:158 / orchestrator 1123-1125)에서 흡수 → cost-0 사이클 스킵.
    if should_defer_paid_call():
        # REQ-053-G3/F2: 차단 시 CLI_DEGRADED_DEFER audit emit (raise 직전, D4)
        LOG.warning(
            "CLI_DEGRADED_DEFER strict_cost_zero_mode=True: 유료 직접호출 차단 "
            "persona=%s model=%s",
            persona_name, model,
        )
        try:
            audit(
                "CLI_DEGRADED_DEFER",
                actor="call_persona",
                details={
                    "persona": persona_name,
                    "model": model,
                    "path": "call_persona_direct",
                    "strict_cost_zero_mode": True,
                },
            )
        except Exception:  # noqa: BLE001
            pass
        raise RuntimeError(
            f"strict_cost_zero_mode: 직접 유료 호출 차단됨 — {persona_name} "
            "(REQ-053-G, call_persona 진입점 가드)"
        ) from None

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

        # REQ-053-F1 (G3): messages.create 직전 PAID_CALL 구조화 로그 (5지점 계측 #2)
        _log_paid_call(
            persona=persona_name, path="call_persona_direct", model=model, reason="direct_api"
        )
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
                # REQ-053-F1: messages.create 직전 PAID_CALL 계측 (5지점 #3, tool 재시도)
                _log_paid_call(
                    persona=persona_name, path="call_persona_tool_retry", model=model,
                    reason="tool_retry",
                )
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
                # REQ-053-F1: messages.create 직전 PAID_CALL 계측 (5지점 #4, JSON retry)
                _log_paid_call(
                    persona=persona_name, path="call_persona_json_retry", model=model,
                    reason="json_retry",
                )
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

# REQ-FALLBACK-06-4: 자동전환 in-process 카운터 (SPEC-015, 변경 없음)
_cli_failure_count: int = 0
_CLI_AUTO_DISABLE_THRESHOLD: int = 3

# SPEC-TRADING-053 REQ-053-C3 (ADR-005): strict fail-closed in-process 캐시.
# None = 콜드스타트(미확정) → fail-open(False), True/False = 직전 관측 상태.
_LAST_KNOWN_STRICT: bool | None = None

# SPEC-TRADING-052 REQ-052-B ADR-003: 조기경고 기본 쿨다운 1시간.
# SPEC-031의 HALT_NOTIFY_COOLDOWN_SECONDS(6h)보다 짧음 — 무음 크레딧 소진의 시간민감성.
CLI_DEGRADED_ALERT_COOLDOWN_SECONDS: int = 3600


# @MX:ANCHOR: [AUTO] SPEC-052 CLI degraded throttle gate (SPEC-031 maybe_notify_halt 동형)
# @MX:REASON: [AUTO] fan_in >= 3 (call_persona_via_cli 폴백경로·is_cli_mode_active stale경로·뉴스 strict경로); throttle 불변식이 여기 집중됨
# @MX:SPEC: SPEC-TRADING-052 REQ-052-B ADR-003
def maybe_send_cli_degraded_alert(
    cooldown_seconds: int | None = None,
    now_provider: Callable[[], datetime] | None = None,
) -> bool:
    """CLI degraded 조기경고를 쿨다운 throttle로 제한해 발송한다.

    SPEC-TRADING-052 REQ-052-B1/B2/ADR-003:
    - cli_degraded_notified_at IS NULL 또는 now - last >= cooldown → 발송 + 타임스탬프 갱신 → True
    - 쿨다운 내 → throttle → False
    - 상태는 system_state에 영속(재시작 생존, EC-2).
    - 텔레그램 실패는 swallow(fail-safe).

    Args:
        cooldown_seconds: 쿨다운 오버라이드(테스트 seam). None=기본 1h.
        now_provider: 테스트 clock seam — Callable[[], datetime]. None=wall clock.

    Returns:
        True=발송됨, False=throttle.
    """
    cooldown = CLI_DEGRADED_ALERT_COOLDOWN_SECONDS if cooldown_seconds is None else cooldown_seconds
    now = (now_provider or (lambda: datetime.now(UTC)))()

    try:
        state = get_system_state()
        last = state.get("cli_degraded_notified_at")
        if last is not None and (now - last).total_seconds() < cooldown:
            return False
        update_system_state(cli_degraded_notified_at=now, updated_by="cli_degraded_alert")
    except Exception:  # noqa: BLE001
        LOG.warning("maybe_send_cli_degraded_alert: system_state 접근 실패 — throttle 생략")
        return False

    try:
        from trading.alerts import telegram as tg
        tg.system_briefing(
            "CLI 불건강",
            "유료 Anthropic API로 비용 누수 중 — 호스트 재인증 필요. "
            "CLI degraded 상태 감지: cli 인증 만료 또는 워처 stale.",
        )
    except Exception:  # noqa: BLE001
        LOG.exception("CLI degraded alert 텔레그램 발송 실패")
    return True


def should_defer_paid_call() -> bool:
    """strict_cost_zero_mode ON에서 유료 호출을 차단해야 하면 True.

    SPEC-TRADING-053 REQ-053-C1 (ADR-005 strict fail-closed 분리):
    strict ON이면 cli_personas_enabled/cli_only_mode 값과 무관하게 True 반환.
    strict OFF → False (REQ-052-C2 불변, SPEC-016 폴백 보존).

    DB 예외 처리 (REQ-053-C3 ADR-005):
    - 콜드스타트(_LAST_KNOWN_STRICT=None) + DB 예외 → False(fail-open, D-new4)
      실제 strict-OFF 시스템의 SPEC-016 폴백을 막지 않기 위함(REQ-052-C2 HARD 우선).
    - last-known strict ON + DB 예외 → True(fail-closed)
    - last-known strict OFF + DB 예외 → False
    """
    global _LAST_KNOWN_STRICT
    try:
        state = get_system_state()
        # 성공 시 캐시 갱신
        _LAST_KNOWN_STRICT = bool(state.get("strict_cost_zero_mode", False))
    except Exception:  # noqa: BLE001 — DB 예외
        # REQ-053-C3 (D-new4): 콜드스타트(빈 캐시) → fail-open
        if _LAST_KNOWN_STRICT is None:
            return False
        # last-known ON → fail-closed, last-known OFF → fail-open
        if _LAST_KNOWN_STRICT:
            LOG.warning(
                "should_defer_paid_call: DB 장애 + last-known strict ON "
                "→ fail-closed(True). reason=db_unavailable_strict_failclosed",
            )
        return _LAST_KNOWN_STRICT
    if not _LAST_KNOWN_STRICT:
        return False
    # REQ-053-C1: strict ON → cli 플래그와 무관하게 True
    return True


def _persist_cli_degraded(
    consecutive_failures: int,
    now_provider: Callable[[], datetime] | None = None,
) -> None:
    """system_state에 CLI degraded latch를 영속 기록한다.

    SPEC-TRADING-052 REQ-052-A1/A3/A5:
    - cli_degraded=True로 latch (REQ-052-A5: in-process 카운터와 독립)
    - cli_degraded_since = 최초 전이 시각
    - cli_consecutive_failures = 영속 연속 실패 횟수
    - DB 실패 시 graceful log, 호출자에게 예외 전파 안 함(REQ-052-A4).
    """
    now = (now_provider or (lambda: datetime.now(UTC)))()
    try:
        state = get_system_state()
        # since는 최초 전이 시각을 유지 (기존 latch 중이면 갱신 안 함)
        since = state.get("cli_degraded_since") or now
        update_system_state(
            cli_degraded=True,
            cli_degraded_since=since,
            cli_consecutive_failures=consecutive_failures,
            updated_by="cli_degraded_guard",
        )
    except Exception:  # noqa: BLE001
        LOG.warning(
            "degraded 영속 기록 DB 실패 — graceful(fail-open 보존). 사이클 계속. "
            "consecutive_failures=%d",
            consecutive_failures,
        )


def _persist_cli_healthy() -> None:
    """system_state에서 CLI degraded 상태를 해제하고 카운터·throttle clock을 리셋한다.

    SPEC-TRADING-052 REQ-052-A2: CLI 성공 / 하트비트 신선 복귀 시 호출.
    - cli_degraded=False
    - cli_consecutive_failures=0
    - cli_degraded_notified_at=None (다음 에피소드 첫 발동이 즉시 발사되게)
    DB 실패 시 graceful(fail-open 보존).
    """
    try:
        update_system_state(
            cli_degraded=False,
            cli_consecutive_failures=0,
            cli_degraded_since=None,
            cli_degraded_notified_at=None,
            updated_by="cli_degraded_guard",
        )
    except Exception:  # noqa: BLE001
        LOG.warning("healthy 복귀 DB 기록 실패 — graceful")


def _log_paid_call(*, persona: str, path: str, model: str, reason: str) -> None:
    """유료 API 발동 구조화 로그 — 세 경로(폴백/직접/뉴스) 동일 스키마.

    SPEC-TRADING-052 REQ-052-D1: persona/path/model/reason 포함.
    """
    LOG.warning(
        "PAID_CALL persona=%s path=%s model=%s reason=%s",
        persona, path, model, reason,
    )


def _reset_cli_failures() -> None:
    """in-process 연속 실패 카운터 리셋 + 영속 healthy 복귀.

    SPEC-TRADING-052 REQ-052-A2: CLI 성공 시 호출.
    """
    global _cli_failure_count
    _cli_failure_count = 0
    _persist_cli_healthy()


def _record_cli_failure(
    persona_name: str,
    reason: str,
    now_provider: Callable[[], datetime] | None = None,
) -> None:
    """CLI 실패를 기록하고 임계 도달 시 자동전환 + degraded latch.

    REQ-FALLBACK-06-3: per-failure 알림 → SPEC-052 throttled alert로 대체(REQ-052-B3).
    REQ-FALLBACK-06-4: 3연속 → cli_personas_enabled=False 자동전환 (보존).
    REQ-052-A1: 영속 degraded latch (in-process 카운터와 독립, ADR-005).
    REQ-052-B3 [D7]: base.py L541의 per-failure 무throttle system_briefing("CLI fallback")
                     → maybe_send_cli_degraded_alert throttled alert로 대체.
                     L557/L558 "CLI auto-disabled" 알림은 그대로 보존.
    """
    global _cli_failure_count
    _cli_failure_count += 1
    persistent_count = _cli_failure_count  # 영속에 쓸 현재 값

    # SPEC-052 REQ-052-A1: 영속 연속 실패 카운터 + degraded latch 기록
    # (DB 실패 시 graceful — REQ-052-A4)
    _persist_cli_degraded(
        consecutive_failures=persistent_count,
        now_provider=now_provider,
    )

    # SPEC-052 REQ-052-B1/B2/B3 [D7]: throttled 조기경고 (per-failure 무throttle 대체)
    # 기존 L541 tg.system_briefing("CLI fallback", ...) 제거, throttled alert로 대체
    try:
        maybe_send_cli_degraded_alert(now_provider=now_provider)
    except Exception:  # noqa: BLE001
        pass

    # REQ-052-D1: 구조화 로그
    _log_paid_call(
        persona=persona_name,
        path="persona_fallback",
        model=_HAIKU_FALLBACK_MODEL,
        reason=reason,
    )

    if _cli_failure_count >= _CLI_AUTO_DISABLE_THRESHOLD:
        # REQ-053-D1: strict ON → auto-disable 부수효과(cli_personas=False·audit·TG) 생략.
        # 카운터 리셋은 strict와 무관하게 항상 수행 (E5: 무한 누적 방지, D-new3).
        strict = False
        try:
            strict = bool(get_system_state().get("strict_cost_zero_mode", False))
        except Exception:  # noqa: BLE001 — fail-open: strict 판정 실패 시 기존 동작
            strict = False

        if not strict:
            # REQ-053-D2: strict OFF에서는 기존 자동전환 동작 불변
            try:
                update_system_state(cli_personas_enabled=False, updated_by="auto_disable")
                audit(
                    "CLI_AUTO_DISABLED",
                    actor="call_persona",
                    details={"consecutive_failures": _cli_failure_count},
                )
                # REQ-052-B3 [D7]: L557/L558 "CLI auto-disabled" 알림은 그대로 보존
                from trading.alerts import telegram as tg
                tg.system_briefing(
                    "CLI auto-disabled",
                    f"CLI mode auto-disabled after {_cli_failure_count} consecutive failures",
                )
            except Exception:  # noqa: BLE001
                pass
        # [HARD] REQ-053-D(D-new3)/REQ-052-A5: 카운터 리셋은 strict와 무관하게 항상.
        # 영속 degraded latch는 건드리지 않음 — degraded는 A2(성공/하트비트)에서만 해제.
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
    # SPEC-TRADING-016 REQ-016-1-3/4: Bind cli_bridge symbols on this module
    # so tests can patch them and so the fallback path uses the centralised
    # model whitelist.
    _ensure_cli_imports()

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

        # SPEC-TRADING-016 REQ-016-1-4: Single source of truth for the
        # fallback model. The whitelist guard below ensures the log line
        # below and the actual API call cannot drift apart.
        fallback_model = _HAIKU_FALLBACK_MODEL
        assert_fallback_model(fallback_model)

        # SPEC-TRADING-016 REQ-016-1-4(c): Log message must reference the
        # actual model being used, not a hardcoded literal.
        LOG.warning(
            "CLI failed for %s (%s), falling back to %s API",
            persona_name, reason, fallback_model,
        )

        # SPEC-TRADING-052 REQ-052-C1/ADR-001: strict_cost_zero_mode ON → defer(스킵)
        # 기본 OFF에서는 이 블록을 건너뛰어 SPEC-016 기존 동작 보존(REQ-052-C2).
        if should_defer_paid_call():
            audit(
                "CLI_DEGRADED_DEFER",
                actor="call_persona_via_cli",
                details={
                    "persona": persona_name,
                    "reason": reason,
                    "path": "persona_fallback",
                    "strict_cost_zero_mode": True,
                },
            )
            LOG.warning(
                "strict_cost_zero_mode=True: 유료 폴백 차단(defer). persona=%s reason=%s",
                persona_name, reason,
            )
            # throttle-aligned 알림
            try:
                maybe_send_cli_degraded_alert()
            except Exception:  # noqa: BLE001
                pass
            raise RuntimeError(
                f"strict_cost_zero_mode: 유료 폴백 차단됨 — {persona_name} ({reason})"
            ) from None

        # REQ-FALLBACK-06-6: Haiku only fallback
        try:
            return call_persona(
                persona_name=persona_name,
                model=fallback_model,
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
    SPEC-TRADING-052 REQ-052-A1b: 워처 stale 시 degraded로 마킹(직접경로 사각지대 해소).
    SPEC-TRADING-053 REQ-053-D4: strict ON이면 워처 stale에도 False를 반환하지 않음(보조 방어).
      디스패처를 call_persona_via_cli로 라우팅하여 거기서 should_defer_paid_call이 차단.
    """
    try:
        from trading.db.session import get_system_state
        state = get_system_state()
        if not state.get("cli_personas_enabled", False):
            return False

        # REQ-SCHED-07-5: Check watcher heartbeat staleness
        from trading.personas.cli_bridge import is_watcher_alive
        if not is_watcher_alive():
            # REQ-053-D4 (보조 방어): strict ON이면 워처 stale에도 False로 빠지지 않음.
            # call_persona_via_cli로 라우팅 → 거기서 should_defer_paid_call이 유료 호출 차단.
            if state.get("strict_cost_zero_mode", False):
                LOG.warning(
                    "strict_cost_zero_mode=True: 워처 stale이나 False 차단 — "
                    "call_persona_via_cli로 라우팅(should_defer가 거기서 차단). REQ-053-D4"
                )
                return True
            LOG.warning("CLI mode enabled but watcher heartbeat stale — using API fallback")
            # SPEC-TRADING-052 REQ-052-A1b: 워처 stale → degraded 마킹(직접경로 사각지대)
            # in-process 카운터 미사용(직접경로엔 _record_cli_failure 없음) — 영속 latch 직접 기록
            _persist_cli_degraded(consecutive_failures=1)
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
