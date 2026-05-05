"""Tests for Smart Event Filter (Module 3).

Tests REQ-FILTER-03-1 through REQ-FILTER-03-7.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from trading.strategy.car.filter import (
    CAR_FILTER_THRESHOLD,
    CONFIDENCE_THRESHOLD,
    _apply_filter_logic,
    _build_car_context,
    evaluate_event,
)
from trading.strategy.car.models import CARPrediction, FilterDecision, FilterResult


class TestApplyFilterLogic:
    """REQ-FILTER-03-2: Filter decision logic."""

    def test_high_car_high_confidence_passes(self):
        """M3-1: |predicted_car_5d| >= threshold with high confidence -> PASS."""
        prediction = CARPrediction(
            event_type="disclosure",
            event_subtype="earnings",
            ticker="000660",
            predicted_car_5d=0.028,
            confidence=0.75,
            sample_count=25,
        )
        decision, reason = _apply_filter_logic(prediction)
        assert decision == FilterDecision.PASS
        assert "material" in reason.lower() or ">=" in reason

    def test_low_car_high_confidence_blocks(self):
        """M3-2: |predicted_car_5d| < threshold with high confidence -> BLOCK."""
        prediction = CARPrediction(
            event_type="disclosure",
            event_subtype="governance",
            ticker="005930",
            predicted_car_5d=0.004,
            confidence=0.8,
            sample_count=30,
        )
        decision, reason = _apply_filter_logic(prediction)
        assert decision == FilterDecision.BLOCK
        assert "too small" in reason.lower() or "<" in reason

    def test_low_confidence_passes_through(self):
        """M3-3: Low confidence -> PASS_LOW_CONFIDENCE (conservative)."""
        prediction = CARPrediction(
            event_type="price_spike",
            event_subtype="positive_3pct",
            ticker="999999",
            predicted_car_5d=0.001,
            confidence=0.3,
            sample_count=5,
        )
        decision, reason = _apply_filter_logic(prediction)
        assert decision == FilterDecision.PASS_LOW_CONFIDENCE
        assert "conservative" in reason.lower() or "low confidence" in reason.lower()

    def test_negative_car_also_passes_if_magnitude_sufficient(self):
        """Negative predicted CAR with high absolute value also passes."""
        prediction = CARPrediction(
            event_type="price_spike",
            event_subtype="negative_5pct",
            ticker="005930",
            predicted_car_5d=-0.025,
            confidence=0.7,
            sample_count=20,
        )
        decision, _ = _apply_filter_logic(prediction)
        assert decision == FilterDecision.PASS

    def test_exactly_at_threshold_passes(self):
        """Edge case: exactly at threshold passes."""
        prediction = CARPrediction(
            event_type="disclosure",
            event_subtype="earnings",
            ticker="005930",
            predicted_car_5d=CAR_FILTER_THRESHOLD,
            confidence=0.6,
            sample_count=15,
        )
        decision, _ = _apply_filter_logic(prediction)
        assert decision == FilterDecision.PASS

    def test_exactly_at_confidence_threshold_passes_through(self):
        """Edge case: exactly at confidence threshold passes through."""
        prediction = CARPrediction(
            event_type="disclosure",
            event_subtype="earnings",
            ticker="005930",
            predicted_car_5d=0.03,
            confidence=CONFIDENCE_THRESHOLD,
            sample_count=10,
        )
        decision, _ = _apply_filter_logic(prediction)
        assert decision == FilterDecision.PASS_LOW_CONFIDENCE


class TestEvaluateEvent:
    """REQ-FILTER-03-1: Full event evaluation flow."""

    @patch("trading.strategy.car.filter._persist_filter_log")
    @patch("trading.strategy.car.filter.audit")
    @patch("trading.strategy.car.filter.predict_car")
    def test_safety_critical_bypasses_filter(self, mock_predict, mock_audit, mock_persist):
        """M3-4: Safety-critical events bypass filter entirely."""
        result = evaluate_event(
            ticker="005930",
            event_type="circuit_breaker",
            is_safety_critical=True,
        )
        assert result.decision == FilterDecision.PASS
        assert "safety" in result.reason.lower() or "bypass" in result.reason.lower()
        mock_predict.assert_not_called()

    @patch("trading.strategy.car.filter._persist_filter_log")
    @patch("trading.strategy.car.filter.audit")
    @patch("trading.strategy.car.filter.predict_car")
    def test_high_car_event_passes_with_context(self, mock_predict, mock_audit, mock_persist):
        """M3-5: Passed events get CAR context injected."""
        mock_predict.return_value = CARPrediction(
            event_type="disclosure",
            event_subtype="earnings",
            ticker="000660",
            predicted_car_5d=0.028,
            confidence=0.75,
            sample_count=25,
            similar_events=[
                {"ticker": "000660", "event_date": "2025-01-01", "event_type": "disclosure", "car_5d": 0.03, "event_magnitude": 0.05},
            ],
        )

        result = evaluate_event(
            ticker="000660",
            event_type="disclosure",
            event_subtype="earnings",
            event_magnitude=0.05,
        )

        assert result.decision == FilterDecision.PASS
        assert result.car_context is not None
        assert "[Event-CAR Context]" in result.car_context
        assert "000660" in result.car_context

    @patch("trading.strategy.car.filter._persist_filter_log")
    @patch("trading.strategy.car.filter.audit")
    @patch("trading.strategy.car.filter.predict_car")
    def test_blocked_event_audited(self, mock_predict, mock_audit, mock_persist):
        """M3-2: Blocked events logged to audit."""
        mock_predict.return_value = CARPrediction(
            event_type="disclosure",
            event_subtype="governance",
            ticker="005930",
            predicted_car_5d=0.004,
            confidence=0.8,
            sample_count=30,
        )

        result = evaluate_event(
            ticker="005930",
            event_type="disclosure",
            event_subtype="governance",
        )

        assert result.decision == FilterDecision.BLOCK
        mock_audit.assert_called()
        # Check audit was called with EVENT_CAR_FILTERED
        audit_calls = [c for c in mock_audit.call_args_list if c[0][0] == "EVENT_CAR_FILTERED"]
        assert len(audit_calls) == 1


class TestBuildCARContext:
    """REQ-FILTER-03-5: CAR context for Decision persona."""

    def test_context_includes_required_fields(self):
        prediction = CARPrediction(
            event_type="disclosure",
            event_subtype="earnings",
            ticker="000660",
            predicted_car_5d=0.028,
            confidence=0.75,
            sample_count=25,
            similar_events=[
                {"ticker": "000660", "event_date": "2025-01-01", "event_type": "disclosure", "car_5d": 0.03, "event_magnitude": 0.05},
                {"ticker": "005930", "event_date": "2025-02-01", "event_type": "disclosure", "car_5d": 0.02, "event_magnitude": 0.04},
            ],
        )

        context = _build_car_context(prediction, "000660", "disclosure", "earnings")

        assert "[Event-CAR Context]" in context
        assert "disclosure/earnings" in context
        assert "000660" in context
        assert "Predicted 5-day CAR:" in context
        assert "positive" in context.lower()
        assert "Historical similar events:" in context

    def test_neutral_interpretation_for_small_car(self):
        prediction = CARPrediction(
            event_type="vix_shock",
            ticker="005930",
            predicted_car_5d=0.003,
            confidence=0.6,
            sample_count=15,
        )

        context = _build_car_context(prediction, "005930", "vix_shock", None)
        assert "neutral" in context.lower()
