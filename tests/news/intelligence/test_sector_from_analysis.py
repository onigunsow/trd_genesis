"""SPEC-TRADING-026 (A2) — LLM-derived sector correction at analysis time.

Keyword reclassification (c3/A1) is best-effort and has gaps (geopolitics,
index/macro). The Haiku analyzer already reads each article's content and emits
JSON; A2 has it also emit a content-derived ``sector`` from the canonical set.
On import the article's sector is corrected when the LLM returns a valid sector
different from the (feed-derived) one. Invalid/missing/same → no change.
"""

from __future__ import annotations

from trading.news.intelligence.analyzer import (
    _corrected_sector,
    _parse_analysis_response,
)


class TestCorrectedSector:
    def test_valid_different_sector_applied(self):
        assert _corrected_sector({"sector": "biotech_pharma"}, "semiconductor") == "biotech_pharma"

    def test_valid_same_sector_no_change(self):
        assert _corrected_sector({"sector": "semiconductor"}, "semiconductor") is None

    def test_invalid_sector_ignored(self):
        assert _corrected_sector({"sector": "nonsense"}, "semiconductor") is None

    def test_missing_sector_ignored(self):
        assert _corrected_sector({}, "semiconductor") is None

    def test_empty_string_ignored(self):
        assert _corrected_sector({"sector": ""}, "semiconductor") is None


class TestParseIncludesSector:
    def _wrap(self, extra: str) -> str:
        return (
            '[{"classification":"sector_specific","impact_score":4,'
            '"investment_implication":"가 나","keywords":["x"],'
            '"sentiment":"neutral"' + extra + "}]"
        )

    def test_parse_extracts_valid_sector(self):
        out = _parse_analysis_response(self._wrap(',"sector":"energy_commodities"'), 1)
        assert out is not None
        assert out[0]["sector"] == "energy_commodities"

    def test_parse_defaults_empty_when_absent(self):
        out = _parse_analysis_response(self._wrap(""), 1)
        assert out is not None
        assert out[0]["sector"] == ""

    def test_parse_invalidates_bad_sector(self):
        out = _parse_analysis_response(self._wrap(',"sector":"garbage"'), 1)
        assert out is not None
        assert out[0]["sector"] == ""
