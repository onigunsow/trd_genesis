"""CAR Prediction Engine — estimate expected CAR for new events.

REQ-CARPRED-02-1: Predict CAR magnitude from historical similar events.
REQ-CARPRED-02-2: Recency-weighted + magnitude-similarity weighted mean.
REQ-CARPRED-02-4: Low sample count returns confidence=0.0.
"""

from __future__ import annotations

import logging
import math
from datetime import date, timedelta
from typing import Any

from trading.db.session import connection
from trading.strategy.car.models import CARPrediction

LOG = logging.getLogger(__name__)

# Minimum sample count for meaningful prediction (REQ-CARPRED-02-4)
MIN_SAMPLE_COUNT: int = 10
# Half-life for recency weighting in days
RECENCY_HALF_LIFE_DAYS: int = 180
# Gaussian kernel bandwidth for magnitude similarity
MAGNITUDE_BANDWIDTH: float = 0.02


def predict_car(
    event_type: str,
    event_subtype: str | None,
    ticker: str,
    event_magnitude: float | None = None,
    reference_date: date | None = None,
) -> CARPrediction:
    """Predict CAR for a new event based on historical data.

    REQ-CARPRED-02-2: Algorithm:
    1. Query matching events (same type + subtype, or type only if subtype < 10)
    2. Filter to same sector if available
    3. Compute weighted mean with recency + magnitude weights
    4. Return prediction with confidence score

    Args:
        event_type: Event category (price_spike, disclosure, vix_shock, fx_shock).
        event_subtype: Sub-category (earnings, governance, positive_3pct, etc.).
        ticker: Stock code for sector matching.
        event_magnitude: Magnitude of triggering event (e.g. price change %).
        reference_date: Date to compute recency from (default: today).

    Returns:
        CARPrediction with predicted values and confidence.
    """
    ref_date = reference_date or date.today()
    prediction = CARPrediction(
        event_type=event_type,
        event_subtype=event_subtype,
        ticker=ticker,
    )

    # Query historical events
    rows = _fetch_similar_events(event_type, event_subtype, ticker)

    if not rows:
        return prediction

    # If subtype match has < MIN_SAMPLE_COUNT, broaden to type-only
    if len(rows) < MIN_SAMPLE_COUNT and event_subtype:
        rows = _fetch_similar_events(event_type, None, ticker)

    if not rows:
        return prediction

    prediction.sample_count = len(rows)

    # REQ-CARPRED-02-4: Insufficient data
    if len(rows) < MIN_SAMPLE_COUNT:
        prediction.confidence = 0.0
        return prediction

    # Compute weighted means
    total_weight = 0.0
    weighted_car_1d = 0.0
    weighted_car_5d = 0.0
    weighted_car_10d = 0.0

    for row in rows:
        if row.get("car_5d") is None:
            continue

        # Recency weight: exp(-ln(2) * days_elapsed / half_life)
        days_elapsed = (ref_date - row["event_date"]).days
        recency_w = math.exp(-math.log(2) * days_elapsed / RECENCY_HALF_LIFE_DAYS)

        # Magnitude similarity weight (Gaussian kernel)
        mag_w = 1.0
        if event_magnitude is not None and row.get("event_magnitude") is not None:
            diff = abs(event_magnitude - row["event_magnitude"])
            mag_w = math.exp(-0.5 * (diff / MAGNITUDE_BANDWIDTH) ** 2)

        weight = recency_w * mag_w
        total_weight += weight
        weighted_car_1d += weight * (row.get("car_1d") or 0.0)
        weighted_car_5d += weight * (row.get("car_5d") or 0.0)
        weighted_car_10d += weight * (row.get("car_10d") or 0.0)

    if total_weight == 0:
        return prediction

    prediction.predicted_car_1d = round(weighted_car_1d / total_weight, 6)
    prediction.predicted_car_5d = round(weighted_car_5d / total_weight, 6)
    prediction.predicted_car_10d = round(weighted_car_10d / total_weight, 6)

    # Confidence based on sample size and variance
    prediction.confidence = _compute_confidence(rows, len(rows))

    # Top-5 similar events for explainability
    prediction.similar_events = _top_similar(rows, ref_date, event_magnitude, limit=5)

    return prediction


def _fetch_similar_events(
    event_type: str,
    event_subtype: str | None,
    ticker: str,
) -> list[dict[str, Any]]:
    """Fetch historical CAR records matching criteria."""
    if event_subtype:
        sql = """
            SELECT ticker, event_type, event_subtype, event_date,
                   event_magnitude, car_1d, car_5d, car_10d
              FROM event_car_history
             WHERE event_type = %s AND event_subtype = %s
               AND car_5d IS NOT NULL
             ORDER BY event_date DESC
             LIMIT 200
        """
        params: tuple[Any, ...] = (event_type, event_subtype)
    else:
        sql = """
            SELECT ticker, event_type, event_subtype, event_date,
                   event_magnitude, car_1d, car_5d, car_10d
              FROM event_car_history
             WHERE event_type = %s
               AND car_5d IS NOT NULL
             ORDER BY event_date DESC
             LIMIT 200
        """
        params = (event_type,)

    try:
        with connection() as conn, conn.cursor() as cur:
            cur.execute(sql, params)
            return list(cur.fetchall())
    except Exception as e:
        LOG.warning("Failed to fetch CAR history: %s", e)
        return []


def _compute_confidence(rows: list[dict[str, Any]], n: int) -> float:
    """Compute confidence score based on sample size and variance.

    Confidence approaches 1.0 with large N and low variance.
    """
    if n < MIN_SAMPLE_COUNT:
        return 0.0

    # Sample size factor: sigmoid-like growth
    size_factor = min(1.0, n / 50.0)

    # Variance factor: lower variance = higher confidence
    car_5d_values = [r["car_5d"] for r in rows if r.get("car_5d") is not None]
    if not car_5d_values:
        return 0.0

    mean_val = sum(car_5d_values) / len(car_5d_values)
    variance = sum((v - mean_val) ** 2 for v in car_5d_values) / len(car_5d_values)
    std_dev = math.sqrt(variance) if variance > 0 else 0.0

    # Low std relative to mean -> high confidence
    cv = std_dev / (abs(mean_val) + 0.001)  # coefficient of variation
    variance_factor = max(0.0, 1.0 - cv / 5.0)

    return round(min(1.0, size_factor * 0.6 + variance_factor * 0.4), 3)


def _top_similar(
    rows: list[dict[str, Any]],
    ref_date: date,
    event_magnitude: float | None,
    limit: int = 5,
) -> list[dict[str, Any]]:
    """Return top-N most similar historical events."""
    scored = []
    for row in rows:
        if row.get("car_5d") is None:
            continue
        days = (ref_date - row["event_date"]).days
        recency_score = math.exp(-math.log(2) * days / RECENCY_HALF_LIFE_DAYS)
        scored.append((recency_score, row))

    scored.sort(key=lambda x: x[0], reverse=True)

    return [
        {
            "ticker": r["ticker"],
            "event_date": str(r["event_date"]),
            "event_type": r["event_type"],
            "car_5d": r["car_5d"],
            "event_magnitude": r.get("event_magnitude"),
        }
        for _, r in scored[:limit]
    ]
