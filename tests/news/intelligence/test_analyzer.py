"""Tests for article analyzer module."""

from unittest.mock import MagicMock, patch

import pytest

from trading.news.intelligence.analyzer import (
    TITLE_SIMILARITY_THRESHOLD,
    _apply_quality_checks,
    _parse_analysis_response,
    _prepare_batch,
    check_title_similarity,
    is_noise_title,
)


class TestPrepareBatch:
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


class TestIsNoiseTitle:
    def test_detects_csr_keywords(self):
        assert is_noise_title("삼성전자, 사회공헌 활동 강화")
        assert is_noise_title("현대차 기부 프로그램 확대")
        assert is_noise_title("SK 봉사활동 참여")

    def test_detects_hr_keywords(self):
        assert is_noise_title("LG전자 신임 부사장 취임")
        assert is_noise_title("포스코 인사 이동 발표")
        assert is_noise_title("카카오 부고 소식")

    def test_detects_event_keywords(self):
        assert is_noise_title("네이버 어린이날 축제 개최")
        assert is_noise_title("한화 ESG 보고서 발간")
        assert is_noise_title("삼성 사회공헌 협약 체결")

    def test_passes_real_news(self):
        assert not is_noise_title("한국은행 기준금리 인상 결정")
        assert not is_noise_title("삼성전자 1분기 영업이익 30% 급증")
        assert not is_noise_title("미중 무역전쟁 재점화 우려")

    def test_detects_promotional_patterns(self):
        assert is_noise_title("갤럭시 출시 기념 30% 할인 이벤트")
        assert is_noise_title("신제품 출시 20% 프로모션")

    def test_case_insensitive(self):
        assert is_noise_title("기업 사회공헌 활동")


class TestCheckTitleSimilarity:
    def test_identical_strings(self):
        sim = check_title_similarity("한국은행 금리 인상", "한국은행 금리 인상")
        assert sim > 0.95

    def test_different_strings(self):
        sim = check_title_similarity(
            "한국은행 금리 인상",
            "채권 숏, 은행주 롱 포지션 확대 고려. 고금리 수혜 섹터 주목.",
        )
        assert sim < 0.5

    def test_title_restating_summary(self):
        title = "삼성전자 1분기 영업이익 급증"
        bad_summary = "삼성전자가 1분기 영업이익이 급증했다."
        sim = check_title_similarity(title, bad_summary)
        # Should be high similarity (bad)
        assert sim > 0.6


class TestParseAnalysisResponse:
    def test_valid_new_format(self):
        text = '[{"classification": "macro_market_moving", "impact_score": 5, "investment_implication": "유가 급등 예상. 에너지 롱 포지션 확대 고려.", "keywords": ["유가", "중동", "안전자산"], "sentiment": "negative"}]'
        result = _parse_analysis_response(text, 1)
        assert result is not None
        assert len(result) == 1
        assert result[0]["classification"] == "macro_market_moving"
        assert result[0]["impact_score"] == 5
        assert result[0]["summary_2line"] == "유가 급등 예상. 에너지 롱 포지션 확대 고려."
        assert result[0]["sentiment"] == "negative"
        assert len(result[0]["keywords"]) == 3

    def test_noise_classification_forces_impact_zero(self):
        text = '[{"classification": "noise", "impact_score": 3, "investment_implication": "투자 관련성 없음", "keywords": [], "sentiment": "neutral"}]'
        result = _parse_analysis_response(text, 1)
        assert result is not None
        assert result[0]["classification"] == "noise"
        assert result[0]["impact_score"] == 0  # Forced to 0

    def test_backward_compatible_with_summary_2line(self):
        text = '[{"summary_2line": "Line1\\nLine2", "impact_score": 4, "keywords": ["k1", "k2", "k3"], "sentiment": "positive"}]'
        result = _parse_analysis_response(text, 1)
        assert result is not None
        assert result[0]["impact_score"] == 4
        assert result[0]["summary_2line"] == "Line1\nLine2"
        # Default classification when not provided
        assert result[0]["classification"] == "company_specific"

    def test_json_with_code_fences(self):
        text = '```json\n[{"classification": "sector_specific", "impact_score": 3, "investment_implication": "반도체 업황 개선.", "keywords": ["반도체"], "sentiment": "positive"}]\n```'
        result = _parse_analysis_response(text, 1)
        assert result is not None
        assert result[0]["classification"] == "sector_specific"
        assert result[0]["impact_score"] == 3

    def test_clamps_impact_score(self):
        text = '[{"classification": "macro_market_moving", "impact_score": 10, "investment_implication": "Test", "keywords": ["k1"], "sentiment": "neutral"}]'
        result = _parse_analysis_response(text, 1)
        assert result is not None
        assert result[0]["impact_score"] == 5  # Clamped to max

    def test_impact_zero_allowed(self):
        text = '[{"classification": "noise", "impact_score": 0, "investment_implication": "", "keywords": [], "sentiment": "neutral"}]'
        result = _parse_analysis_response(text, 1)
        assert result is not None
        assert result[0]["impact_score"] == 0

    def test_invalid_classification_defaults(self):
        text = '[{"classification": "unknown_type", "impact_score": 3, "investment_implication": "Test", "keywords": ["k1"], "sentiment": "neutral"}]'
        result = _parse_analysis_response(text, 1)
        assert result is not None
        assert result[0]["classification"] == "company_specific"

    def test_invalid_sentiment_defaults_neutral(self):
        text = '[{"classification": "sector_specific", "impact_score": 3, "investment_implication": "Test", "keywords": ["k1"], "sentiment": "bullish"}]'
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
        text = '[{"classification": "company_specific", "impact_score": 1, "investment_implication": "A", "keywords": [], "sentiment": "neutral"}, {"classification": "sector_specific", "impact_score": 2, "investment_implication": "B", "keywords": [], "sentiment": "neutral"}, {"classification": "macro_market_moving", "impact_score": 3, "investment_implication": "C", "keywords": [], "sentiment": "neutral"}]'
        result = _parse_analysis_response(text, 2)
        assert result is not None
        assert len(result) == 2

    def test_truncates_keywords_to_five(self):
        text = '[{"classification": "sector_specific", "impact_score": 3, "investment_implication": "Test", "keywords": ["a","b","c","d","e","f","g"], "sentiment": "neutral"}]'
        result = _parse_analysis_response(text, 1)
        assert result is not None
        assert len(result[0]["keywords"]) == 5


class TestApplyQualityChecks:
    def test_penalizes_title_restating_summary(self):
        articles = [{"title": "삼성전자 실적 발표", "id": 1}]
        results = [{
            "summary_2line": "삼성전자 실적 발표했다.",
            "impact_score": 3,
            "keywords": ["삼성"],
            "sentiment": "neutral",
            "classification": "company_specific",
        }]
        checked = _apply_quality_checks(articles, results)
        # Impact should be penalized (reduced by 1)
        assert checked[0]["impact_score"] < 3

    def test_does_not_penalize_actionable_implication(self):
        articles = [{"title": "한국은행 기준금리 인상 0.25%p", "id": 1}]
        results = [{
            "summary_2line": "채권 가격 하락 압력. 은행주 NIM 개선 기대로 은행 섹터 롱 고려.",
            "impact_score": 5,
            "keywords": ["금리", "채권", "은행"],
            "sentiment": "negative",
            "classification": "macro_market_moving",
        }]
        checked = _apply_quality_checks(articles, results)
        # Impact should remain unchanged
        assert checked[0]["impact_score"] == 5

    def test_impact_zero_becomes_noise(self):
        articles = [{"title": "기업 행사 개최", "id": 1}]
        results = [{
            "summary_2line": "기업 행사가 개최되었다.",
            "impact_score": 1,
            "keywords": [],
            "sentiment": "neutral",
            "classification": "company_specific",
        }]
        checked = _apply_quality_checks(articles, results)
        # After penalty, impact should be 0 and classification noise
        assert checked[0]["impact_score"] == 0
        assert checked[0]["classification"] == "noise"
