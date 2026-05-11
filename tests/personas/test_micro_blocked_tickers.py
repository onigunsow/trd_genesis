"""SPEC-TRADING-018 — Micro persona blocked-ticker awareness.

Reproduction tests for the 2026-05-11 zero-trade incident where two intraday
cycles (09:30, 11:00) returned ``signals: []`` because the hardcoded
``DEFAULT_WATCHLIST`` (5 large-caps) entirely overlapped with the exchange's
``단기과열`` (stat_cls=55) blocked-ticker list.

Coverage:
- REQ-018-1: orchestrator filters blocked tickers from expanded_watchlist
- REQ-018-2: context.assemble_micro_input wires blocked_tickers into return dict
- REQ-018-3: micro.jinja renders [매매제한 종목] block when blocked_tickers non-empty
- REQ-018-4: screened fallback when DEFAULT_WATCHLIST is fully blocked
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest
from jinja2 import Environment, FileSystemLoader

# ---------------------------------------------------------------------------
# REQ-018-1: orchestrator filters blocked tickers
# ---------------------------------------------------------------------------


class TestBuildMicroInputFiltersBlockedTickers:
    """REQ-018-1: ``_build_micro_input`` must exclude tickers in blocked_cache."""

    def test_build_micro_input_filters_blocked_tickers(self):
        """When DEFAULT_WATCHLIST is fully blocked, expanded_watchlist must
        exclude all 5 blocked tickers and contain only screened tickers."""
        from trading.personas import context as ctx
        from trading.personas.orchestrator import _build_micro_input

        # Today's scenario: all 5 DEFAULT_WATCHLIST tickers are blocked.
        blocked_cache_payload = {
            "date": "2026-05-11",
            "blocked": {
                "005930": {"reason": "단기과열 (stat_cls=55)", "stat_cls": "55"},
                "000660": {"reason": "단기과열 (stat_cls=55)", "stat_cls": "55"},
                "035420": {"reason": "단기과열 (stat_cls=55)", "stat_cls": "55"},
                "035720": {"reason": "단기과열 (stat_cls=55)", "stat_cls": "55"},
                "373220": {"reason": "단기과열 (stat_cls=55)", "stat_cls": "55"},
            },
            "blocked_today_by_safety": [],
        }
        # 20 screened tickers — none overlap with DEFAULT_WATCHLIST.
        screened = [
            "000270",
            "005380",
            "012330",
            "017670",
            "028260",
            "032830",
            "051910",
            "055550",
            "066570",
            "086790",
            "096770",
            "105560",
            "207940",
            "247540",
            "251270",
            "316140",
            "323410",
            "352820",
            "377300",
            "393890",
        ]

        captured: dict[str, object] = {}

        def fake_assemble(*, macro_summary, watchlist, blocked_tickers=None, **kwargs):
            captured["watchlist"] = watchlist
            captured["blocked_tickers"] = blocked_tickers
            captured["macro_summary"] = macro_summary
            return {"watchlist_for_assert": watchlist}

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
            _build_micro_input(today="2026-05-11", macro_summary="bullish")

        watchlist = captured["watchlist"]
        assert isinstance(watchlist, list)

        # None of the 5 blocked DEFAULT_WATCHLIST tickers must appear.
        blocked_codes = set(blocked_cache_payload["blocked"].keys())
        leaked = [t for t in watchlist if t in blocked_codes]
        assert leaked == [], f"blocked tickers leaked into expanded_watchlist: {leaked}"

        # At least 10 screened candidates should remain to keep universe live.
        assert (
            len(watchlist) >= 10
        ), f"expanded_watchlist too small after filtering: {watchlist!r}"

        # Every remaining ticker must originate from screened (since DEFAULT
        # is fully blocked).
        screened_set = set(screened)
        non_screened = [t for t in watchlist if t not in screened_set]
        assert (
            non_screened == []
        ), f"unexpected non-screened tickers in fallback universe: {non_screened}"

        # The blocked dict must be forwarded to assemble_micro_input so the
        # prompt layer can render the [매매제한 종목] block.
        passed = captured["blocked_tickers"]
        assert passed is not None
        # Accept either the inner dict or the full cache; require the codes.
        if isinstance(passed, dict):
            keys = set(passed.keys())
        else:
            keys = set(passed)
        assert blocked_codes.issubset(
            keys
        ), f"blocked_tickers not forwarded to assemble_micro_input: {passed!r}"

    def test_build_micro_input_no_regression_when_no_blocked(self):
        """REQ-018-1(c): When blocked is empty, the universe is the legacy
        ``DEFAULT_WATCHLIST + screened[:15]`` merge."""
        from trading.personas import context as ctx
        from trading.personas.orchestrator import _build_micro_input

        screened = [f"00{i:04d}" for i in range(20)]  # synthetic codes, no overlap

        captured: dict[str, object] = {}

        def fake_assemble(*, macro_summary, watchlist, blocked_tickers=None, **kwargs):
            captured["watchlist"] = watchlist
            captured["blocked_tickers"] = blocked_tickers
            return {}

        with (
            patch(
                "trading.personas.orchestrator.get_blocked_tickers",
                return_value={
                    "date": "2026-05-11",
                    "blocked": {},
                    "blocked_today_by_safety": [],
                },
            ),
            patch(
                "trading.personas.orchestrator.load_screened_tickers",
                return_value=screened,
            ),
            patch.object(ctx, "assemble_micro_input", side_effect=fake_assemble),
        ):
            _build_micro_input(today="2026-05-11", macro_summary=None)

        watchlist = captured["watchlist"]
        # All 5 DEFAULT_WATCHLIST tickers must be present (legacy behavior).
        for tk in ctx.DEFAULT_WATCHLIST:
            assert tk in watchlist, (
                f"backward compatibility broken: {tk} missing from "
                f"watchlist={watchlist!r}"
            )
        # blocked_tickers should be empty dict/list (key present, value falsy).
        passed = captured["blocked_tickers"]
        assert passed is not None
        assert not passed, f"expected empty blocked_tickers, got {passed!r}"


# ---------------------------------------------------------------------------
# REQ-018-2: context.assemble_micro_input includes blocked_tickers field
# ---------------------------------------------------------------------------


class TestAssembleMicroInputIncludesBlockedTickers:
    """REQ-018-2: ``assemble_micro_input`` returns a dict with
    ``blocked_tickers`` always present (default empty)."""

    def test_assemble_micro_input_includes_blocked_tickers_field(self):
        from trading.personas import context as ctx

        blocked = {
            "005930": {"reason": "단기과열 (stat_cls=55)"},
            "000660": {"reason": "단기과열 (stat_cls=55)"},
        }

        # Stub DB-facing helpers so the test stays pure (no Postgres).
        with (
            patch.object(ctx, "_technicals", return_value=None),
            patch.object(ctx, "_fundamentals", return_value=None),
            patch.object(ctx, "_flows_5d", return_value=None),
            patch.object(ctx, "_recent_disclosures", return_value=[]),
            patch.object(ctx, "_read_md", return_value="(stub)"),
            patch.object(ctx, "_load_memory", return_value=[]),
        ):
            result = ctx.assemble_micro_input(
                macro_summary="bullish",
                watchlist=["005930", "000660"],
                blocked_tickers=blocked,
            )

        assert (
            "blocked_tickers" in result
        ), f"return dict missing 'blocked_tickers' key: keys={list(result)}"
        # Value must equal the input (dict or list — exact passthrough).
        assert result["blocked_tickers"] == blocked

    def test_assemble_micro_input_default_blocked_tickers_is_empty(self):
        """REQ-018-2(c): default value must be a non-None empty container."""
        from trading.personas import context as ctx

        with (
            patch.object(ctx, "_technicals", return_value=None),
            patch.object(ctx, "_fundamentals", return_value=None),
            patch.object(ctx, "_flows_5d", return_value=None),
            patch.object(ctx, "_recent_disclosures", return_value=[]),
            patch.object(ctx, "_read_md", return_value="(stub)"),
            patch.object(ctx, "_load_memory", return_value=[]),
        ):
            result = ctx.assemble_micro_input(
                macro_summary=None,
                watchlist=["005930"],
            )

        assert "blocked_tickers" in result
        assert result["blocked_tickers"] is not None
        assert not result[
            "blocked_tickers"
        ], f"default blocked_tickers must be falsy/empty, got {result['blocked_tickers']!r}"


# ---------------------------------------------------------------------------
# REQ-018-3: micro.jinja renders the [매매제한 종목] block
# ---------------------------------------------------------------------------


@pytest.fixture
def jinja_env() -> Environment:
    """Load Jinja env rooted at the persona prompts directory."""
    prompts_dir = (
        Path(__file__).resolve().parent.parent.parent
        / "src"
        / "trading"
        / "personas"
        / "prompts"
    )
    return Environment(loader=FileSystemLoader(str(prompts_dir)))


def _base_micro_context(**overrides):
    """Minimum context required by micro.jinja for a clean render."""
    ctx = {
        "today": "2026-05-11",
        "macro_summary": "(없음)",
        "universe_snapshot": "- 005930 삼성전자: stub",
        "recent_disclosures": [],
        "user_watchlist": "",
        "static_context": "",
        "static_news": "",
        "memory": "(활성 메모리 없음)",
        "blocked_tickers": {},
    }
    ctx.update(overrides)
    return ctx


class TestMicroJinjaRendersBlockedBlock:
    """REQ-018-3: micro.jinja exposes a [매매제한 종목] block guarded by
    ``{% if blocked_tickers %}``."""

    def test_micro_prompt_renders_blocked_block(self, jinja_env):
        template = jinja_env.get_template("micro.jinja")
        blocked = {
            "005930": {"reason": "단기과열 (stat_cls=55)"},
            "000660": {"reason": "단기과열 (stat_cls=55)"},
        }
        rendered = template.render(**_base_micro_context(blocked_tickers=blocked))

        # Header must appear as a distinct section.
        assert (
            "매매제한 종목" in rendered
        ), "expected '매매제한 종목' header missing from rendered prompt"
        # Each blocked ticker code must be listed.
        for code in blocked:
            assert (
                code in rendered
            ), f"blocked ticker {code} missing from rendered prompt"
        # The prompt must explicitly instruct exclusion.
        assert (
            "후보에서" in rendered
        ), "expected '후보에서' in rendered prompt (explicit exclusion instruction)"
        assert (
            "제외" in rendered
        ), "expected '제외' in rendered prompt (explicit exclusion instruction)"

    def test_micro_prompt_omits_block_when_no_blocked(self, jinja_env):
        """REQ-018-3(c): no block rendered when blocked_tickers is empty."""
        template = jinja_env.get_template("micro.jinja")
        rendered = template.render(**_base_micro_context(blocked_tickers={}))

        assert (
            "매매제한 종목" not in rendered
        ), "blocked-tickers block must not render when blocked_tickers is empty"


# ---------------------------------------------------------------------------
# REQ-018-4: Screened fallback when DEFAULT_WATCHLIST is fully blocked
# ---------------------------------------------------------------------------


class TestScreenedFallbackWhenDefaultFullyBlocked:
    """REQ-018-4: If DEFAULT_WATCHLIST is entirely blocked and screened
    tickers are available, the watchlist falls back to screened-only."""

    def test_screened_fallback_when_default_fully_blocked(self):
        from trading.personas import context as ctx
        from trading.personas.orchestrator import _build_micro_input

        # Block every DEFAULT_WATCHLIST ticker.
        blocked_cache_payload = {
            "date": "2026-05-11",
            "blocked": {tk: {"reason": "단기과열"} for tk in ctx.DEFAULT_WATCHLIST},
            "blocked_today_by_safety": [],
        }
        # Provide screened tickers (none overlap with DEFAULT_WATCHLIST).
        screened = [
            "000270",
            "005380",
            "012330",
            "017670",
            "028260",
            "032830",
            "051910",
            "055550",
            "066570",
            "086790",
        ]

        captured: dict[str, object] = {}

        def fake_assemble(*, macro_summary, watchlist, blocked_tickers=None, **kwargs):
            captured["watchlist"] = watchlist
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
            _build_micro_input(today="2026-05-11", macro_summary=None)

        watchlist = captured["watchlist"]
        # No DEFAULT_WATCHLIST ticker may survive.
        for tk in ctx.DEFAULT_WATCHLIST:
            assert (
                tk not in watchlist
            ), f"fully-blocked ticker {tk} leaked into fallback watchlist"
        # Universe must remain populated from screened.
        assert len(watchlist) > 0, "fallback watchlist must not be empty"
        assert all(
            tk in screened for tk in watchlist
        ), f"fallback watchlist must come from screened set: {watchlist!r}"
