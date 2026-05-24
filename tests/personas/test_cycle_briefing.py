"""SPEC-TRADING-027 — consolidated per-cycle decision-chain briefing.

The bot used to send 4 fragmented terse persona briefings per cycle (and
"신규 시그널 없음" with no reason). This consolidates each cycle into ONE
"사이클 요약" that shows the chain Macro -> Micro -> Decision -> Risk -> outcome
WITH the reasoning the personas already produce (decision.summary,
micro.summary, risk.rationale). No-trade cycles are compressed; per-persona
detail is opt-in via verbose mode.
"""

from __future__ import annotations

from unittest.mock import patch

from trading.personas import orchestrator as orch

# ---------------------------------------------------------------------------
# 제안1 — _summarize_persona surfaces the WHY (even with no signal)
# ---------------------------------------------------------------------------

class TestSummarizeSurfacesWhy:
    def test_decision_no_signal_includes_summary(self):
        out = orch._summarize_persona(
            "decision",
            {"signals": [], "summary": "과열 우세 + 외인 순매도로 신규 진입 보류"},
        )
        assert "신규 시그널 없음" in out
        assert "외인 순매도" in out  # the WHY is surfaced

    def test_decision_no_signal_no_summary_graceful(self):
        out = orch._summarize_persona("decision", {"signals": []})
        assert "신규 시그널 없음" in out

    def test_micro_includes_tone_summary(self):
        out = orch._summarize_persona(
            "micro",
            {"candidates": {"buy": [], "sell": [], "hold": [{"ticker": "005930"}]},
             "summary": "반도체 강세, 방어적 접근"},
        )
        assert "매수 0" in out
        assert "반도체 강세" in out


# ---------------------------------------------------------------------------
# 제안2 — _build_cycle_chain consolidates the chain + compresses no-trade
# ---------------------------------------------------------------------------

class TestBuildCycleChain:
    def test_no_trade_is_compact_with_reason(self):
        chain = orch._build_cycle_chain(
            cycle_kind="intraday",
            macro_json={"regime": "bull", "risk_appetite": "neutral"},
            micro_json={"candidates": {"buy": [], "sell": [], "hold": []},
                        "summary": "관망 우세"},
            decision_json={"signals": [], "summary": "과열 우세로 보류"},
            risk_json=None,
            executed=0,
            rejected=0,
        )
        assert "과열 우세로 보류" in chain        # the WHY
        assert "신규 시그널 없음" in chain or "관망" in chain
        # compact: a no-trade cycle should be short
        assert chain.count("\n") <= 4

    def test_trade_chain_shows_full_path(self):
        chain = orch._build_cycle_chain(
            cycle_kind="pre_market",
            macro_json={"regime": "bull", "risk_appetite": "risk_on"},
            micro_json={"candidates": {"buy": [{"ticker": "005930"}], "sell": [], "hold": []},
                        "summary": "반도체 매수 우위"},
            decision_json={"signals": [{"ticker": "005930", "side": "buy", "qty": 5,
                                        "rationale": "외인 매수 + 한도 내"}],
                           "summary": "삼성전자 분할 매수"},
            risk_json={"verdict": "APPROVE", "rationale": "한도 내 정상"},
            executed=1,
            rejected=0,
        )
        assert "005930" in chain
        assert "APPROVE" in chain
        assert "외인 매수" in chain or "삼성전자 분할 매수" in chain
        assert "체결" in chain  # outcome

    def test_missing_personas_graceful(self):
        chain = orch._build_cycle_chain(
            cycle_kind="event",
            macro_json=None, micro_json=None,
            decision_json={"signals": [], "summary": "데이터 부족"},
            risk_json=None, executed=0, rejected=0,
        )
        assert "데이터 부족" in chain


# ---------------------------------------------------------------------------
# 제안3 — verbose gate: per-persona briefings only in verbose mode
# ---------------------------------------------------------------------------

class TestVerboseGate:
    def test_persona_briefing_suppressed_in_concise(self):
        from trading.alerts import telegram as tg

        with patch.object(tg, "_verbose_briefing_active", return_value=False), \
             patch.object(tg, "_send_raw") as send:
            tg.persona_briefing("Decision", "model", "summary")
        send.assert_not_called()

    def test_persona_briefing_sent_in_verbose(self):
        from trading.alerts import telegram as tg

        with patch.object(tg, "_verbose_briefing_active", return_value=True), \
             patch.object(tg, "_send_raw") as send:
            tg.persona_briefing("Decision", "model", "summary")
        send.assert_called_once()

    def test_cycle_briefing_suppressed_when_silent(self):
        from trading.alerts import telegram as tg

        with patch.object(tg, "_briefing_silent", return_value=True), \
             patch.object(tg, "_send_raw") as send:
            tg.cycle_briefing("intraday", "chain text")
        send.assert_not_called()

    def test_cycle_briefing_sent_when_not_silent(self):
        from trading.alerts import telegram as tg

        with patch.object(tg, "_briefing_silent", return_value=False), \
             patch.object(tg, "_send_raw") as send:
            tg.cycle_briefing("intraday", "chain text")
        send.assert_called_once()
