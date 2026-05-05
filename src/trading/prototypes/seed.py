"""Initial prototype seed data — 10 historical market scenarios.

REQ-PROTO-03-6: 10 prototypes covering major Korean market events 2020-2024.
REQ-PROTO-03-7: Embedding generation from concatenated description + indicators.
"""

from __future__ import annotations

import logging
from datetime import date
from typing import Any

LOG = logging.getLogger(__name__)

# 10 initial market prototypes (REQ-PROTO-03-6)
SEED_PROTOTYPES: list[dict[str, Any]] = [
    {
        "name": "2024-08-crash",
        "description": (
            "August 2024 global equity crash triggered by Bank of Japan rate hike "
            "leading to Yen carry trade unwind. KOSPI dropped 11.3% in two weeks. "
            "Foreign investors net sold for 10+ consecutive days. VIX spiked above 30. "
            "Tech and growth sectors hit hardest, utilities and healthcare outperformed."
        ),
        "category": "crash",
        "time_period_start": date(2024, 8, 1),
        "time_period_end": date(2024, 8, 15),
        "market_conditions": {
            "kospi_change_pct": -11.3,
            "kosdaq_change_pct": -15.2,
            "vix_level": 38.5,
            "usd_krw": 1380,
            "foreign_net_sell_days": 10,
            "market_breadth_pct": 15,
            "sector_rotation": "defensive",
            "volume_ratio_vs_20d": 2.8,
        },
        "key_indicators": {
            "trigger_event": "BOJ rate hike -> Yen carry trade unwind -> global selloff",
            "leading_signals": ["VIX > 30", "USD/KRW > 1350", "Foreign net sell > 5 days"],
            "duration_days": 12,
            "max_drawdown_pct": -11.3,
            "recovery_days": 45,
            "affected_sectors": ["tech", "growth", "semiconductors"],
            "safe_sectors": ["utilities", "healthcare", "telecom"],
        },
        "outcome": {
            "max_drawdown_pct": -11.3,
            "recovery_days": 45,
            "bottom_date": "2024-08-05",
            "lesson": "Leverage unwind crashes are sharp but short. V-recovery if fundamentals intact.",
        },
        "risk_recommendation": {
            "max_exposure_pct": 40,
            "max_single_stock_pct": 10,
            "avoid_sectors": ["tech", "growth", "semiconductors"],
            "prefer_sectors": ["utilities", "healthcare"],
            "reduce_position_if_held": True,
            "reasoning": "Yen carry trade unwind pattern + foreign net sell. Reduce tech, increase cash.",
        },
    },
    {
        "name": "2024-11-rally",
        "description": (
            "November-December 2024 rally driven by US election results (Trump). "
            "Deregulation and tariff expectations boosted exporters and semiconductors. "
            "KOSPI gained 8.5% selectively. Strong foreign buying in semis and autos. "
            "VIX dropped below 15. Domestic-focused stocks hurt by tariff fears."
        ),
        "category": "rally",
        "time_period_start": date(2024, 11, 1),
        "time_period_end": date(2024, 12, 15),
        "market_conditions": {
            "kospi_change_pct": 8.5,
            "kosdaq_change_pct": 3.2,
            "vix_level": 13.5,
            "usd_krw": 1310,
            "foreign_net_sell_days": -8,
            "market_breadth_pct": 65,
            "sector_rotation": "cyclical",
            "volume_ratio_vs_20d": 1.5,
        },
        "key_indicators": {
            "trigger_event": "Trump election win -> deregulation + tariff expectations",
            "leading_signals": ["VIX < 15", "Foreign net buy semis/autos", "USD/KRW stable"],
            "duration_days": 45,
            "max_drawdown_pct": 0,
            "recovery_days": 0,
            "affected_sectors": ["semiconductors", "auto", "defense"],
            "safe_sectors": [],
        },
        "outcome": {
            "max_gain_pct": 8.5,
            "duration_days": 45,
            "lesson": "Sector rotation matters more than market direction in election rallies.",
        },
        "risk_recommendation": {
            "max_exposure_pct": 90,
            "max_single_stock_pct": 20,
            "avoid_sectors": ["domestic_consumer", "small_cap"],
            "prefer_sectors": ["semiconductors", "auto", "defense"],
            "reduce_position_if_held": False,
            "reasoning": "Election rally with sector rotation. Increase exposure to export leaders.",
        },
    },
    {
        "name": "2020-03-covid-crash",
        "description": (
            "February-March 2020 global COVID-19 pandemic crash. "
            "KOSPI dropped 35% in 5 weeks as lockdowns spread globally. "
            "Circuit breakers triggered multiple times. VIX exceeded 60. "
            "All asset correlations approached 1.0. Foreign selling massive and indiscriminate."
        ),
        "category": "crash",
        "time_period_start": date(2020, 2, 20),
        "time_period_end": date(2020, 3, 23),
        "market_conditions": {
            "kospi_change_pct": -35.0,
            "kosdaq_change_pct": -30.0,
            "vix_level": 65.0,
            "usd_krw": 1290,
            "foreign_net_sell_days": 25,
            "market_breadth_pct": 5,
            "sector_rotation": "none_all_selling",
            "volume_ratio_vs_20d": 4.0,
        },
        "key_indicators": {
            "trigger_event": "COVID-19 global pandemic + lockdown announcements",
            "leading_signals": ["VIX > 50", "Circuit breaker triggered", "Global correlation 1.0"],
            "duration_days": 33,
            "max_drawdown_pct": -35.0,
            "recovery_days": 180,
            "affected_sectors": ["all"],
            "safe_sectors": ["pharma", "online_platforms"],
        },
        "outcome": {
            "max_drawdown_pct": -35.0,
            "recovery_days": 180,
            "bottom_date": "2020-03-19",
            "lesson": "Extreme crash - cash is king. V-recovery opportunities massive for patient investors.",
        },
        "risk_recommendation": {
            "max_exposure_pct": 20,
            "max_single_stock_pct": 5,
            "avoid_sectors": ["travel", "retail", "entertainment"],
            "prefer_sectors": ["pharma", "online"],
            "reduce_position_if_held": True,
            "reasoning": "Extreme pandemic crash. Maximum defensive posture. Preserve capital.",
        },
    },
    {
        "name": "2020-04-covid-recovery",
        "description": (
            "March-June 2020 V-shaped recovery from COVID crash bottom. "
            "Driven by massive global stimulus (Fed, BOK rate cuts, fiscal packages). "
            "KOSPI recovered to pre-COVID levels within 6 months. "
            "Retail investors entered market aggressively (donghakmimi movement)."
        ),
        "category": "recovery",
        "time_period_start": date(2020, 3, 24),
        "time_period_end": date(2020, 6, 30),
        "market_conditions": {
            "kospi_change_pct": 45.0,
            "kosdaq_change_pct": 55.0,
            "vix_level": 25.0,
            "usd_krw": 1210,
            "foreign_net_sell_days": -5,
            "market_breadth_pct": 75,
            "sector_rotation": "growth",
            "volume_ratio_vs_20d": 2.0,
        },
        "key_indicators": {
            "trigger_event": "Massive global stimulus + BOK rate cut",
            "leading_signals": ["VIX declining from peak", "Retail inflow surge", "Stimulus announced"],
            "duration_days": 98,
            "max_drawdown_pct": 0,
            "recovery_days": 0,
            "affected_sectors": ["tech", "bio", "EV"],
            "safe_sectors": [],
        },
        "outcome": {
            "max_gain_pct": 45.0,
            "duration_days": 98,
            "lesson": "Post-crash stimulus recovery is powerful. Growth stocks lead.",
        },
        "risk_recommendation": {
            "max_exposure_pct": 80,
            "max_single_stock_pct": 15,
            "avoid_sectors": [],
            "prefer_sectors": ["tech", "bio", "EV", "battery"],
            "reduce_position_if_held": False,
            "reasoning": "Post-crash recovery with stimulus support. Increase exposure to growth.",
        },
    },
    {
        "name": "2022-rate-hike-bear",
        "description": (
            "January-October 2022 bear market driven by aggressive Fed rate hikes. "
            "Growth stocks sold off heavily. KOSPI fell 25% over 10 months. "
            "USD/KRW breached 1400 for first time since 2009. "
            "Gradual grind-down unlike sharp crash, making timing difficult."
        ),
        "category": "correction",
        "time_period_start": date(2022, 1, 1),
        "time_period_end": date(2022, 10, 15),
        "market_conditions": {
            "kospi_change_pct": -25.0,
            "kosdaq_change_pct": -35.0,
            "vix_level": 32.0,
            "usd_krw": 1430,
            "foreign_net_sell_days": 15,
            "market_breadth_pct": 20,
            "sector_rotation": "value",
            "volume_ratio_vs_20d": 1.2,
        },
        "key_indicators": {
            "trigger_event": "Fed aggressive rate hike cycle (25bp -> 75bp increments)",
            "leading_signals": ["USD/KRW > 1350", "Growth PE compression", "10Y-2Y inversion"],
            "duration_days": 288,
            "max_drawdown_pct": -25.0,
            "recovery_days": 365,
            "affected_sectors": ["growth", "tech", "bio"],
            "safe_sectors": ["value", "financials", "energy"],
        },
        "outcome": {
            "max_drawdown_pct": -25.0,
            "duration_days": 288,
            "lesson": "Rate hike bears are slow and grinding. Value outperforms growth.",
        },
        "risk_recommendation": {
            "max_exposure_pct": 50,
            "max_single_stock_pct": 10,
            "avoid_sectors": ["growth", "tech", "bio"],
            "prefer_sectors": ["value", "financials", "energy"],
            "reduce_position_if_held": True,
            "reasoning": "Rate hike environment favors value. Reduce growth exposure gradually.",
        },
    },
    {
        "name": "2023-ai-rally",
        "description": (
            "January-July 2023 AI hype rally led by NVIDIA effect. "
            "Korean semiconductor and AI-related stocks surged. "
            "SK Hynix +80%, Samsung +25%. KOSPI gained 18% but concentrated in few stocks. "
            "Narrow breadth rally - top 10 stocks drove 90% of gains."
        ),
        "category": "rally",
        "time_period_start": date(2023, 1, 1),
        "time_period_end": date(2023, 7, 31),
        "market_conditions": {
            "kospi_change_pct": 18.0,
            "kosdaq_change_pct": 30.0,
            "vix_level": 14.0,
            "usd_krw": 1280,
            "foreign_net_sell_days": -10,
            "market_breadth_pct": 40,
            "sector_rotation": "tech_concentrated",
            "volume_ratio_vs_20d": 1.3,
        },
        "key_indicators": {
            "trigger_event": "ChatGPT/NVIDIA AI hype -> semiconductor demand expectations",
            "leading_signals": ["NVIDIA earnings beat", "HBM demand surge", "Narrow breadth"],
            "duration_days": 210,
            "max_drawdown_pct": -5.0,
            "recovery_days": 10,
            "affected_sectors": ["semiconductors", "AI", "HBM"],
            "safe_sectors": [],
        },
        "outcome": {
            "max_gain_pct": 18.0,
            "duration_days": 210,
            "lesson": "Theme rallies are concentrated. Position in leaders, not laggards.",
        },
        "risk_recommendation": {
            "max_exposure_pct": 85,
            "max_single_stock_pct": 20,
            "avoid_sectors": ["traditional_manufacturing"],
            "prefer_sectors": ["semiconductors", "AI", "HBM"],
            "reduce_position_if_held": False,
            "reasoning": "AI theme rally concentrated in semis. Focus exposure on leaders.",
        },
    },
    {
        "name": "2024-04-sideways",
        "description": (
            "April-June 2024 range-bound sideways market. "
            "KOSPI traded in 2550-2700 range for 3 months. "
            "Low volatility, VIX around 13-15. No clear catalysts. "
            "Earnings season mixed. Best strategy: individual stock picking within range."
        ),
        "category": "sideways",
        "time_period_start": date(2024, 4, 1),
        "time_period_end": date(2024, 6, 30),
        "market_conditions": {
            "kospi_change_pct": 1.5,
            "kosdaq_change_pct": -2.0,
            "vix_level": 14.0,
            "usd_krw": 1340,
            "foreign_net_sell_days": 3,
            "market_breadth_pct": 50,
            "sector_rotation": "mixed",
            "volume_ratio_vs_20d": 0.8,
        },
        "key_indicators": {
            "trigger_event": "No major catalyst - range trading",
            "leading_signals": ["VIX 13-15 stable", "Volume below average", "Range-bound index"],
            "duration_days": 90,
            "max_drawdown_pct": -3.0,
            "recovery_days": 5,
            "affected_sectors": [],
            "safe_sectors": [],
        },
        "outcome": {
            "max_gain_pct": 3.0,
            "max_drawdown_pct": -3.0,
            "duration_days": 90,
            "lesson": "Sideways markets reward stock picking and mean reversion strategies.",
        },
        "risk_recommendation": {
            "max_exposure_pct": 70,
            "max_single_stock_pct": 15,
            "avoid_sectors": [],
            "prefer_sectors": [],
            "reduce_position_if_held": False,
            "reasoning": "Sideways market. Normal exposure with stock-picking focus.",
        },
    },
    {
        "name": "2022-09-credit-crisis",
        "description": (
            "September-November 2022 Korean credit crisis (Legoland/Heungkuk Life). "
            "Short-term money market froze. CP/bond spreads spiked. "
            "Construction, real estate stocks crashed. BOK emergency liquidity injection. "
            "KOSPI fell 10% in 6 weeks with financial sector leading the decline."
        ),
        "category": "correction",
        "time_period_start": date(2022, 9, 1),
        "time_period_end": date(2022, 11, 30),
        "market_conditions": {
            "kospi_change_pct": -10.0,
            "kosdaq_change_pct": -15.0,
            "vix_level": 30.0,
            "usd_krw": 1440,
            "foreign_net_sell_days": 12,
            "market_breadth_pct": 25,
            "sector_rotation": "defensive",
            "volume_ratio_vs_20d": 1.8,
        },
        "key_indicators": {
            "trigger_event": "Legoland ABCP default -> credit market freeze",
            "leading_signals": ["Credit spreads spike", "Construction stock crash", "BOK intervention"],
            "duration_days": 90,
            "max_drawdown_pct": -10.0,
            "recovery_days": 60,
            "affected_sectors": ["construction", "real_estate", "financials"],
            "safe_sectors": ["tech", "semiconductors"],
        },
        "outcome": {
            "max_drawdown_pct": -10.0,
            "recovery_days": 60,
            "lesson": "Korean credit events are local and resolve with BOK intervention.",
        },
        "risk_recommendation": {
            "max_exposure_pct": 50,
            "max_single_stock_pct": 10,
            "avoid_sectors": ["construction", "real_estate", "financials"],
            "prefer_sectors": ["tech", "semiconductors"],
            "reduce_position_if_held": True,
            "reasoning": "Credit crisis - avoid financials and real estate. Tech relatively safe.",
        },
    },
    {
        "name": "2021-11-peak",
        "description": (
            "November 2021 - January 2022 KOSPI peak and rotation. "
            "KOSPI reached all-time high ~3300 then started decline. "
            "Retail investors began exiting. Foreign selling accelerated. "
            "Market shifted from growth/momentum to value/dividend."
        ),
        "category": "correction",
        "time_period_start": date(2021, 11, 1),
        "time_period_end": date(2022, 1, 31),
        "market_conditions": {
            "kospi_change_pct": -12.0,
            "kosdaq_change_pct": -18.0,
            "vix_level": 28.0,
            "usd_krw": 1200,
            "foreign_net_sell_days": 15,
            "market_breadth_pct": 30,
            "sector_rotation": "value_rotation",
            "volume_ratio_vs_20d": 1.5,
        },
        "key_indicators": {
            "trigger_event": "KOSPI all-time high exhaustion + retail exit + rate hike start",
            "leading_signals": ["Retail net selling", "IPO market cooling", "Growth PE at extremes"],
            "duration_days": 90,
            "max_drawdown_pct": -12.0,
            "recovery_days": 0,
            "affected_sectors": ["growth", "IPO", "meme_stocks"],
            "safe_sectors": ["value", "dividend", "financials"],
        },
        "outcome": {
            "max_drawdown_pct": -12.0,
            "duration_days": 90,
            "lesson": "Market peaks are distribution phases. Rotate from growth to value early.",
        },
        "risk_recommendation": {
            "max_exposure_pct": 60,
            "max_single_stock_pct": 12,
            "avoid_sectors": ["growth", "IPO", "meme"],
            "prefer_sectors": ["value", "dividend", "financials"],
            "reduce_position_if_held": True,
            "reasoning": "Market peak rotation. Reduce growth positions, shift to value/dividend.",
        },
    },
    {
        "name": "2024-01-value-rotation",
        "description": (
            "January-March 2024 value rotation driven by Corporate Value-Up program. "
            "Government announced shareholder-friendly corporate governance reforms. "
            "Financial and undervalued large-caps rallied 15-30%. "
            "KOSPI gained 6% but concentrated in low-PBR value stocks."
        ),
        "category": "rally",
        "time_period_start": date(2024, 1, 1),
        "time_period_end": date(2024, 3, 31),
        "market_conditions": {
            "kospi_change_pct": 6.0,
            "kosdaq_change_pct": -3.0,
            "vix_level": 14.0,
            "usd_krw": 1320,
            "foreign_net_sell_days": -5,
            "market_breadth_pct": 55,
            "sector_rotation": "value",
            "volume_ratio_vs_20d": 1.2,
        },
        "key_indicators": {
            "trigger_event": "Korean Corporate Value-Up program announcement",
            "leading_signals": ["Low-PBR stocks rally", "Financial sector outperformance", "Government policy"],
            "duration_days": 90,
            "max_drawdown_pct": -2.0,
            "recovery_days": 5,
            "affected_sectors": ["financials", "holding_companies", "utilities"],
            "safe_sectors": [],
        },
        "outcome": {
            "max_gain_pct": 6.0,
            "duration_days": 90,
            "lesson": "Policy-driven value rotation favors low-PBR stocks with governance improvements.",
        },
        "risk_recommendation": {
            "max_exposure_pct": 80,
            "max_single_stock_pct": 15,
            "avoid_sectors": ["high_growth_no_dividend"],
            "prefer_sectors": ["financials", "holding_companies", "utilities"],
            "reduce_position_if_held": False,
            "reasoning": "Value rotation on policy support. Normal exposure with value tilt.",
        },
    },
]


def seed_prototypes() -> int:
    """Seed the initial 10 prototypes with embeddings.

    REQ-PROTO-03-6: Initial prototype library seeding.
    REQ-PROTO-03-7: Generate embeddings from description + indicators.

    Returns:
        Number of prototypes successfully seeded.
    """
    from trading.prototypes.library import add_prototype, get_prototype_by_name

    seeded = 0
    for proto in SEED_PROTOTYPES:
        # Skip if already exists
        if get_prototype_by_name(proto["name"]):
            LOG.info("Prototype already exists: %s", proto["name"])
            continue

        # Generate embedding from description + indicators
        embedding = _generate_prototype_embedding(proto)
        if not embedding:
            LOG.warning("Failed to generate embedding for %s — using zero vector", proto["name"])
            embedding = [0.0] * 1024

        result = add_prototype(
            name=proto["name"],
            description=proto["description"],
            category=proto["category"],
            time_period_start=proto["time_period_start"],
            time_period_end=proto["time_period_end"],
            market_conditions=proto["market_conditions"],
            key_indicators=proto["key_indicators"],
            outcome=proto["outcome"],
            risk_recommendation=proto["risk_recommendation"],
            embedding=embedding,
            source="manual",
            is_active=True,
        )
        if result:
            seeded += 1

    LOG.info("Prototype seeding complete: %d/%d seeded", seeded, len(SEED_PROTOTYPES))
    return seeded


def _generate_prototype_embedding(proto: dict[str, Any]) -> list[float] | None:
    """Generate embedding from prototype text representation.

    REQ-PROTO-03-7: Concatenate description + category + indicators + conditions.
    """
    import json

    text = (
        f"Market prototype: {proto['name']}\n"
        f"Category: {proto['category']}\n"
        f"Description: {proto['description']}\n"
        f"Key indicators: {json.dumps(proto['key_indicators'])}\n"
        f"Market conditions: {json.dumps(proto['market_conditions'])}"
    )

    try:
        from trading.embeddings.embedder import generate_embeddings
        results = generate_embeddings([text])
        if results and len(results) > 0:
            return results[0]
    except ImportError:
        LOG.debug("embeddings module not available for prototype seeding")
    except Exception:
        LOG.exception("Failed to generate prototype embedding for %s", proto["name"])

    return None
