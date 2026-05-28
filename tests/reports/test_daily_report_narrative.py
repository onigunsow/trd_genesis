"""SPEC-TRADING-030 — daily-report qualitative narrative review.

Adds a 3-section narrative (매크로 총평 / 마이크로 총평 / 보유자산 리뷰 + 종합)
on top of the existing operational-metrics block, generated through the zero-cost
CLI subscription path (``call_persona_via_cli``) so it works UNDER cli_only_mode.

All external dependencies (intelligence_*.md file reads, ``account.balance``,
``call_persona_via_cli``, DB ``connection``, telegram) are mocked. methodology: TDD.

Covers REQ-030-1 .. REQ-030-9 / AC-1 .. AC-9.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from trading.reports import daily_report as dr

# --------------------------------------------------------------------------- #
# Fixtures: representative intelligence_*.md content                          #
# --------------------------------------------------------------------------- #

MACRO_MD = """# Intelligence Report (Macro) — 2026-05-28
_Generated: 2026-05-28 14:45:10 KST | Source: News Intelligence Pipeline_

## Market-Moving Events (3 stories)

### [투자 주목] 코스피 8000 붕괴 [fn오후시황] (Impact: 5/5)
_4 sources | 2026-05-27 | Keywords: 코스피, 반도체, 양극화_
→ 현금 비중 확대와 방어주 비중 확대를 우선한다.

### [투자 주목] 금통위 기준금리 동결 (Impact: 3/5)
_2 sources | 2026-05-28 | Keywords: 기준금리, 금통위_
→ 금리 인상 시그널 시 은행주 비중 확대.

### [투자 주목] 환율 소폭 변동 (Impact: 1/5)
_1 sources | 2026-05-28 | Keywords: 환율_
→ 환헤지 점검.
"""

# A header whose title carries embedded text but still ends with (Impact: N/5),
# to ensure we match the LAST (Impact: N/5) on the header line.
MACRO_MD_TRICKY_HEADER = """## Market-Moving Events

### [투자 주목] 한은 금통위 "물가 점검(Impact: 미정) 후 인상 결정"(종합) (Impact: 4/5)
_1 sources | 2026-05-28 | Keywords: 금리, 긴축_
→ 긴축 압력 본격화.
"""


def _micro_md(n_stories: int) -> str:
    """Build a micro intelligence doc with n_stories descending Impact."""
    head = "# Intelligence Report (Micro)\n\n## Market-Moving Events\n\n"
    blocks = []
    for i in range(n_stories):
        impact = 5 - (i % 5)  # cycle 5,4,3,2,1,5,...
        blocks.append(
            f"### [투자 주목] 종목 이벤트 {i} (Impact: {impact}/5)\n"
            f"_1 sources | 2026-05-28 | Keywords: kw{i}_\n"
            f"→ 대응전략 {i}.\n"
        )
    return head + "\n".join(blocks)


def _base_metrics() -> dict:
    """Minimal operational-metrics dict (existing _gather_today keys)."""
    return {
        "today": "2026-05-28",
        "orders": [],
        "runs": [],
        "risk": [],
        "cost": {"executed_count": 0, "exec_fee_total": 0, "attempted_fee_total": 0},
        "cumulative": {"week_orders": 0, "month_orders": 0, "week_fee": 0, "month_fee": 0},
        "tool_stats": {"total_calls": 0, "failures": 0, "persona_invocations": 0},
        "reflection_stats": {"total_rounds": 0, "approved": 0, "rejected": 0, "withdrawn": 0},
        "model_breakdown": [],
        "auto_expansion_tickers": [],
    }


def _wire_conn(conn_mock, cur):
    """Wire a patched ``connection()`` mock so ``with connection() as conn,
    conn.cursor() as cur`` yields ``cur``."""
    enter = conn_mock.return_value.__enter__.return_value
    enter.cursor.return_value.__enter__.return_value = cur


def _balance_payload() -> dict:
    return {
        "cash_d2": 8_787_740,
        "buyable": 8_000_000,
        "buyable_effective": 8_000_000,
        "nrcvb_buy_amt": 0,
        "total_assets": 11_916_140,
        "stock_eval": 3_128_400,
        "invest_basis": 11_916_140,
        "pnl_total": 128_400,
        "holdings": [
            {"ticker": "005930", "name": "삼성전자", "qty": 10, "avg_cost": 70_000,
             "current_price": 80_000, "eval_amount": 800_000,
             "pnl_amount": 100_000, "pnl_pct": 14.28},
            {"ticker": "000660", "name": "SK하이닉스", "qty": 5, "avg_cost": 200_000,
             "current_price": 195_000, "eval_amount": 975_000,
             "pnl_amount": -25_000, "pnl_pct": -2.5},
        ],
    }


# --------------------------------------------------------------------------- #
# AC-1 — intelligence digest (REQ-030-1)                                      #
# --------------------------------------------------------------------------- #

class TestIntelligenceDigest:
    def test_parse_stories_sorted_by_impact_desc(self):
        stories = dr._parse_intel_stories(MACRO_MD)
        impacts = [s["impact"] for s in stories]
        assert impacts == sorted(impacts, reverse=True)
        assert impacts[0] == 5

    def test_story_fields_preserved(self):
        stories = dr._parse_intel_stories(MACRO_MD)
        top = stories[0]
        assert "코스피 8000 붕괴" in top["title"]
        assert top["impact"] == 5
        assert "코스피" in top["keywords"]
        assert "방어주" in top["strategy"]

    def test_tricky_header_matches_last_impact(self):
        stories = dr._parse_intel_stories(MACRO_MD_TRICKY_HEADER)
        assert len(stories) == 1
        # The (Impact: 미정) embedded in the title must NOT be picked; the real
        # trailing (Impact: 4/5) must be.
        assert stories[0]["impact"] == 4
        assert "물가 점검(Impact: 미정)" in stories[0]["title"]

    def test_micro_top_n_truncation_marker(self):
        # 20 stories, N_MICRO default => truncated, marker present.
        md = _micro_md(20)
        stories, marker = dr._intel_digest_stories(md, top_n=dr.N_MICRO)
        assert len(stories) == dr.N_MICRO
        assert marker  # non-empty truncation marker
        assert "생략" in marker
        # Marker reports the omitted count (20 - N_MICRO).
        assert str(20 - dr.N_MICRO) in marker

    def test_micro_no_truncation_when_under_cap(self):
        md = _micro_md(3)
        stories, marker = dr._intel_digest_stories(md, top_n=dr.N_MICRO)
        assert len(stories) == 3
        assert marker == ""

    def test_gather_today_has_intelligence_key(self):
        with patch.object(dr, "connection") as conn, \
             patch.object(dr, "_read_context_md") as read_md, \
             patch.object(dr, "_collect_portfolio", return_value={"status": "ok", "holdings": []}):
            cur = MagicMock()
            cur.fetchall.return_value = []
            cur.fetchone.return_value = {}
            _wire_conn(conn, cur)
            read_md.side_effect = lambda name: MACRO_MD if "macro" in name else _micro_md(15)
            data = dr._gather_today()
        assert "intelligence" in data
        intel = data["intelligence"]
        assert "macro" in intel
        assert "micro" in intel
        assert intel["macro"]["status"] == "ok"
        assert len(intel["macro"]["stories"]) >= 1
        assert len(intel["micro"]["stories"]) == dr.N_MICRO


# --------------------------------------------------------------------------- #
# AC-2 / AC-9b / AC-9c — portfolio collection (REQ-030-2)                      #
# --------------------------------------------------------------------------- #

class TestPortfolioCollection:
    def test_collect_portfolio_success(self):
        with patch("trading.reports.daily_report.KisClient") as Client, \
             patch("trading.reports.daily_report.balance", return_value=_balance_payload()):
            Client.return_value = MagicMock()
            port = dr._collect_portfolio()
        assert port["status"] == "ok"
        assert len(port["holdings"]) == 2
        assert port["holdings"][0]["ticker"] == "005930"
        for key in ("total_assets", "cash_d2", "stock_eval", "invest_basis", "pnl_total"):
            assert key in port

    def test_collect_portfolio_balance_failure_is_placeholder(self):
        from trading.kis.client import KisError

        err = MagicMock()
        with patch("trading.reports.daily_report.KisClient", return_value=MagicMock()), \
             patch("trading.reports.daily_report.balance", side_effect=KisError(err)):
            port = dr._collect_portfolio()  # must NOT raise
        assert port["status"] == "error"
        assert port["holdings"] == []

    def test_collect_portfolio_empty_holdings(self):
        payload = _balance_payload()
        payload["holdings"] = []
        with patch("trading.reports.daily_report.KisClient", return_value=MagicMock()), \
             patch("trading.reports.daily_report.balance", return_value=payload):
            port = dr._collect_portfolio()
        assert port["status"] == "ok"
        assert port["holdings"] == []

    def test_gather_today_preserves_existing_keys(self):
        with patch.object(dr, "connection") as conn, \
             patch.object(dr, "_read_context_md", return_value=MACRO_MD), \
             patch.object(dr, "_collect_portfolio", return_value={"status": "ok", "holdings": []}):
            cur = MagicMock()
            cur.fetchall.return_value = []
            cur.fetchone.return_value = {}
            _wire_conn(conn, cur)
            data = dr._gather_today()
        for key in ("today", "orders", "runs", "risk", "cost", "cumulative",
                    "tool_stats", "reflection_stats", "model_breakdown",
                    "auto_expansion_tickers", "intelligence", "portfolio"):
            assert key in data


# --------------------------------------------------------------------------- #
# AC-3 — CLI subscription path (REQ-030-3)                                     #
# --------------------------------------------------------------------------- #

class TestNarrativeCliCall:
    def _data_with_extras(self) -> dict:
        d = _base_metrics()
        macro_story = {"title": "코스피 급락", "impact": 5,
                       "keywords": "코스피", "strategy": "현금 확대"}
        micro_story = {"title": "종목 X", "impact": 4, "keywords": "kw", "strategy": "관망"}
        d["intelligence"] = {
            "macro": {"status": "ok", "stories": [macro_story], "marker": ""},
            "micro": {"status": "ok", "stories": [micro_story], "marker": ""},
        }
        bal = _balance_payload()
        summary_keys = ("total_assets", "cash_d2", "stock_eval", "invest_basis", "pnl_total")
        d["portfolio"] = {
            "status": "ok",
            "holdings": bal["holdings"],
            **{k: bal[k] for k in summary_keys},
        }
        return d

    def test_returns_response_text(self):
        from trading.personas.base import PersonaResult

        fake = PersonaResult(
            persona_run_id=1, response_text="## 매크로 총평\n...\n## 종합\n...",
            response_json=None, input_tokens=0, output_tokens=0, cost_krw=0.0,
            latency_ms=10, tool_calls_count=0, tool_input_tokens=0, tool_output_tokens=0,
        )
        with patch("trading.reports.daily_report.call_persona_via_cli", return_value=fake) as cpc:
            out = dr._narrative_text(self._data_with_extras())
        assert out == "## 매크로 총평\n...\n## 종합\n..."
        cpc.assert_called_once()

    def test_call_kwargs(self):
        from trading.personas.base import PersonaResult

        fake = PersonaResult(
            persona_run_id=1, response_text="prose", response_json=None,
            input_tokens=0, output_tokens=0, cost_krw=0.0, latency_ms=1,
            tool_calls_count=0, tool_input_tokens=0, tool_output_tokens=0,
        )
        with patch("trading.reports.daily_report.call_persona_via_cli", return_value=fake) as cpc:
            dr._narrative_text(self._data_with_extras())
        _, kwargs = cpc.call_args
        assert kwargs["persona_name"] == "daily_report"
        assert kwargs["model"] == "cli-claude-max"
        assert kwargs["expect_json"] is False
        assert kwargs["apply_memory_ops"] is False
        assert isinstance(kwargs["system_prompt"], str)
        assert kwargs["system_prompt"]
        assert isinstance(kwargs["user_message"], str)
        assert kwargs["user_message"]


# --------------------------------------------------------------------------- #
# AC-4 — 3-section prompt with guardrails (REQ-030-4)                          #
# --------------------------------------------------------------------------- #

class TestNarrativePrompt:
    def test_system_prompt_sections_and_guardrails(self):
        from trading.personas.base import PersonaResult

        captured = {}

        def _capture(**kwargs):
            captured.update(kwargs)
            return PersonaResult(
                persona_run_id=1, response_text="x", response_json=None,
                input_tokens=0, output_tokens=0, cost_krw=0.0, latency_ms=1,
                tool_calls_count=0, tool_input_tokens=0, tool_output_tokens=0)

        with patch("trading.reports.daily_report.call_persona_via_cli", side_effect=_capture):
            dr._narrative_text(TestNarrativeCliCall()._data_with_extras())

        sp = captured["system_prompt"]
        # 3 sections + synthesis
        assert "매크로" in sp
        assert "마이크로" in sp
        assert "보유자산" in sp
        assert "종합" in sp
        # guardrails reused from _llm_text
        assert "환각" in sp
        assert "KRW" in sp or "원화" in sp
        assert "₩" in sp or "원" in sp
        assert "$" in sp  # the "USD($) 금지" guardrail
        # Neutralise build_cli_prompt's trailing JSON-only footer
        assert "JSON" in sp


# --------------------------------------------------------------------------- #
# AC-5 — output composition order (REQ-030-5)                                  #
# --------------------------------------------------------------------------- #

class TestComposition:
    def _run_generate(self, narrative_return=None, narrative_exc=None):
        data = _base_metrics()
        data["intelligence"] = {"macro": {"status": "ok", "stories": [], "marker": ""},
                                "micro": {"status": "ok", "stories": [], "marker": ""}}
        data["portfolio"] = {"status": "ok", "holdings": []}

        cur = MagicMock()
        with patch.object(dr, "_gather_today", return_value=data), \
             patch.object(dr, "connection") as conn, \
             patch.object(dr, "system_briefing") as brief:
            _wire_conn(conn, cur)
            if narrative_exc is not None:
                with patch.object(dr, "_narrative_text", side_effect=narrative_exc):
                    text = dr.generate_and_send()
            else:
                with patch.object(dr, "_narrative_text", return_value=narrative_return):
                    text = dr.generate_and_send()
        return text, cur, brief

    def test_narrative_on_top_metrics_below(self):
        narrative = "## 매크로 총평\n오늘 시장은 하락했다.\n## 종합\n방어적 기조."
        text, cur, brief = self._run_generate(narrative_return=narrative)
        # Narrative present and ABOVE the metrics block.
        assert narrative in text
        assert "[일일 리포트" in text  # metrics block header from _fallback_text
        assert text.index("매크로 총평") < text.index("[일일 리포트")
        # DB UPSERT + telegram still happen.
        assert cur.execute.called
        brief.assert_called_once()

    def test_narrative_success_does_not_show_skip_line(self):
        """When the narrative succeeds, the metrics block must NOT falsely print
        '(LLM 요약 생략)' — that line belongs only to the degrade path."""
        narrative = "## 매크로 총평\n시황 코멘트.\n## 종합\n중립."
        text, _, _ = self._run_generate(narrative_return=narrative)
        assert "생략" not in text
        assert "LLM 요약" not in text


# --------------------------------------------------------------------------- #
# _fallback_text skip-reason line conditionality                              #
# --------------------------------------------------------------------------- #

class TestFallbackSkipLine:
    def test_no_skip_line_when_reason_absent(self):
        # Used as the metrics block under a successful narrative: no skip line.
        text = dr._fallback_text(_base_metrics())
        assert "생략" not in text
        assert "LLM 요약" not in text

    def test_skip_line_present_when_reason_given(self):
        # Degrade path: the human-readable reason must still be shown.
        reason = "CLI 전용 모드 — 직접 API 호출 차단(정상), 결정형 요약 사용"
        text = dr._fallback_text(_base_metrics(), skip_reason=reason)
        assert reason in text


# --------------------------------------------------------------------------- #
# AC-6 / AC-9e — graceful degrade (REQ-030-6)                                  #
# --------------------------------------------------------------------------- #

class TestGracefulDegrade(TestComposition):
    def test_double_failure_degrades_to_fallback(self):
        exc = RuntimeError("Double failure for daily_report: CLI (timeout) + Haiku (429)")
        text, cur, brief = self._run_generate(narrative_exc=exc)
        # No exception propagated; body is metrics-only fallback.
        assert "[일일 리포트" in text
        assert "매크로 총평" not in text
        # skip_reason rendered (human-readable).
        assert "실패" in text or "생략" in text or "차단" in text
        # cron continues: DB + telegram still fired.
        assert cur.execute.called
        brief.assert_called_once()

    def test_skip_reason_message_present(self):
        text, _, _ = self._run_generate(
            narrative_exc=RuntimeError("Double failure for daily_report: CLI + Haiku both failed")
        )
        # _llm_skip_reason generic branch
        assert "실패" in text


# --------------------------------------------------------------------------- #
# AC-9a — intelligence missing/stale placeholder (REQ-030-9a)                  #
# --------------------------------------------------------------------------- #

class TestIntelligenceDegrade:
    def test_missing_file_placeholder(self):
        # _read_context_md returns the personas/context placeholder string for missing files.
        placeholder = "_(intelligence_macro.md 미생성 — cron 미실행 또는 첫 운영)_"
        with patch.object(dr, "connection") as conn, \
             patch.object(dr, "_read_context_md", return_value=placeholder), \
             patch.object(dr, "_collect_portfolio", return_value={"status": "ok", "holdings": []}):
            cur = MagicMock()
            cur.fetchall.return_value = []
            cur.fetchone.return_value = {}
            _wire_conn(conn, cur)
            data = dr._gather_today()
        assert data["intelligence"]["macro"]["status"] == "missing"
        assert data["intelligence"]["macro"]["stories"] == []

    def test_zero_stories_status(self):
        md = "# Intelligence\n\n## Market-Moving Events\n\n(no stories)\n"
        stories, marker = dr._intel_digest_stories(md, top_n=dr.N_MACRO)
        assert stories == []
        assert marker == ""


# --------------------------------------------------------------------------- #
# _read_context_md — read-only reuse (REQ-030-8)                              #
# --------------------------------------------------------------------------- #

class TestReadContextMd:
    def test_missing_file_returns_placeholder(self, tmp_path):
        with patch.object(dr, "project_root", return_value=tmp_path):
            out = dr._read_context_md("intelligence_macro.md")
        assert "미생성" in out

    def test_existing_file_read(self, tmp_path):
        (tmp_path / "data" / "contexts").mkdir(parents=True)
        (tmp_path / "data" / "contexts" / "intelligence_macro.md").write_text(
            MACRO_MD, encoding="utf-8")
        with patch.object(dr, "project_root", return_value=tmp_path):
            out = dr._read_context_md("intelligence_macro.md")
        assert "코스피 8000 붕괴" in out

    def test_gather_intelligence_endtoend(self, tmp_path):
        d = tmp_path / "data" / "contexts"
        d.mkdir(parents=True)
        (d / "intelligence_macro.md").write_text(MACRO_MD, encoding="utf-8")
        (d / "intelligence_micro.md").write_text(_micro_md(20), encoding="utf-8")
        with patch.object(dr, "project_root", return_value=tmp_path):
            intel = dr._gather_intelligence()
        assert intel["macro"]["status"] == "ok"
        assert intel["micro"]["status"] == "ok"
        assert len(intel["micro"]["stories"]) == dr.N_MICRO
        assert "생략" in intel["micro"]["marker"]


# --------------------------------------------------------------------------- #
# AC-7 / AC-8 — invariants (REQ-030-7/8)                                       #
# --------------------------------------------------------------------------- #

class TestInvariants:
    def test_no_new_direct_anthropic_call_in_generate_path(self):
        """REQ-030-7: generate_and_send must NOT call Anthropic().messages.create."""
        import inspect

        # The daily-report generate path must route only through the CLI bridge.
        src = inspect.getsource(dr.generate_and_send) + inspect.getsource(dr._narrative_text)
        assert "messages.create" not in src
        assert "Anthropic(" not in src

    def test_narrative_path_does_not_write_intelligence_files(self):
        """REQ-030-8: read-only reuse of intelligence_*.md (no write)."""
        from trading.personas.base import PersonaResult

        fake = PersonaResult(
            persona_run_id=1, response_text="prose", response_json=None,
            input_tokens=0, output_tokens=0, cost_krw=0.0, latency_ms=1,
            tool_calls_count=0, tool_input_tokens=0, tool_output_tokens=0)

        opened_for_write = []
        real_open = open

        def _tracking_open(file, mode="r", *args, **kwargs):
            if "w" in mode or "a" in mode:
                opened_for_write.append(str(file))
            return real_open(file, mode, *args, **kwargs)

        data = TestNarrativeCliCall()._data_with_extras()
        with patch("trading.reports.daily_report.call_persona_via_cli", return_value=fake), \
             patch("builtins.open", side_effect=_tracking_open):
            dr._narrative_text(data)
        assert not any("intelligence_" in f for f in opened_for_write)
