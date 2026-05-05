"""Pydantic models for Event-CAR subsystem.

REQ-CARPRED-02-3: CARPrediction output model.
REQ-FILTER-03-1: FilterDecision enum.
"""

from __future__ import annotations

from datetime import date, datetime
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


class FilterDecision(str, Enum):
    """Smart Event Filter decision outcomes."""

    PASS = "PASS"
    BLOCK = "BLOCK"
    PASS_LOW_CONFIDENCE = "PASS_LOW_CONFIDENCE"


class EventCARRecord(BaseModel):
    """A single historical event-CAR observation."""

    ticker: str
    event_type: str
    event_subtype: str | None = None
    event_date: date
    event_magnitude: float | None = None
    car_1d: float | None = None
    car_5d: float | None = None
    car_10d: float | None = None
    benchmark_return_1d: float | None = None
    benchmark_return_5d: float | None = None
    benchmark_return_10d: float | None = None
    volume_ratio: float | None = None


class CARPrediction(BaseModel):
    """Predicted CAR for a new event based on historical similar events.

    REQ-CARPRED-02-3: Output model for CAR prediction engine.
    """

    event_type: str
    event_subtype: str | None = None
    ticker: str
    predicted_car_1d: float = 0.0
    predicted_car_5d: float = 0.0
    predicted_car_10d: float = 0.0
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    sample_count: int = 0
    similar_events: list[dict[str, Any]] = Field(default_factory=list)


class FilterResult(BaseModel):
    """Result of the Smart Event Filter evaluation."""

    ticker: str
    event_type: str
    event_subtype: str | None = None
    event_magnitude: float | None = None
    decision: FilterDecision
    predicted_car_5d: float | None = None
    confidence: float = 0.0
    sample_count: int = 0
    threshold: float = 0.015
    reason: str = ""
    car_context: str | None = None
