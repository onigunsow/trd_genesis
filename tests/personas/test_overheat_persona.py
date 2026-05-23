"""SPEC-TRADING-026 — Overheating (단기과열) softening at the persona layer.

SPEC-025/018 excluded ALL blocked tickers (incl. 단기과열 55) from the micro
watchlist, the decision candidate filter, and the prompt. SPEC-026 keeps 55 as
a *cautioned* (reduce-weight) candidate while still hard-excluding the genuine
risk states (관리 51 / 투자위험 52 / 투자경고 53 / 거래정지 54) and any entry
without an explicit stat_cls 55.

Covered here:
- ``_split_blocked`` partitions a blocked dict by stat_cls (REQ-026-6).
- ``_build_micro_input`` keeps 55 in the watchlist, drops 51 (REQ-026-6).
- micro.jinja / decision.jinja render 55 in a *caution / reduce-weight* section
  rather than the hard-exclude section (REQ-026-7).
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest
from jinja2 import Environment, FileSystemLoader

# ---------------------------------------------------------------------------
# REQ-026-6 — _split_blocked + watchlist keeps 55, drops hard blocks
# ---------------------------------------------------------------------------

class TestSplitBlocked:
    def test_partitions_by_stat_cls(self):
        from trading.personas.orchestrator import _split_blocked

        blocked = {
            "005930": {"reason": "단기과열", "stat_cls": "55"},
            "000660": {"reason": "관리", "stat_cls": "51"},
            "035720": {"reason": "intraday safety"},  # no stat_cls
        }
        hard, over = _split_blocked(blocked)

        assert set(over) == {"005930"}
        assert set(hard) == {"000660", "035720"}  # missing stat_cls => hard

    def test_none_is_safe(self):
        from trading.personas.orchestrator import _split_blocked

        hard, over = _split_blocked(None)
        assert hard == {} and over == {}


class TestMicroWatchlistKeepsOverheated:
    def test_overheated_kept_hard_dropped(self):
        from trading.personas import context as ctx
        from trading.personas.orchestrator import _build_micro_input

        # screened universe: one overheated(55), one hard(51), one clean.
        screened = ["005930", "000660", "035720"]
        blocked_cache_payload = {
            "date": "2026-05-22",
            "blocked": {
                "005930": {"reason": "단기과열", "stat_cls": "55"},
                "000660": {"reason": "관리종목", "stat_cls": "51"},
            },
            "blocked_today_by_safety": [],
        }

        captured: dict[str, object] = {}

        def fake_assemble(*, macro_summary, watchlist, blocked_tickers=None, **kwargs):
            captured["watchlist"] = watchlist
            captured["blocked_tickers"] = blocked_tickers
            return {}

        with (
            patch(
                "trading.personas.orchestrator.get_blocked_tickers",
                return_value=blocked_cache_payload,
            ),
            patch(
                "trading.personas.orchestrator.load_screened_tickers",
                return_value=screened,
            ),
            patch.object(ctx, "assemble_micro_input", side_effect=fake_assemble),
        ):
            _build_micro_input(today="2026-05-22", macro_summary=None)

        watchlist = captured["watchlist"]
        assert "005930" in watchlist, "단기과열(55) must be KEPT in watchlist"
        assert "035720" in watchlist, "clean ticker must remain"
        assert "000660" not in watchlist, "관리(51) must be hard-excluded"

        # Full blocked dict (incl. 55) is still forwarded so the prompt can
        # render the caution section.
        passed = captured["blocked_tickers"]
        assert "005930" in passed and "000660" in passed


# ---------------------------------------------------------------------------
# REQ-026-7 — templates bucket 55 into a caution section
# ---------------------------------------------------------------------------

@pytest.fixture
def jinja_env() -> Environment:
    prompts_dir = (
        Path(__file__).resolve().parent.parent.parent
        / "src" / "trading" / "personas" / "prompts"
    )
    return Environment(loader=FileSystemLoader(str(prompts_dir)))


def _micro_ctx(**overrides):
    base = {
        "today": "2026-05-22",
        "macro_summary": "(없음)",
        "universe_snapshot": "- 005930 삼성전자: stub",
        "recent_disclosures": [],
        "user_watchlist": "",
        "static_context": "",
        "static_news": "",
        "memory": "(활성 메모리 없음)",
        "blocked_tickers": {},
    }
    base.update(overrides)
    return base


class TestMicroTemplateBuckets:
    def test_overheated_in_caution_hard_in_exclude(self, jinja_env):
        template = jinja_env.get_template("micro.jinja")
        blocked = {
            "005930": {"reason": "단기과열", "stat_cls": "55"},
            "000660": {"reason": "관리종목", "stat_cls": "51"},
        }
        rendered = template.render(**_micro_ctx(blocked_tickers=blocked))

        idx_exclude = rendered.find("매매제한 종목")
        idx_caution = rendered.find("단기과열 주의")
        assert idx_exclude >= 0, "hard-exclude section missing"
        assert idx_caution >= 0, "단기과열 주의(caution) section missing"
        assert idx_exclude < idx_caution, "exclude section must precede caution"

        # Ticker codes also appear in static prompt text, so scope by section.
        exclude_section = rendered[idx_exclude:idx_caution]
        caution_section = rendered[idx_caution:]
        assert "000660" in exclude_section and "000660" not in caution_section, (
            "51 must be listed only in the hard-exclude section"
        )
        assert "005930" in caution_section, "55 must be listed in the caution section"
        assert "005930" not in exclude_section, "55 must NOT be in the exclude list"
        # Caution must instruct reduce-weight, not exclusion.
        assert "비중 축소" in rendered

    def test_only_overheated_no_exclude_header(self, jinja_env):
        """When every blocked entry is 55, the hard-exclude header is omitted."""
        template = jinja_env.get_template("micro.jinja")
        blocked = {"005930": {"reason": "단기과열", "stat_cls": "55"}}
        rendered = template.render(**_micro_ctx(blocked_tickers=blocked))

        assert "단기과열 주의" in rendered
        assert "매매제한 종목" not in rendered


class TestOverheatOrderPolicy:
    """REQ-026-8: size-cap + limit-only for overheated BUYs; sells untouched."""

    def test_buy_overheated_size_capped_and_limit(self):
        from trading.personas.orchestrator import _apply_overheat_order_policy
        from trading.risk.market_safety import OVERHEAT_SIZE_FACTOR

        sig: dict = {}
        new_qty, capped = _apply_overheat_order_policy(
            sig, qty=10, side="buy", ref_price=70_000, overheated=True
        )
        assert capped is True
        assert new_qty == int(10 * OVERHEAT_SIZE_FACTOR)
        assert sig["order_type"] == "limit"
        assert sig["limit_price"] == 70_000
        assert sig["qty"] == new_qty

    def test_sell_overheated_unchanged(self):
        from trading.personas.orchestrator import _apply_overheat_order_policy

        sig: dict = {}
        new_qty, capped = _apply_overheat_order_policy(
            sig, qty=10, side="sell", ref_price=70_000, overheated=True
        )
        assert capped is False
        assert new_qty == 10
        assert sig == {}, "sell must stay a market order (no risk-exit throttle)"

    def test_normal_buy_unchanged(self):
        from trading.personas.orchestrator import _apply_overheat_order_policy

        sig: dict = {}
        new_qty, capped = _apply_overheat_order_policy(
            sig, qty=10, side="buy", ref_price=70_000, overheated=False
        )
        assert capped is False and new_qty == 10 and sig == {}

    def test_qty_floored_at_one(self):
        from trading.personas.orchestrator import _apply_overheat_order_policy

        sig: dict = {}
        new_qty, capped = _apply_overheat_order_policy(
            sig, qty=1, side="buy", ref_price=70_000, overheated=True
        )
        assert capped is True and new_qty == 1  # max(1, int(0.5)) == 1


class TestExecuteSignalOrderType:
    """REQ-026-8: _execute_signal honours per-signal order_type / limit_price so
    the orchestrator can force a limit order on overheated entries."""

    def test_honours_limit_order(self):
        from trading.personas import orchestrator as orch

        captured: dict[str, object] = {}

        def fake_buy(client, *, ticker, qty, order_type="market",
                     limit_price=None, persona_decision_id=None):
            captured.update(
                ticker=ticker, qty=qty, order_type=order_type, limit_price=limit_price
            )
            return {"order_id": 7}

        with patch.object(orch, "kis_buy", side_effect=fake_buy):
            sig = {
                "side": "buy", "ticker": "005930", "qty": 5,
                "order_type": "limit", "limit_price": 70_000,
            }
            oid = orch._execute_signal(client=None, sig=sig, decision_id=1)

        assert oid == 7
        assert captured["order_type"] == "limit"
        assert captured["limit_price"] == 70_000
        assert captured["qty"] == 5

    def test_defaults_to_market(self):
        from trading.personas import orchestrator as orch

        captured: dict[str, object] = {}

        def fake_buy(client, *, ticker, qty, order_type="market",
                     limit_price=None, persona_decision_id=None):
            captured.update(order_type=order_type, limit_price=limit_price)
            return {"order_id": 8}

        with patch.object(orch, "kis_buy", side_effect=fake_buy):
            sig = {"side": "buy", "ticker": "005930", "qty": 5}
            orch._execute_signal(client=None, sig=sig, decision_id=1)

        assert captured["order_type"] == "market"
        assert captured["limit_price"] is None


class TestDecisionTemplateBuckets:
    def _decision_ctx(self, **overrides):
        base = {
            "today": "2026-05-22",
            "cycle_kind": "intraday",
            "assets": {},  # decision.jinja reads assets.* (default-guarded)
            "blocked_tickers": {},
        }
        base.update(overrides)
        return base

    def test_overheated_not_in_proposal_ban(self, jinja_env):
        template = jinja_env.get_template("decision.jinja")
        blocked = {
            "005930": {"reason": "단기과열", "stat_cls": "55"},
            "000660": {"reason": "거래정지", "stat_cls": "54"},
        }
        rendered = template.render(**self._decision_ctx(blocked_tickers=blocked))

        # 54 → hard "제안 금지"; 55 → caution, must NOT be in the ban list.
        idx_ban = rendered.find("매매 제한 종목")
        idx_caution = rendered.find("단기과열 주의")
        assert idx_ban >= 0
        assert idx_caution >= 0
        assert idx_ban < idx_caution, "ban section must precede caution"

        # Ticker codes appear in static prompt text too, so scope by section.
        ban_section = rendered[idx_ban:idx_caution]
        caution_section = rendered[idx_caution:]
        assert "000660" in ban_section and "000660" not in caution_section, (
            "54 must be listed only in the proposal-ban section"
        )
        assert "005930" in caution_section, "55 must be in the caution section"
        assert "005930" not in ban_section, "55 must NOT be in the proposal-ban list"
