"""Tests for article analyzer module."""

from unittest.mock import MagicMock, patch

import pytest

from trading.news.intelligence.analyzer import (
    _parse_analysis_response,
    _prepare_batch,
)


class TestPrepareB:
    def test_extracts_fields(self):
        articles = [{
            "id": 1,
            "title": "Test Article",
            "source_name": "Reuters",
            "sector": "semiconductor",
            "body_text": "Full body text content here",
            "summary": "Short summary",
            "published_at": "2026-05-05",
        }]
        batch = _prepare_batch(articles)
        assert len(batch) == 1
        assert batch[0]["title"] == "Test Article"
        assert batch[0]["source_name"] == "Reuters"
        assert batch[0]["sector"] == "semiconductor"
        assert len(batch[0]["body_excerpt"]) <= 1000

    def test_body_text_truncation(self):
        articles = [{
            "id": 1,
            "title": "Test",
            "source_name": "Test",
            "sector": "test",
            "body_text": "x" * 2000,
            "summary": None,
            "published_at": "2026-05-05",
        }]
        batch = _prepare_batch(articles)
        assert len(batch[0]["body_excerpt"]) == 1000

    def test_fallback_to_summary(self):
        articles = [{
            "id": 1,
            "title": "Test",
            "source_name": "Test",
            "sector": "test",
            "body_text": None,
            "summary": "Summary text",
            "published_at": "2026-05-05",
        }]
        batch = _prepare_batch(articles)
        assert batch[0]["body_excerpt"] == "Summary text"


class TestParseAnalysisResponse:
    def test_valid_json_array(self):
        text = '[{"summary_2line": "Line1\\nLine2", "impact_score": 4, "keywords": ["k1", "k2", "k3"], "sentiment": "positive"}]'
        result = _parse_analysis_response(text, 1)
        assert result is not None
        assert len(result) == 1
        assert result[0]["impact_score"] == 4
        assert result[0]["sentiment"] == "positive"
        assert len(result[0]["keywords"]) == 3

    def test_json_with_code_fences(self):
        text = '```json\n[{"summary_2line": "Test", "impact_score": 3, "keywords": ["k1"], "sentiment": "neutral"}]\n```'
        result = _parse_analysis_response(text, 1)
        assert result is not None
        assert result[0]["impact_score"] == 3

    def test_clamps_impact_score(self):
        text = '[{"summary_2line": "Test", "impact_score": 10, "keywords": ["k1"], "sentiment": "neutral"}]'
        result = _parse_analysis_response(text, 1)
        assert result is not None
        assert result[0]["impact_score"] == 5  # Clamped to max

    def test_invalid_sentiment_defaults_neutral(self):
        text = '[{"summary_2line": "Test", "impact_score": 3, "keywords": ["k1"], "sentiment": "bullish"}]'
        result = _parse_analysis_response(text, 1)
        assert result is not None
        assert result[0]["sentiment"] == "neutral"

    def test_invalid_json_returns_none(self):
        text = "This is not valid JSON at all"
        result = _parse_analysis_response(text, 1)
        assert result is None

    def test_empty_array_returns_none(self):
        text = "[]"
        result = _parse_analysis_response(text, 1)
        assert result is None

    def test_limits_to_expected_count(self):
        text = '[{"summary_2line": "A", "impact_score": 1, "keywords": [], "sentiment": "neutral"}, {"summary_2line": "B", "impact_score": 2, "keywords": [], "sentiment": "neutral"}, {"summary_2line": "C", "impact_score": 3, "keywords": [], "sentiment": "neutral"}]'
        result = _parse_analysis_response(text, 2)
        assert result is not None
        assert len(result) == 2

    def test_truncates_keywords_to_five(self):
        text = '[{"summary_2line": "Test", "impact_score": 3, "keywords": ["a","b","c","d","e","f","g"], "sentiment": "neutral"}]'
        result = _parse_analysis_response(text, 1)
        assert result is not None
        assert len(result[0]["keywords"]) == 5
