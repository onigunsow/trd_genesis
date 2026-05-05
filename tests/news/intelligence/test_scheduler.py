"""Tests for intelligence pipeline scheduler."""

from trading.news.intelligence.scheduler import (
    FEATURE_FLAG,
    PipelineResult,
)


class TestPipelineResult:
    def test_default_values(self):
        result = PipelineResult()
        assert result.articles_analyzed == 0
        assert result.clusters_formed == 0
        assert result.success is True
        assert result.error is None

    def test_failed_result(self):
        result = PipelineResult(
            success=False,
            error="RuntimeError: test error",
        )
        assert result.success is False
        assert "RuntimeError" in result.error


class TestFeatureFlag:
    def test_flag_name(self):
        assert FEATURE_FLAG == "news_intelligence_enabled"
