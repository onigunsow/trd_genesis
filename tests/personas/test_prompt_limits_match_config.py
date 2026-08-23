"""2026-08-23 프롬프트-코드 한도 모순 제거.

프롬프트가 '종목당 20%·일일 -1%' 라고 말하는 동안 코드는 15%·-2.5% 를 강제하고 있었다.
페르소나가 틀린 한도로 사이징하면 제안이 거부되거나(과대) 스스로를 과하게 조인다(과소).
한도 숫자는 config 단일 원천에서만 나와야 한다.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from jinja2 import Environment, FileSystemLoader

from trading import config
from trading.personas.regime_branch import prompt_context

PROMPTS = Path(config.__file__).parent / "personas" / "prompts"
TEMPLATES = ("decision.jinja", "risk.jinja")


def _render(name: str) -> str:
    env = Environment(loader=FileSystemLoader(str(PROMPTS)))
    return env.get_template(name).render(
        **prompt_context("neutral", "neutral"),
        assets={"holdings": []},
        daily_order_count=0,
        daily_pnl_pct=0,
    )


@pytest.mark.parametrize("name", TEMPLATES)
def test_rendered_limits_match_config(name):
    text = _render(name)
    expected = (
        f"-{abs(config.RISK_DAILY_MAX_LOSS) * 100}%",
        f"{config.RISK_PER_TICKER_MAX_POSITION * 100}%",
        f"{config.RISK_TOTAL_INVESTED_MAX * 100}%",
        f"{config.RISK_SINGLE_ORDER_MAX * 100}%",
    )
    for token in expected:
        assert token in text, f"{name}: {token} 누락"


@pytest.mark.parametrize("name", TEMPLATES)
def test_no_stale_hardcoded_limits(name):
    """과거 값이 그대로 남아 있으면 실패 — config 를 바꿔도 프롬프트가 안 따라온 것."""
    text = _render(name)
    for stale in ("종목당 최대 포지션: 20.0%", "일일 최대 손실: -1.0%", "자본의 5%를 초과하면"):
        assert stale not in text, f"{name}: 낡은 하드코딩 '{stale}' 잔존"


def test_confidence_threshold_is_reachable():
    """confidence 정의(20일 수익 확률)에서 도달 가능한 임계여야 한다.

    실측(8/8~ buy 105건) 최대가 0.60 이었다. 임계가 그보다 높으면 '절반 진입' 룰이
    100% 발동해 목표 비중의 절반이 사실상 상한이 된다.
    """
    assert 0.50 < config.DECISION_CONFIDENCE_FULL_SIZE <= 0.60
    assert str(config.DECISION_CONFIDENCE_FULL_SIZE) in _render("decision.jinja")
