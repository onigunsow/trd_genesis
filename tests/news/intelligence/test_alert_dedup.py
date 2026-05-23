"""SPEC-TRADING-026 (c4) — stable news-alert dedup by article identity.

The old dedup hashed ``representative_title``, which is the *highest-impact
member's* title and therefore changes when re-clustering (every 3h) admits a new
member — so the same story re-alerted under a slightly different headline. The
fix dedups on the cluster's underlying ``article_ids`` (stable story identity):
if ANY member article was alerted within the rolling window, the alert is
suppressed regardless of which headline is currently representative.
"""

from __future__ import annotations

import pytest

from trading.news.intelligence import relevance as rel


class TestAlertKeys:
    def test_keys_from_article_ids(self):
        keys = rel._alert_keys(
            {"article_ids": [10, 11, 12], "representative_title": "x"}
        )
        assert keys == ["art:10", "art:11", "art:12"]

    def test_fallback_to_title_hash_when_no_ids(self):
        keys = rel._alert_keys(
            {"article_ids": [], "representative_title": "엔비디아 어닝"}
        )
        assert len(keys) == 1
        assert keys[0] and not keys[0].startswith("art:")  # a title hash


@pytest.fixture
def fake_store(monkeypatch):
    """Replace the DB-backed dedup helpers with an in-memory set + capture sends."""
    store: set[str] = set()
    sent: list[tuple] = []

    monkeypatch.setattr(
        rel, "_any_alerted_recently", lambda keys: any(k in store for k in keys)
    )
    monkeypatch.setattr(rel, "_record_alerts", lambda keys: store.update(keys))
    monkeypatch.setattr(
        "trading.alerts.telegram.system_briefing",
        lambda *a, **k: sent.append(a),
    )
    return store, sent


class TestSendCriticalAlertDedup:
    def test_same_story_new_representative_title_deduped(self, fake_store):
        _store, sent = fake_store
        # Same NVIDIA earnings story, re-clustered with a different headline but
        # overlapping member articles.
        c1 = {
            "representative_title": "엔비디아 어닝 서프라이즈",
            "sector": "semiconductor",
            "article_ids": [10, 11, 12],
        }
        c2 = {
            "representative_title": "엔비디아, 1분기 영업익 80.2조…147%↑",
            "sector": "semiconductor",
            "article_ids": [11, 12, 13],  # shares 11,12 with c1
        }
        rel._send_critical_alert(c1)
        rel._send_critical_alert(c2)
        assert len(sent) == 1, "re-clustered same story must not re-alert"

    def test_distinct_story_still_alerts(self, fake_store):
        _store, sent = fake_store
        c1 = {
            "representative_title": "엔비디아 어닝",
            "sector": "semiconductor",
            "article_ids": [10, 11],
        }
        c2 = {
            "representative_title": "삼성SDI 배터리 수주",
            "sector": "auto_ev_battery",
            "article_ids": [20, 21],  # disjoint → genuinely new story
        }
        rel._send_critical_alert(c1)
        rel._send_critical_alert(c2)
        assert len(sent) == 2, "a disjoint story must still alert"

    def test_first_alert_sends_and_records(self, fake_store):
        store, sent = fake_store
        rel._send_critical_alert(
            {
                "representative_title": "유가 급등",
                "sector": "energy_commodities",
                "article_ids": [30, 31],
            }
        )
        assert len(sent) == 1
        assert "art:30" in store and "art:31" in store
