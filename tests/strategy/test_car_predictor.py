"""Tests for CAR Prediction Engine (Module 2).

Tests REQ-CARPRED-02-1 through REQ-CARPRED-02-4.
"""

from __future__ import annotations

import math
from datetime import date, timedelta
from unittest.mock import MagicMock, patch

import pytest

from trading.strategy.car.models import CARPrediction
from trading.strategy.car.predictor import (
    MIN_SAMPLE_COUNT,
    RECENCY_HALF_LIFE_DAYS,
    _compute_confidence,
    _top_similar,
    predict_car,
)


class TestPredictCAR:
    """REQ-CARPRED-02-1: Predict CAR from historical events."""

    @patch("trading.strategy.car.predictor._fetch_similar_events")
    def test_sufficient_history_returns_prediction(self, mock_fetch):
        """M2-1: Prediction with sufficient history."""
        today = date(2025, 3, 15)
        rows = [
            {
                "ticker": "000660",
                "event_type": "disclosure",
                "event_subtype": "earnings",
                "event_date": today - timedelta(days=i * 30),
                "event_magnitude": 0.05,
                "car_1d": 0.005 + i * 0.001,
                "car_5d": 0.023 + i * 0.002,
                "car_10d": 0.03,
            }
            for i in range(25)
        ]
        mock_fetch.return_value = rows

        result = predict_car(
            event_type="disclosure",
            event_subtype="earnings",
            ticker="000660",
            event_magnitude=0.05,
            reference_date=today,
        )

        assert isinstance(result, CARPrediction)
        assert result.sample_count == 25
        assert result.confidence > 0.5
        assert result.predicted_car_5d > 0
        assert len(result.similar_events) <= 5

    @patch("trading.strategy.car.predictor._fetch_similar_events")
    def test_insufficient_history_zero_confidence(self, mock_fetch):
        """M2-2: Prediction with insufficient history returns confidence=0.0."""
        rows = [
            {
                "ticker": "999999",
                "event_type": "disclosure",
                "event_subtype": "governance",
                "event_date": date(2025, 1, 1) - timedelta(days=i * 30),
                "event_magnitude": 0.02,
                "car_1d": 0.003,
                "car_5d": 0.005,
                "car_10d": 0.007,
            }
            for i in range(5)
        ]
        mock_fetch.return_value = rows

        result = predict_car(
            event_type="disclosure",
            event_subtype="governance",
            ticker="999999",
            reference_date=date(2025, 3, 15),
        )

        assert result.confidence == 0.0
        assert result.sample_count == 5

    @patch("trading.strategy.car.predictor._fetch_similar_events")
    def test_no_history_returns_empty_prediction(self, mock_fetch):
        """No historical data returns default prediction."""
        mock_fetch.return_value = []

        result = predict_car(
            event_type="unknown_type",
            event_subtype=None,
            ticker="999999",
        )

        assert result.predicted_car_5d == 0.0
        assert result.confidence == 0.0
        assert result.sample_count == 0

    @patch("trading.strategy.car.predictor._fetch_similar_events")
    def test_recency_weighting(self, mock_fetch):
        """M2-3: Recent events get higher weight."""
        today = date(2025, 6, 1)
        # Recent events with high CAR
        recent = [
            {
                "ticker": "005930",
                "event_type": "price_spike",
                "event_subtype": "positive_3pct",
                "event_date": today - timedelta(days=30),
                "event_magnitude": 0.04,
                "car_1d": 0.01,
                "car_5d": 0.025,
                "car_10d": 0.03,
            }
            for _ in range(10)
        ]
        # Old events with low CAR
        old = [
            {
                "ticker": "005930",
                "event_type": "price_spike",
                "event_subtype": "positive_3pct",
                "event_date": today - timedelta(days=400),
                "event_magnitude": 0.03,
                "car_1d": 0.002,
                "car_5d": 0.005,
                "car_10d": 0.006,
            }
            for _ in range(20)
        ]
        mock_fetch.return_value = recent + old

        result = predict_car(
            event_type="price_spike",
            event_subtype="positive_3pct",
            ticker="005930",
            event_magnitude=0.04,
            reference_date=today,
        )

        # Predicted CAR should be closer to 0.025 (recent) than 0.005 (old)
        assert result.predicted_car_5d > 0.01


class TestComputeConfidence:
    """Test confidence score computation."""

    def test_below_min_sample_returns_zero(self):
        rows = [{"car_5d": 0.01}] * 5
        assert _compute_confidence(rows, 5) == 0.0

    def test_large_sample_low_variance_high_confidence(self):
        rows = [{"car_5d": 0.02}] * 50
        confidence = _compute_confidence(rows, 50)
        assert confidence > 0.7

    def test_large_sample_high_variance_lower_confidence(self):
        # High variance: alternating positive and negative
        rows = [{"car_5d": 0.05 if i % 2 == 0 else -0.05} for i in range(50)]
        confidence = _compute_confidence(rows, 50)
        # Should still be reasonable but lower than low-variance case
        assert confidence < 0.8


class TestTopSimilar:
    """Test similar event selection."""

    def test_returns_limited_results(self):
        today = date(2025, 3, 1)
        rows = [
            {
                "ticker": f"00{i}",
                "event_type": "price_spike",
                "event_subtype": "positive_3pct",
                "event_date": today - timedelta(days=i * 10),
                "event_magnitude": 0.03,
                "car_5d": 0.01 * i,
                "car_10d": 0.015,
            }
            for i in range(1, 20)
        ]

        result = _top_similar(rows, today, event_magnitude=0.03, limit=5)
        assert len(result) == 5

    def test_most_recent_ranked_first(self):
        today = date(2025, 3, 1)
        rows = [
            {
                "ticker": "005930",
                "event_type": "disclosure",
                "event_subtype": "earnings",
                "event_date": today - timedelta(days=10),
                "event_magnitude": 0.05,
                "car_5d": 0.03,
            },
            {
                "ticker": "005930",
                "event_type": "disclosure",
                "event_subtype": "earnings",
                "event_date": today - timedelta(days=300),
                "event_magnitude": 0.05,
                "car_5d": 0.01,
            },
        ]

        result = _top_similar(rows, today, event_magnitude=0.05, limit=5)
        assert result[0]["car_5d"] == 0.03  # Most recent should be first
