"""Smart Event Filter — CAR-based event filtering before Decision persona.

REQ-FILTER-03-1: Sits between event trigger and Decision invocation.
REQ-FILTER-03-2: PASS/BLOCK/PASS_LOW_CONFIDENCE decision logic.
REQ-FILTER-03-3: Configurable threshold via CAR_FILTER_THRESHOLD env var.
REQ-FILTER-03-4: Logs blocked events to audit_log.
REQ-FILTER-03-5: Injects CAR context on PASS.
REQ-FILTER-03-7: Safety-critical events bypass filter.
"""

from __future__ import annotations

import logging
import os
from typing import Any

from trading.db.session import audit, connection
from trading.strategy.car.models import CARPrediction, FilterDecision, FilterResult
from trading.strategy.car.predictor import predict_car

LOG = logging.getLogger(__name__)

# REQ-FILTER-03-3: Configurable threshold
CAR_FILTER_THRESHOLD: float = float(os.environ.get("CAR_FILTER_THRESHOLD", "0.015"))

# Confidence threshold below which we pass-through (conservative)
CONFIDENCE_THRESHOLD: float = 0.5


def evaluate_event(
    ticker: str,
    event_type: str,
    event_subtype: str | None = None,
    event_magnitude: float | None = None,
    is_safety_critical: bool = False,
) -> FilterResult:
    """Evaluate whether an event should trigger Decision persona.

    REQ-FILTER-03-2: Decision logic:
    - If |predicted_car_5d| >= threshold AND confidence > 0.5: PASS
    - If |predicted_car_5d| < threshold AND confidence > 0.5: BLOCK
    - If confidence <= 0.5: PASS_LOW_CONFIDENCE (conservative pass-through)

    REQ-FILTER-03-7: Safety-critical events bypass filter entirely.

    Args:
        ticker: Stock code.
        event_type: Event category.
        event_subtype: Event sub-category.
        event_magnitude: Triggering magnitude (e.g. price change %).
        is_safety_critical: If True, bypass filter (circuit breaker conditions).

    Returns:
        FilterResult with decision and context.
    """
    # REQ-FILTER-03-7: Safety-critical bypass
    if is_safety_critical:
        result = FilterResult(
            ticker=ticker,
            event_type=event_type,
            event_subtype=event_subtype,
            event_magnitude=event_magnitude,
            decision=FilterDecision.PASS,
            threshold=CAR_FILTER_THRESHOLD,
            reason="Safety-critical event: bypass CAR filter",
        )
        return result

    # Get CAR prediction
    prediction = predict_car(
        event_type=event_type,
        event_subtype=event_subtype,
        ticker=ticker,
        event_magnitude=event_magnitude,
    )

    # Apply decision logic
    decision, reason = _apply_filter_logic(prediction)

    # Build CAR context for Decision persona (REQ-FILTER-03-5)
    car_context = None
    if decision == FilterDecision.PASS:
        car_context = _build_car_context(prediction, ticker, event_type, event_subtype)

    result = FilterResult(
        ticker=ticker,
        event_type=event_type,
        event_subtype=event_subtype,
        event_magnitude=event_magnitude,
        decision=decision,
        predicted_car_5d=prediction.predicted_car_5d,
        confidence=prediction.confidence,
        sample_count=prediction.sample_count,
        threshold=CAR_FILTER_THRESHOLD,
        reason=reason,
        car_context=car_context,
    )

    # Persist filter decision
    _persist_filter_log(result)

    # Audit logging
    if decision == FilterDecision.BLOCK:
        audit("EVENT_CAR_FILTERED", actor="car_filter", details={
            "ticker": ticker,
            "event_type": event_type,
            "event_subtype": event_subtype,
            "predicted_car_5d": prediction.predicted_car_5d,
            "threshold": CAR_FILTER_THRESHOLD,
            "confidence": prediction.confidence,
            "sample_count": prediction.sample_count,
        })
    else:
        audit("EVENT_CAR_PASSED", actor="car_filter", details={
            "ticker": ticker,
            "event_type": event_type,
            "decision": decision.value,
            "predicted_car_5d": prediction.predicted_car_5d,
            "confidence": prediction.confidence,
        })

    return result


def _apply_filter_logic(prediction: CARPrediction) -> tuple[FilterDecision, str]:
    """Apply the CAR filter decision logic.

    REQ-FILTER-03-2: Three-way decision based on predicted CAR and confidence.
    """
    # Low confidence: conservative pass-through
    if prediction.confidence <= CONFIDENCE_THRESHOLD:
        return (
            FilterDecision.PASS_LOW_CONFIDENCE,
            f"Low confidence ({prediction.confidence:.2f} <= {CONFIDENCE_THRESHOLD}), "
            f"sample_count={prediction.sample_count}. Conservative pass-through.",
        )

    # High confidence: evaluate predicted CAR
    abs_car = abs(prediction.predicted_car_5d)
    if abs_car >= CAR_FILTER_THRESHOLD:
        direction = "positive" if prediction.predicted_car_5d > 0 else "negative"
        return (
            FilterDecision.PASS,
            f"|predicted_car_5d|={abs_car:.4f} >= threshold={CAR_FILTER_THRESHOLD}. "
            f"Event has material {direction} impact history.",
        )
    else:
        return (
            FilterDecision.BLOCK,
            f"|predicted_car_5d|={abs_car:.4f} < threshold={CAR_FILTER_THRESHOLD}. "
            f"Historical impact too small to justify Decision invocation.",
        )


def _build_car_context(
    prediction: CARPrediction,
    ticker: str,
    event_type: str,
    event_subtype: str | None,
) -> str:
    """Build CAR context string for injection into Decision persona input.

    REQ-FILTER-03-5: Context includes prediction, confidence, similar events.
    """
    direction = "positive" if prediction.predicted_car_5d > 0 else "negative"
    if abs(prediction.predicted_car_5d) < 0.005:
        direction = "neutral"

    subtype_str = f"/{event_subtype}" if event_subtype else ""
    lines = [
        "[Event-CAR Context]",
        f"Event: {event_type}{subtype_str} for {ticker}",
        f"Predicted 5-day CAR: {prediction.predicted_car_5d:+.2%} "
        f"(confidence: {prediction.confidence:.0%}, N={prediction.sample_count})",
    ]

    if prediction.similar_events:
        similar_strs = []
        for ev in prediction.similar_events[:3]:
            similar_strs.append(
                f"  - {ev['ticker']} ({ev['event_date']}): CAR={ev['car_5d']:+.2%}"
            )
        lines.append("Historical similar events:")
        lines.extend(similar_strs)

    lines.append(
        f"Interpretation: This event type historically leads to {direction} price impact"
    )
    return "\n".join(lines)


def _persist_filter_log(result: FilterResult) -> None:
    """Persist filter decision to event_filter_log table (REQ-FILTER-03-6)."""
    sql = """
        INSERT INTO event_filter_log
            (ticker, event_type, event_subtype, event_magnitude,
             predicted_car_5d, confidence, sample_count, threshold, decision, reason)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
    """
    try:
        with connection() as conn, conn.cursor() as cur:
            cur.execute(sql, (
                result.ticker,
                result.event_type,
                result.event_subtype,
                result.event_magnitude,
                result.predicted_car_5d,
                result.confidence,
                result.sample_count,
                result.threshold,
                result.decision.value,
                result.reason,
            ))
    except Exception as e:
        LOG.warning("Failed to persist event_filter_log: %s", e)
