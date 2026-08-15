"""포트폴리오 게이트 — 매수 축소/드롭의 "왜"가 감사에 남는가 (2026-08-15).

30일치 PORTFOLIO_ADJUSTMENT 가 {ticker, 6→3} 만 있고 사유가 없어, 평균 47%
삭감이 누구 소행인지 알 수 없었다. 페르소나는 rationale 을 매번 보내는데
게이트가 버리고 있었고, 섹터캡·현금바닥 드롭은 감사 자체가 없었다.
세 게이트 모두 gate 이름 + 사유를 남겨야 한다.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from trading.personas import portfolio_gate as pg


def _buy(t, qty=6):
    return {"ticker": t, "side": "buy", "qty": qty}


def _run(*, adjusted=None, rejected=None, cash_pct=50.0, regime="neutral"):
    """페르소나 응답을 주입하고 audit 호출을 수집한다."""
    audit = MagicMock()
    pres = SimpleNamespace(
        response_json={"adjusted_signals": adjusted or [], "rejected": rejected or []}
    )
    holdings = [{"ticker": str(i)} for i in range(6)]  # >= 5 -> 페르소나 활성
    with (
        patch.object(pg.portfolio, "run", return_value=pres),
        patch.object(pg, "get_effective_regime", return_value=(regime, "neutral")),
        patch.object(pg, "audit", audit),
        patch.object(pg.tg, "system_briefing"),
        patch.object(pg, "_apply_sector_cap_guard", side_effect=lambda s, i, **k: (s, i)),
    ):
        pg._apply_portfolio_adjustment(
            [_buy("316140"), _buy("096770", 1)], [3036, 3037],
            holdings=holdings, holdings_count=6,
            total_assets=10_000_000, cash_pct=cash_pct,
            today="2026-08-14", cycle_kind="intraday",
        )
    return audit


def _events(audit, name):
    return [c.kwargs["details"] for c in audit.call_args_list if c.args[0] == name]


class TestPersonaRationaleIsAudited:
    def test_adjusted_entry_carries_gate_and_rationale(self):
        """8/14 실제 응답 재현: 316140 6→3, rationale='...물타기 형태...절반만'."""
        audit = _run(adjusted=[{
            "ticker": "316140", "side": "buy",
            "qty_original": 6, "qty_adjusted": 3,
            "rationale": "손실 중인 기존 포지션에 물타기 형태 — 절반만 집행",
        }])
        ev = _events(audit, "PORTFOLIO_ADJUSTMENT")
        assert len(ev) == 1
        a = ev[0]["adjusted"][0]
        assert a["ticker"] == "316140"
        assert (a["qty_original"], a["qty_adjusted"]) == (6, 3)
        assert a["gate"] == "portfolio_persona"
        assert "물타기" in a["rationale"]

    def test_rejected_entry_carries_reason(self):
        audit = _run(rejected=[{"ticker": "096770", "reason": "섹터 편중"}])
        ev = _events(audit, "PORTFOLIO_ADJUSTMENT")
        r = ev[0]["rejected"][0]
        assert r["ticker"] == "096770"
        assert r["gate"] == "portfolio_persona"
        assert r["reason"] == "섹터 편중"

    def test_no_change_emits_nothing(self):
        """페르소나가 원안 통과시키면 감사 없음(소음 금지) — 종전 동작 보존."""
        audit = _run(adjusted=[{
            "ticker": "316140", "qty_original": 6, "qty_adjusted": 6, "rationale": "원안 통과",
        }])
        assert _events(audit, "PORTFOLIO_ADJUSTMENT") == []


class TestCashFloorDropIsAudited:
    def test_below_floor_drop_records_gate_and_reason(self):
        """현금이 바닥 아래면 BUY 드롭 — 종전엔 res_rejected 에만 들어가고 감사 없음."""
        audit = _run(cash_pct=1.0, regime="bear")  # bear floor 보다 확실히 아래
        ev = _events(audit, "PORTFOLIO_GATE_DROP")
        floor_ev = [e for e in ev if e.get("gate") == "cash_floor"]
        assert len(floor_ev) == 1
        e = floor_ev[0]
        assert "cash 1.0%" in e["reason"]
        assert "floor" in e["reason"]
        assert {d["decision_id"] for d in e["dropped"]} == {3036, 3037}
        assert e["decision_scope"] == "batch"


class TestSectorCapDropIsAudited:
    def test_sector_cap_drop_records_gate_reason_decision_id(self):
        """섹터캡 드롭은 텔레그램만 있고 감사가 없었다. enforce_sector_cap 결과를
        주입해 게이트가 감사를 남기는지만 본다(섹터 계산 자체는 별도 테스트)."""
        audit = MagicMock()
        signals = [_buy("316140"), _buy("055550")]
        sig_ids = [3036, 3040]
        dropped_infos = [{"ticker": "316140", "reason": "금융 42% > 한도 40%"}]
        with (
            patch.object(pg, "audit", audit),
            patch.object(pg.tg, "system_briefing"),
            patch.object(pg, "get_sectors_from_db", return_value={}),
            patch.object(pg, "_build_price_map", return_value={}),
            patch.object(pg, "_enrich_holdings_with_sector", return_value=[]),
            patch.object(pg, "enforce_sector_cap", return_value=([signals[1]], dropped_infos)),
        ):
            rej: list[int] = []
            _kept, kept_ids = pg._apply_sector_cap_guard(
                signals, sig_ids, holdings=[], total_assets=10_000_000,
                regime="neutral", cycle_kind="intraday", res_rejected=rej,
            )
        assert kept_ids == [3040]
        assert rej == [3036]
        ev = [c.kwargs["details"] for c in audit.call_args_list
              if c.args and c.args[0] == "PORTFOLIO_GATE_DROP"]
        assert len(ev) == 1
        e = ev[0]
        assert e["gate"] == "sector_cap"
        assert e["cycle"] == "intraday"
        d = e["dropped"][0]
        assert d["ticker"] == "316140"
        assert "40%" in d["reason"]
        assert d["decision_id"] == 3036
        assert e["decision_scope"] == "batch"
