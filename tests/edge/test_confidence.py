"""Edge Validation Phase 2 — 확신도 구간/상관/위험게이트 override 분석."""

from __future__ import annotations

from datetime import date, timedelta

from trading.edge import confidence as cf
from trading.edge.roundtrips import RoundTrip


def _rt(i, conf, profit, verdict=None):
    d = date(2026, 1, 1) + timedelta(days=i)
    return RoundTrip(
        ticker="A", entry_date=d, exit_date=d + timedelta(days=1),
        qty=1, entry_price=10_000, exit_price=10_000 + profit,
        entry_fee=0, exit_fee=0, confidence=conf, verdict=verdict,
    )


class TestBuckets:
    def test_buckets_partition_by_confidence(self):
        rts = [
            _rt(0, 0.40, 100),
            _rt(1, 0.60, 100),
            _rt(2, 0.80, 100),
            _rt(3, 0.90, 100),
        ]
        rep = cf.analyze(rts)
        labels = {b.label: b.n for b in rep.buckets}
        assert labels["<0.50"] == 1
        assert labels["0.50–0.70"] == 1
        assert labels["0.70–0.85"] == 1
        assert labels[">0.85"] == 1

    def test_none_confidence_counted_separately(self):
        rts = [_rt(0, None, 100), _rt(1, 0.9, 100)]
        rep = cf.analyze(rts)
        assert rep.none_count == 1
        assert rep.n_with_conf == 1


class TestCorrelation:
    def test_n_lt_3_guard(self):
        rts = [_rt(0, 0.5, 100), _rt(1, 0.9, 200)]
        rep = cf.analyze(rts)
        assert rep.pearson is None
        assert rep.spearman is None

    def test_positive_correlation_when_higher_conf_earns_more(self):
        rts = [_rt(i, 0.3 + 0.1 * i, 100 * i) for i in range(6)]
        rep = cf.analyze(rts)
        assert rep.pearson is not None
        assert rep.pearson > 0.5
        assert rep.spearman is not None
        assert rep.spearman > 0.5


class TestOverride:
    def test_override_worse_than_approve(self):
        # APPROVE 거래는 이익, override(HOLD/REJECT 체결)는 손실 → 게이트 가치 있음.
        rts = [_rt(i, 0.8, 500, verdict="APPROVE") for i in range(4)]
        rts += [_rt(10 + i, 0.8, -300, verdict="HOLD") for i in range(3)]
        rts += [_rt(20 + i, 0.8, -300, verdict="REJECT") for i in range(2)]
        rep = cf.analyze(rts)
        assert rep.approve.n == 4
        assert rep.overridden.n == 5
        assert rep.overridden.expectancy < rep.approve.expectancy
        text = cf.render(rep)
        assert "위험 게이트가 가치 있음" in text

    def test_render_no_confidence(self):
        rts = [_rt(0, None, 100), _rt(1, None, -50)]
        rep = cf.analyze(rts)
        text = cf.render(rep)
        assert "분석 불가" in text
