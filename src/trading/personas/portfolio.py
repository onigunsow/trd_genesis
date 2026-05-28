"""Portfolio persona — Sonnet 4.6, M5+, holdings ≥ 5.

Adjusts decision signal sizing from a portfolio perspective. Optional layer.
"""

from __future__ import annotations

from datetime import date
from typing import Any

from trading.personas.base import (
    call_persona,
    call_persona_via_cli,
    is_cli_mode_active,
    render_prompt,
)

MODEL = "claude-sonnet-4-6"
PERSONA = "portfolio"
ACTIVATION_THRESHOLD = 5


def is_active(holdings_count: int) -> bool:
    return holdings_count >= ACTIVATION_THRESHOLD


def run(input_data: dict[str, Any], cycle_kind: str = "pre_market"):
    today = input_data.get("today") or date.today().isoformat()
    system_prompt = render_prompt("portfolio.jinja", **{**input_data, "today": today})
    user_msg = "결정 페르소나 시그널을 포트폴리오 관점에서 조정한 결과를 JSON으로 제출하세요."
    trigger_context = {"holdings_count": input_data.get("holdings_count", 0)}

    # SPEC-TRADING-034 REQ-034-9: CLI routing for zero-cost operation under
    # cli_only_mode (SPEC-015/016). Mirrors the decision.py / macro.py branch so
    # the portfolio persona no longer requires a paid Sonnet API call (which
    # raises RuntimeError when ANTHROPIC_API_KEY is unset). expect_json kept.
    if is_cli_mode_active():
        return call_persona_via_cli(
            persona_name=PERSONA,
            model=MODEL,
            cycle_kind=cycle_kind,
            system_prompt=system_prompt,
            user_message=user_msg,
            trigger_context=trigger_context,
            expect_json=True,
            tickers=None,  # Portfolio has no ticker-specific tools
            input_data=input_data,
        )

    return call_persona(
        persona_name=PERSONA,
        model=MODEL,
        cycle_kind=cycle_kind,
        system_prompt=system_prompt,
        user_message=user_msg,
        trigger_context=trigger_context,
        max_tokens=2000,
        expect_json=True,
    )
