"""Tests for dynamic risk exposure ceiling calculation."""

from __future__ import annotations

from trading.prototypes.exposure import (
    STATIC_LIMIT_PCT,
    compute_ceiling,
    get_risk_advisory,
)


class TestComputeCeiling:
    """Test exposure ceiling computation logic."""

    def test_no_matches_returns_none(self):
        assert compute_ceiling([]) is None

    def test_all_below_threshold(self):
        matches = [
            {"name": "2024-08-crash", "category": "crash", "similarity": 0.70},
            {"name": "2022-rate-hike", "category": "correction", "similarity": 0.65},
        ]
        assert compute_ceiling(matches) is None

    def test_crash_high_similarity_30pct(self):
        matches = [
            {"name": "2020-03-covid", "category": "crash", "similarity": 0.88},
        ]
        assert compute_ceiling(matches) == 30.0

    def test_crash_medium_similarity_50pct(self):
        matches = [
            {"name": "2024-08-crash", "category": "crash", "similarity": 0.82},
        ]
        assert compute_ceiling(matches) == 50.0

    def test_correction_at_threshold_60pct(self):
        matches = [
            {"name": "2022-09-credit", "category": "correction", "similarity": 0.76},
        ]
        assert compute_ceiling(matches) == 60.0

    def test_rally_does_not_tighten(self):
        """Rally prototypes do NOT reduce ceiling (advisory only)."""
        matches = [
            {"name": "2024-11-rally", "category": "rally", "similarity": 0.87},
        ]
        # Rally does not add to restrictive ceilings
        assert compute_ceiling(matches) is None

    def test_multiple_matches_most_restrictive(self):
        """REQ-DYNRISK-04-4: Use most restrictive among all >= 0.75."""
        matches = [
            {"name": "2024-08-crash", "category": "crash", "similarity": 0.81},  # 50%
            {"name": "2022-09-credit", "category": "correction", "similarity": 0.77},  # 60%
            {"name": "2024-11-rally", "category": "rally", "similarity": 0.76},  # advisory
        ]
        # Most restrictive: 50% (crash at 0.81)
        assert compute_ceiling(matches) == 50.0

    def test_ceiling_never_exceeds_static(self):
        """Dynamic ceiling capped at static limit (80%)."""
        # This shouldn't happen with current rules but test the guard
        matches = [
            {"name": "test", "category": "correction", "similarity": 0.75},
        ]
        ceiling = compute_ceiling(matches)
        assert ceiling is not None
        assert ceiling <= STATIC_LIMIT_PCT


class TestGetRiskAdvisory:
    """Test risk advisory text generation."""

    def test_advisory_with_significant_match(self):
        matches = [
            {
                "name": "2024-08-crash",
                "category": "crash",
                "similarity": 0.82,
                "ceiling_pct": 50,
                "risk_recommendation": {
                    "reasoning": "Yen carry trade unwind pattern",
                    "max_exposure_pct": 50,
                },
            },
        ]
        advisory = get_risk_advisory(matches)
        assert advisory["has_significant_match"] is True
        assert advisory["applied_ceiling_pct"] == 50.0
        assert "ProtoHedge" in advisory["text"]
        assert "2024-08-crash" in advisory["text"]

    def test_advisory_no_significant_match(self):
        matches = [
            {
                "name": "2024-04-sideways",
                "category": "sideways",
                "similarity": 0.55,
                "ceiling_pct": None,
                "risk_recommendation": {},
            },
        ]
        advisory = get_risk_advisory(matches)
        assert advisory["has_significant_match"] is False
        assert advisory["applied_ceiling_pct"] is None

    def test_advisory_empty_matches(self):
        advisory = get_risk_advisory([])
        assert advisory["has_significant_match"] is False
        assert advisory["applied_ceiling_pct"] is None
        assert advisory["top_matches"] == []
