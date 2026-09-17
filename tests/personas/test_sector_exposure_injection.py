"""2026-09-17 — 결정 페르소나에 현재 섹터 비중·매수 여력 주입.

페르소나는 섹터 한도(35%)는 알았지만 현재 비중을 몰라 9/14 금융 매수 3건을 제안했고
전부 portfolio_gate 섹터 cap 가드에 차단됐다. 프롬프트 여력 숫자는 가드와 원 단위로
일치해야 한다 — 어긋나면 "통과한다던 매수가 차단"되는 새 모순이 생긴다.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import patch

import pytest

from trading.personas.decision import _sector_exposure
from trading.personas.sector_cap_guard import enforce_sector_cap

_TOTAL = 10_000_000
_SECTORS = {"086790": "금융", "055550": "금융", "042660": "운송장비·부품",
            "009540": "금융", "000660": "전기·전자", "999999": "미분류"}


def _input(cands: list[str]) -> dict[str, Any]:
    return {
        "assets": {
            "total_assets": _TOTAL,
            "holdings": [
                {"ticker": "086790", "eval_amount": 2_000_000},
                {"ticker": "055550", "eval_amount": 1_000_000},
                {"ticker": "042660", "eval_amount": 500_000},
            ],
        },
        "micro_candidates": {"buy": [{"ticker": t} for t in cands]},
    }


def _exposure(cands: list[str], cap: float = 35.0) -> dict[str, Any]:
    with patch(
        "trading.personas.sector_cap_guard.get_sectors_from_db",
        side_effect=lambda ts: {t: _SECTORS[t] for t in ts if t in _SECTORS},
    ):
        out = _sector_exposure(_input(cands), cap)
    assert out is not None
    return out


def test_sector_pct_and_room_follow_holdings() -> None:
    ex = _exposure([])
    fin = next(s for s in ex["sectors"] if s["sector"] == "금융")

    assert fin["pct"] == 30.0
    assert fin["room_pct"] == 5.0
    assert fin["room_krw"] == 500_000
    assert fin["tickers"] == ["086790", "055550"]
    assert ex["sectors"][0]["sector"] == "금융"  # 비중 큰 순


def test_candidate_uses_its_sector_room_and_unknown_is_flagged() -> None:
    ex = _exposure(["009540", "000660", "999999"])
    by = {c["ticker"]: c for c in ex["candidates"]}

    assert by["009540"]["room_krw"] == 500_000       # 금융 여력
    assert by["000660"]["room_krw"] == 3_500_000     # 미보유 섹터 = cap 전체
    assert by["999999"]["sector"] is None            # 미분류 = 가드 fail-open


def test_no_room_is_zero_not_negative_krw() -> None:
    ex = _exposure(["009540"], cap=25.0)
    fin = next(s for s in ex["sectors"] if s["sector"] == "금융")

    assert fin["room_pct"] == -5.0
    assert fin["room_krw"] == 0


@pytest.mark.parametrize(("delta", "blocked"), [(-1, False), (+2, True)])
def test_prompt_room_matches_the_code_gate_to_the_won(delta: int, blocked: bool) -> None:
    """여력 -1원은 가드 통과, +2원은 차단 — 프롬프트와 코드가 같은 경계를 본다."""
    ex = _exposure(["009540"])
    room = ex["candidates"][0]["room_krw"]
    holdings = [
        {"ticker": "086790", "eval_amount": 2_000_000, "sector": "금융"},
        {"ticker": "055550", "eval_amount": 1_000_000, "sector": "금융"},
    ]
    _kept, dropped = enforce_sector_cap(
        [{"side": "buy", "ticker": "009540", "qty": 1}],
        holdings=holdings, total_portfolio=_TOTAL, sector_cap_pct=35.0,
        price_map={"009540": room + delta}, sector_map={"009540": "금융"},
    )
    assert bool(dropped) is blocked


def test_zero_total_assets_returns_none() -> None:
    assert _sector_exposure({"assets": {"total_assets": 0}}, 35.0) is None


def test_lookup_failure_returns_none() -> None:
    with patch(
        "trading.personas.sector_cap_guard.get_sectors_from_db",
        side_effect=RuntimeError("db"),
    ):
        assert _sector_exposure(_input(["009540"]), 35.0) is None


def test_prompt_renders_table_and_blocks_full_sector() -> None:
    from trading.personas.base import render_prompt

    ex = _exposure(["009540", "000660"], cap=25.0)
    p = render_prompt("decision.jinja", **{
        "today": "2026-09-17", "cycle_kind": "intraday", "event_trigger": None,
        "car_context": None, "dynamic_thresholds_enabled": False,
        "assets": {"total_assets": _TOTAL}, "sector_exposure": ex,
    })

    assert "[섹터 비중 — 코드 차단선 25.0%]" in p
    assert "| 금융 | 30.0% | **없음(-5.0%p) — 신규 매수 차단** |" in p
    assert "009540: 금융 — **여력 없음, 매수 제안 금지(코드 차단)**" in p
    assert "000660: 전기·전자 — 이 종목 매수금액은 약 2,500,000원 이하여야 통과" in p
