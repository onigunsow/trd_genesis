"""decision.jinja — 프롬프트가 코드에 없는 것을 말하지 않는다 (2026-08-24).

8/24 로그에서 같은 병의 결함 3건이 나왔다.

1. 프롬프트가 `get_dynamic_thresholds 도구를 호출하여 effective_stop 을 쓰라`고
   지시했는데, 호출 그래프상 그 함수의 호출자는 포지션 워치독뿐이다. 결정 페르소나에
   그 도구를 준 적이 없고 값이 프롬프트에 실리지도 않았다 — 손절선을 환각으로 채우거나,
   CLI 모드에서 도구를 시도하다 `--max-turns 1` 에 걸려 사이클째 죽었다
   (`Error: Reached max turns (1)`, 실패 stdout len=28 로 재현 확인).
2. 하드 스톱 플로어는 8/15 에 -10 → -15% 로 넓혔는데 문구는 -10% 로 남아 있었다.
3. "같은 날 같은 종목 매수는 1회만 통과한다"는 룰만 말하고 어느 종목을 이미 샀는지는
   알려주지 않았다 — 8/24 LIMIT_BREACH 20건이 전부 repeat_buy 재제안.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from jinja2 import Environment, FileSystemLoader

_PROMPTS = (
    Path(__file__).resolve().parent.parent.parent / "src" / "trading" / "personas" / "prompts"
)


def _render(**ctx) -> str:
    env = Environment(loader=FileSystemLoader(str(_PROMPTS)))
    base = dict(today="2026-08-24", cycle_kind="intraday", assets={}, blocked_tickers={})
    base.update(ctx)
    return env.get_template("decision.jinja").render(**base)


@pytest.fixture
def with_thresholds() -> str:
    return _render(
        dynamic_thresholds_enabled=True,
        stop_floor_pct=15.0,
        holding_thresholds=[{
            "ticker": "012330", "name": "현대모비스", "pnl_pct": 0.3,
            "effective_stop": -15.0, "effective_take": 21.72,
            "trailing_stop_pct": -8.15, "volatility_regime": "extreme",
            "source": "dynamic",
        }],
        bought_today={"012330": 1, "251270": 2},
    )


class TestNoPhantomTool:
    def test_tool_call_instruction_is_gone(self, with_thresholds):
        """존재하지 않는 도구를 호출하라고 시키면 CLI 모드에서 사이클이 죽는다."""
        assert "get_dynamic_thresholds 도구를 호출" not in with_thresholds

    def test_real_threshold_values_are_rendered(self, with_thresholds):
        assert "012330 현대모비스" in with_thresholds
        assert "-15.0" in with_thresholds
        assert "21.72" in with_thresholds

    def test_says_values_match_the_watchdog(self, with_thresholds):
        """페르소나와 워치독이 같은 숫자를 보고 있음을 프롬프트가 밝힌다."""
        assert "워치독" in with_thresholds


class TestStopFloorMatchesCode:
    def test_floor_is_injected_not_hardcoded(self, with_thresholds):
        assert "-15.0%" in with_thresholds

    def test_stale_ten_percent_floor_is_gone(self):
        """플로어 문구가 코드 상수(STOP_FLOOR_PCT)와 어긋나면 안 된다."""
        from trading.strategy.volatility.thresholds import STOP_FLOOR_PCT

        rendered = _render(stop_floor_pct=abs(STOP_FLOOR_PCT))
        assert "플로어(-10%)" not in rendered
        assert f"플로어(-{abs(STOP_FLOOR_PCT)}%)" in rendered


class TestBoughtTodayIsDisclosed:
    def test_lists_tickers_bought_today(self, with_thresholds):
        assert "오늘 이미 매수한 종목" in with_thresholds
        assert "012330: 1회" in with_thresholds
        assert "251270: 2회" in with_thresholds

    def test_section_absent_when_nothing_bought(self):
        assert "오늘 이미 매수한 종목" not in _render(bought_today={})
