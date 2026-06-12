"""SPEC-TRADING-043 Group A — news-import dead-fallback elimination.

Under standing ``cli_only_mode`` with no host CLI results file, the import path
``scheduled_import`` previously fell back to ``analyze_articles()`` →
``_call_haiku`` (``@block_if_cli_only_mode``) which RAISES, producing ~20
ERROR/WARN lines/day of guaranteed-to-fail dead code.

REQ-043-A1/A2: under cli_only_mode the fallback must NOT run; a single INFO line
is logged and articles stay pending.
REQ-043-A3: the host-result success path (import_host_results > 0) is unchanged.
REQ-043-A4: when cli_only_mode is OFF the sanctioned Haiku fallback still runs.
"""

from __future__ import annotations

import logging
from unittest.mock import patch

import trading.news.intelligence.scheduler as sched


class _FakeMissingFile:
    @staticmethod
    def exists() -> bool:
        return False


def test_cli_only_mode_skips_haiku_fallback(caplog):
    """REQ-043-A1/A2: cli_only_mode active + no results → no analyze_articles,
    single INFO line, no WARNING/ERROR fallback noise."""
    with (
        patch("trading.news.intelligence.scheduler.is_intelligence_enabled",
              return_value=True),
        patch("trading.news.intelligence.analyzer.import_host_results",
              return_value=0),
        patch("trading.news.intelligence.analyzer.RESULTS_FILE", _FakeMissingFile),
        patch("trading.news.intelligence.scheduler.is_cli_only_mode",
              return_value=True),
        patch("trading.news.intelligence.analyzer.analyze_articles") as mock_analyze,
        patch("trading.news.intelligence.scheduler._run_post_analysis_pipeline") as mock_post,
    ):
        with caplog.at_level(logging.INFO,
                             logger="trading.news.intelligence.scheduler"):
            sched.scheduled_import()

    # REQ-043-A1: the Haiku fallback was NOT invoked.
    mock_analyze.assert_not_called()
    mock_post.assert_not_called()

    # REQ-043-A2: exactly one INFO defer line, no WARNING/ERROR fallback noise.
    defer_lines = [r for r in caplog.records
                   if "deferring to next slot" in r.getMessage()]
    assert len(defer_lines) == 1
    assert defer_lines[0].levelno == logging.INFO
    assert not any(r.levelno >= logging.WARNING for r in caplog.records)


def test_fallback_still_runs_when_cli_only_mode_off(caplog):
    """REQ-043-A4: cli_only_mode OFF + no results → sanctioned Haiku fallback runs."""

    class _Metrics:
        articles_processed = 7

    with (
        patch("trading.news.intelligence.scheduler.is_intelligence_enabled",
              return_value=True),
        patch("trading.news.intelligence.analyzer.import_host_results",
              return_value=0),
        patch("trading.news.intelligence.analyzer.RESULTS_FILE", _FakeMissingFile),
        patch("trading.news.intelligence.scheduler.is_cli_only_mode",
              return_value=False),
        patch("trading.news.intelligence.scheduler.audit"),
        patch("trading.news.intelligence.analyzer.analyze_articles",
              return_value=_Metrics()) as mock_analyze,
        patch("trading.news.intelligence.scheduler._run_post_analysis_pipeline") as mock_post,
    ):
        with caplog.at_level(logging.WARNING,
                             logger="trading.news.intelligence.scheduler"):
            sched.scheduled_import()

    # The sanctioned fallback path runs the analysis + post-pipeline.
    mock_analyze.assert_called_once()
    mock_post.assert_called_once()
    # The existing WARNING is still emitted in the sanctioned case.
    assert any("falling back to Haiku API" in r.getMessage()
               for r in caplog.records)


def test_success_path_unchanged_runs_post_pipeline():
    """REQ-043-A3: host results present (>0) → post-pipeline runs, no fallback,
    cli_only_mode is never even consulted."""
    with (
        patch("trading.news.intelligence.scheduler.is_intelligence_enabled",
              return_value=True),
        patch("trading.news.intelligence.analyzer.import_host_results",
              return_value=12),
        patch("trading.news.intelligence.scheduler.is_cli_only_mode") as mock_mode,
        patch("trading.news.intelligence.analyzer.analyze_articles") as mock_analyze,
        patch("trading.news.intelligence.scheduler._run_post_analysis_pipeline") as mock_post,
    ):
        sched.scheduled_import()

    mock_post.assert_called_once()
    mock_analyze.assert_not_called()
    mock_mode.assert_not_called()
