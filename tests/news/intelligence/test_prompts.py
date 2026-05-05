"""Tests for Haiku prompt templates."""

from trading.news.intelligence.prompts import (
    ARTICLE_ANALYSIS_SYSTEM,
    build_analysis_prompt,
)


class TestArticleAnalysisPrompt:
    def test_system_prompt_contains_key_instructions(self):
        assert "senior equity research analyst" in ARTICLE_ANALYSIS_SYSTEM
        assert "classification" in ARTICLE_ANALYSIS_SYSTEM
        assert "impact_score" in ARTICLE_ANALYSIS_SYSTEM
        assert "investment_implication" in ARTICLE_ANALYSIS_SYSTEM
        assert "keywords" in ARTICLE_ANALYSIS_SYSTEM
        assert "sentiment" in ARTICLE_ANALYSIS_SYSTEM
        assert "JSON array" in ARTICLE_ANALYSIS_SYSTEM

    def test_system_prompt_defines_classifications(self):
        assert "macro_market_moving" in ARTICLE_ANALYSIS_SYSTEM
        assert "sector_specific" in ARTICLE_ANALYSIS_SYSTEM
        assert "company_specific" in ARTICLE_ANALYSIS_SYSTEM
        assert "noise" in ARTICLE_ANALYSIS_SYSTEM

    def test_system_prompt_includes_noise_guidance(self):
        assert "PR" in ARTICLE_ANALYSIS_SYSTEM
        assert "CSR" in ARTICLE_ANALYSIS_SYSTEM
        assert "HR" in ARTICLE_ANALYSIS_SYSTEM
        assert "impact_score=0" in ARTICLE_ANALYSIS_SYSTEM

    def test_system_prompt_demands_actionable_implications(self):
        # Must not restate headlines
        assert "DO NOT restate" in ARTICLE_ANALYSIS_SYSTEM
        # Must answer what to DO
        assert "투자자는 어떤 포지션 조정을 고려해야 하는가" in ARTICLE_ANALYSIS_SYSTEM

    def test_build_prompt_single_article(self):
        articles = [{
            "title": "Samsung Electronics reports record Q1 profits",
            "source_name": "Reuters",
            "sector": "semiconductor",
            "body_excerpt": "Samsung Electronics posted record quarterly profits...",
        }]
        prompt = build_analysis_prompt(articles)
        assert "[1]" in prompt
        assert "Samsung Electronics" in prompt
        assert "Reuters" in prompt
        assert "semiconductor" in prompt

    def test_build_prompt_batch_of_ten(self):
        articles = [
            {
                "title": f"Article {i}",
                "source_name": f"Source {i}",
                "sector": "macro_economy",
                "body_excerpt": f"Body text for article {i}",
            }
            for i in range(10)
        ]
        prompt = build_analysis_prompt(articles)
        assert "[1]" in prompt
        assert "[10]" in prompt
        assert "Article 0" in prompt
        assert "Article 9" in prompt

    def test_build_prompt_truncates_body(self):
        articles = [{
            "title": "Test",
            "source_name": "Test",
            "sector": "test",
            "body_excerpt": "x" * 2000,
        }]
        prompt = build_analysis_prompt(articles)
        # Body should be truncated to 1000 chars in the prompt
        assert len(prompt) < 1500  # title + source + 1000 char body + formatting

    def test_build_prompt_handles_missing_body(self):
        articles = [{
            "title": "No body article",
            "source_name": "Source",
            "sector": "test",
            "body_excerpt": "",
        }]
        prompt = build_analysis_prompt(articles)
        assert "No body article" in prompt
        assert "Body:" not in prompt
