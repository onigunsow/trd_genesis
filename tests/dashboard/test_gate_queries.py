"""SPEC-TRADING-065 그룹 3·4 — 게이트 뷰 쿼리의 순수 로직 + 라우트 배선.

DB 집계 SQL 은 라이브 실측으로 검증했다(2026-08-15 세션 실측 재현 확인):
  진입 품질  conf 0.4 +7.92% / 0.6 -3.67%   (AC-3)
  HOLD 사유  단기과열 67/82 = 82%            (AC-4)
  보유기간   2~15일 -35만 / 16~30일 +5만
  사이징     portfolio_persona 평균 삭감 49%
여기서는 그 SQL 결과를 화면용으로 접는 순수 함수와 계약만 고정한다.
"""

from __future__ import annotations

from datetime import date
from unittest.mock import MagicMock, patch

from trading.dashboard import gate_queries as gq


class TestHoldingBuckets:
    def _rt(self, days, pnl, ret):
        from trading.edge.roundtrips import RoundTrip
        d0 = date(2026, 8, 1)
        from datetime import timedelta
        return RoundTrip(ticker="A", entry_date=d0, exit_date=d0 + timedelta(days=days), qty=1,
                         entry_price=100, exit_price=100 * (1 + ret / 100),
                         entry_fee=0, exit_fee=0, confidence=None, verdict=None)

    def test_buckets_cover_all_and_are_disjoint(self):
        labels = [b[0] for b in gq.HOLDING_BUCKETS]
        assert labels == ["0-1일", "2-5일", "6-15일", "16-30일", "31일+"]
        prev_hi = -1
        for _label, lo, hi in gq.HOLDING_BUCKETS:
            assert lo == prev_hi + 1, (
                "구간 사이에 빈틈/겹침이 있으면 왕복이 새거나 이중 계상된다"
            )
            prev_hi = hi

    def test_aggregation_matches_live_shape(self):
        from trading.edge.roundtrips import RoundTripResult
        rts = [self._rt(3, -1, -4.0), self._rt(4, -1, -5.0), self._rt(20, +1, +3.0)]
        with patch.object(gq, "_cache_get", return_value=None), \
             patch("trading.edge.roundtrips.compute_roundtrips",
                   return_value=RoundTripResult(roundtrips=rts)):
            out = gq.fetch_holding_period_pnl()
        by = {b["bucket"]: b for b in out["buckets"]}
        assert by["2-5일"]["n"] == 2
        assert by["2-5일"]["win_rate"] == 0.0
        assert by["16-30일"]["n"] == 1
        assert by["16-30일"]["win_rate"] == 1.0
        assert by["0-1일"]["n"] == 0
        assert by["0-1일"]["win_rate"] is None
        assert out["n_total"] == 3

    def test_since_is_entry_date_based(self):
        """since 는 filter_since 를 타야 한다(ts 컷 금지) — 호출만 확인."""
        from trading.edge.roundtrips import RoundTripResult
        with patch.object(gq, "_cache_get", return_value=None), \
             patch("trading.edge.roundtrips.compute_roundtrips", return_value=RoundTripResult()), \
             patch("trading.edge.roundtrips.filter_since", wraps=lambda r, s: r) as fs:
            gq.fetch_holding_period_pnl(since="2026-08-17")
        assert fs.call_args.args[1] == date(2026, 8, 17)


class TestHoldReasonClassifier:
    def test_overheat_first(self):
        assert gq._classify_hold_reason("한도 여유… 다만 단기과열 단일가매매 지정") == "단기과열"

    def test_new_discretionary_reasons(self):
        assert gq._classify_hold_reason("entry_freshness: late — 늦은 진입") == "늦은 진입"
        assert gq._classify_hold_reason("20일을 버틸 근거가 없다") == "20일 근거 부재"
        assert gq._classify_hold_reason("최근 손실 청산 종목 재진입") == "손실 재진입"

    def test_unknown_is_other(self):
        assert gq._classify_hold_reason("") == "기타"
        assert gq._classify_hold_reason("그냥 마음에 안 듦") == "기타"

    def test_keyword_table_mirrors_risk_prompt(self):
        """risk.jinja 의 재량 사유 3종이 분류표에 있어야 8/17 이후 변화가 읽힌다."""
        labels = {k for k, _ in gq.HOLD_REASON_KEYWORDS}
        assert {"늦은 진입", "20일 근거 부재", "손실 재진입", "단기과열"} <= labels


def _fake_conn(rows):
    cur = MagicMock()
    cur.fetchall.return_value = rows
    cur.__enter__ = lambda s: s
    cur.__exit__ = lambda s, *a: None
    conn = MagicMock()
    conn.cursor.return_value = cur
    conn.__enter__ = lambda s: s
    conn.__exit__ = lambda s, *a: None
    return conn


class TestSizingGateParsing:
    def _run(self, rows, **kw):
        with (
            patch.object(gq, "_cache_get", return_value=None),
            patch.object(gq, "ro_connection", return_value=_fake_conn(rows)),
        ):
            return gq.fetch_sizing_gates(**kw)

    def test_portfolio_adjustment_with_and_without_rationale(self):
        """AC-5: 8/14 이전 건은 rationale 없음, 이후 건은 채워짐 — 둘 다 표에 남는다."""
        from datetime import datetime
        rows = [
            {"ts": datetime(2026, 8, 14), "event_type": "PORTFOLIO_ADJUSTMENT",
             "details": {"adjusted": [{"ticker": "316140", "qty_original": 6,
                                       "qty_adjusted": 3, "decision_id": 3036}]}},
            {"ts": datetime(2026, 8, 18), "event_type": "PORTFOLIO_ADJUSTMENT",
             "details": {"adjusted": [{"ticker": "316140", "qty_original": 6,
                                       "qty_adjusted": 3, "decision_id": 4001,
                                       "gate": "portfolio_persona",
                                       "rationale": "물타기 형태 — 절반만"}]}},
        ]
        out = self._run(rows)
        g = {x["gate"]: x for x in out["gates"]}
        assert g["portfolio_persona"]["n"] == 2
        assert round(g["portfolio_persona"]["avg_cut_pct"]) == 50
        reasons = [r["reason"] for r in out["recent"]]
        assert None in reasons
        assert "물타기 형태 — 절반만" in reasons

    def test_gate_drop_and_limit_breach_and_session(self):
        from datetime import datetime
        rows = [
            {"ts": datetime(2026, 8, 18), "event_type": "PORTFOLIO_GATE_DROP",
             "details": {"gate": "sector_cap",
                         "dropped": [{"ticker": "316140", "reason": "금융 42% > 40%",
                                      "decision_id": 1}]}},
            {"ts": datetime(2026, 8, 18), "event_type": "PORTFOLIO_GATE_DROP",
             "details": {"gate": "cash_floor", "reason": "cash 1.0% < floor 30%",
                         "dropped": [{"decision_id": 2}]}},
            {"ts": datetime(2026, 8, 18), "event_type": "LIMIT_BREACH",
             "details": {"breaches": ["reentry_cooldown: 064350 손실 청산 3일 전",
                                      "avg_down: 064350 …"],
                         "context": {"signal": {"ticker": "064350", "qty": 2}},
                         "decision_id": 3}},
            {"ts": datetime(2026, 8, 18), "event_type": "ORDER_BLOCKED_OUTSIDE_SESSION",
             "details": {"ticker": "096770", "qty": 1, "reason": "정규장 밖", "decision_id": 4}},
        ]
        out = self._run(rows)
        g = {x["gate"]: x["n"] for x in out["gates"]}
        assert g == {"sector_cap": 1, "cash_floor": 1, "reentry_cooldown": 1,
                     "avg_down": 1, "session": 1}
        by_gate = {r["gate"]: r for r in out["recent"]}
        assert by_gate["cash_floor"]["reason"] == "cash 1.0% < floor 30%"  # 이벤트 상위 reason 폴백
        assert by_gate["reentry_cooldown"]["ticker"] == "064350"

    def test_top_n_limits_recent_only(self):
        from datetime import datetime
        rows = [{"ts": datetime(2026, 8, 18), "event_type": "ORDER_BLOCKED_OUTSIDE_SESSION",
                 "details": {"ticker": "A", "qty": 1}} for _ in range(30)]
        out = self._run(rows, top_n=5)
        assert len(out["recent"]) == 5
        assert out["gates"][0]["n"] == 30  # 집계는 잘리지 않는다


class TestTraceSlim:
    def test_keeps_only_own_signal_and_summary(self):
        from trading.dashboard.queries import _slim_trace_decision
        d = {"ticker": "316140", "side": "buy", "response_json": {
            "signals": [{"ticker": "096770", "side": "buy", "rationale": "x" * 3000},
                        {"ticker": "316140", "side": "buy", "rationale": "mine",
                         "entry_freshness": "late"},
                        {"ticker": "316140", "side": "hold", "rationale": "other side"}],
            "summary": "사이클 요약"}}
        _slim_trace_decision(d)
        assert d["response_json"]["signal"]["rationale"] == "mine"
        assert d["response_json"]["summary"] == "사이클 요약"
        assert "signals" not in d["response_json"]
        assert d["entry_freshness"] == "late"

    def test_missing_signal_does_not_resend_all(self):
        from trading.dashboard.queries import _slim_trace_decision
        d = {"ticker": "999999", "side": "buy",
             "response_json": {"signals": [{"ticker": "A", "side": "buy"}], "summary": "s"}}
        _slim_trace_decision(d)
        assert d["response_json"] == {"signal": None, "summary": "s"}
        assert d["entry_freshness"] is None

    def test_non_dict_response_untouched(self):
        from trading.dashboard.queries import _slim_trace_decision
        d = {"ticker": "A", "side": "buy", "response_json": None}
        _slim_trace_decision(d)
        assert d["response_json"] is None


class TestRoutesWired:
    def test_four_gate_routes_exist(self):
        from trading.dashboard.app import app
        paths = {r.path for r in app.routes}
        for p in ("/api/gate/holding-period", "/api/gate/entry-quality",
                  "/api/gate/risk", "/api/gate/sizing"):
            assert p in paths, p


class TestHoldReasonNotInvertedByCompliance:
    """2026-08-27: 근거가 '한도를 지켰다' 고 말해도 한도 위반으로 분류하던 결함.

    실측에서 '한도' 라벨 8건 중 7건이 035420 이었는데 전부
    "한도 5종 전항목 준수로 수치상 위반은 없으나..." 로 시작하는 근거였다.
    지표가 뜻을 정반대로 읽고 있었고, 그 위에서 "한도로 거른 게 +22% 손해" 라는
    틀린 결론이 나왔다. 고친 뒤 한도 라벨은 8건 -> 2건이 됐다.
    """

    def test_compliance_phrasing_is_not_a_limit_reason(self):
        from trading.dashboard.gate_queries import _classify_hold_reason

        rationale = (
            "한도 5종 전항목 준수로 수치상 위반은 없으나, 외국인·기관 5일 누적 "
            "동반 이탈이 확인되고 MA60 대비 하방에 위치해 중기 추세가 미회복 상태다."
        )
        assert _classify_hold_reason(rationale) != "한도"

    def test_real_limit_violation_is_still_caught(self):
        from trading.dashboard.gate_queries import _classify_hold_reason

        for phrase in ("종목당 한도 초과", "추가 여력 소진", "섹터 편중 60% 룰에 명백히 저촉"):
            assert _classify_hold_reason(f"...{phrase}...") == "한도", phrase
