"""Configuration loader and risk-limit constants for SPEC-TRADING-001.

Loads secrets from .env via pydantic-settings. Risk limits are hard-coded constants
that the circuit breaker (REQ-RISK-05-1, REQ-RISK-05-2) enforces independently of
any persona decision.

Implements REQ-INFRA-01-2 (.env loading) and REQ-INFRA-01-5 (paper/live mode
separation via TRADING_MODE).
"""

from __future__ import annotations

import dataclasses
import os
from enum import Enum
from functools import lru_cache
from pathlib import Path
from typing import Final

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class TradingMode(str, Enum):
    PAPER = "paper"
    LIVE = "live"


# REQ-RISK-05-1 — Five hard limits, expressed as fractions of capital.
# These are NOT modified by any persona output. The circuit breaker
# (src/trading/risk/limits.py, M5) enforces them before every order.
#
# SPEC-TRADING-038 REQ-038-1: the daily-loss limit is widened -1.0% -> -2.5%
# (risk owner decision) so normal multi-position intraday swings — bounded by the
# SPEC-037 per-position stop floor (-10%) — are not misread as a real daily loss.
# It is a persona-invariant hard limit, so it stays a module constant; the env
# fallback only lets the operator re-tune the floor without a code change. A real
# loss still trips and stays NON-auto-resumable (SPEC-032, keyed on the
# "daily_loss" breach prefix, independent of this value).
RISK_DAILY_MAX_LOSS: Final[float] = float(os.getenv("RISK_DAILY_MAX_LOSS", "-0.025"))  # -2.5%
RISK_PER_TICKER_MAX_POSITION: Final[float] = 0.20    # 20.0%
RISK_TOTAL_INVESTED_MAX: Final[float] = 0.80         # 80.0%
RISK_SINGLE_ORDER_MAX: Final[float] = 0.10           # 10.0%
RISK_DAILY_ORDER_COUNT_MAX: Final[int] = 10

# SPEC-TRADING-040 M2 (REQ-040-2): single-ticker concentration cap. When a held
# ticker exceeds this fraction of the total portfolio value the position watchdog
# auto-trims (code-enforced — the decision persona effectively never sells). The
# normal cap is wider than RISK_PER_TICKER_MAX_POSITION (the buy-side entry cap,
# 20%): the buy cap stops a *new* over-weight entry while this cap *unwinds* an
# already-accumulated over-weight. Under late-cycle defence the cap tightens to
# RISK_CONCENTRATION_CAP_LATE_CYCLE so trims align with the defence (synergy).
RISK_CONCENTRATION_CAP_PCT: Final[float] = float(
    os.getenv("RISK_CONCENTRATION_CAP_PCT", "0.25")
)  # 25%
RISK_CONCENTRATION_CAP_LATE_CYCLE_PCT: Final[float] = float(
    os.getenv("RISK_CONCENTRATION_CAP_LATE_CYCLE_PCT", "0.20")
)  # 20% when late-cycle defence is active

# SPEC-TRADING-040 M3 (REQ-040-3): daily_count sell-budget reserve K. Buys are
# capped at RISK_DAILY_ORDER_COUNT_MAX - K so K order slots are always reserved
# for risk-reducing exits — buys can never starve a pending sell before a halt
# trips. Sells are excluded from the count entirely (never blocked, never
# increment), so this reserve is a *preventive* belt to SPEC-037's post-halt
# SELL bypass (the suspenders).
RISK_SELL_BUDGET_RESERVE: Final[int] = int(
    os.getenv("RISK_SELL_BUDGET_RESERVE", "2")
)

# SPEC-TRADING-040 M1c (REQ-040-1c): stagnation-rotation trim thresholds. A
# holding parked for STAGNATION_DAYS+ with a flat P&L (|pnl| < band) and a
# neutral RSI is rotated out (partial trim). Risk/rebalance-motivated → EV-exempt
# (SPEC ADR-1). Distinct from the extreme stop/take rules.
STAGNATION_DAYS: Final[int] = int(os.getenv("STAGNATION_DAYS", "20"))
STAGNATION_PNL_BAND_PCT: Final[float] = float(
    os.getenv("STAGNATION_PNL_BAND_PCT", "3.0")
)
STAGNATION_RSI_LOW: Final[float] = float(os.getenv("STAGNATION_RSI_LOW", "40.0"))
STAGNATION_RSI_HIGH: Final[float] = float(os.getenv("STAGNATION_RSI_HIGH", "60.0"))
# Fraction of the position rotated out when stagnant (partial trim, not full).
STAGNATION_TRIM_FRACTION: Final[float] = float(
    os.getenv("STAGNATION_TRIM_FRACTION", "0.5")
)

# REQ-INFRA-01-3 — Healthcheck SLA
HEALTHCHECK_TIMEOUT_SECONDS: Final[int] = 60

# REQ-BRIEF-04-8 — Telegram briefing SLA
TELEGRAM_BRIEFING_SLA_SECONDS: Final[int] = 5

# REQ-EVENT-04-6 — Event trigger SLA (decision invocation)
EVENT_TRIGGER_SLA_SECONDS: Final[int] = 60

# REQ-DATA-03-4 — Backfill epoch for OHLCV
BACKFILL_START_DATE: Final[str] = "2019-01-01"

# ──────────────────────────────────────────────────────────────────────────
# SPEC-TRADING-012 — Event-CAR + Dynamic Thresholds constants
# Fixed rules remain as ultimate fallback (REQ-MIGR-07-5).
# ──────────────────────────────────────────────────────────────────────────
FIXED_STOP_LOSS_PCT: Final[float] = -7.0
FIXED_TAKE_PROFIT_RSI: Final[int] = 85

# REQ-KIS-02-2 — Token cache reuse window
KIS_TOKEN_CACHE_WINDOW_SECONDS: Final[int] = 60

# ──────────────────────────────────────────────────────────────────────────
# 한국투자증권 + 한국 주식시장 매매 비용 모델 (SPEC-TRADING-044 M1)
#
# 단일 진실원천(Single Source of Truth):
#   - 아래 명명 컴포넌트에서 모든 소비자(analytics, exit_sweep, scorecard, walk-forward)가
#     LIVE_ROUND_TRIP_COST_KOSPI/_KOSDAQ 를 읽는다.
#   - 세율 변경 시 컴포넌트 상수 한 줄만 수정하면 됨.
#
# 2026 증권거래세 개편 (유효일: 2026-01-01, SPEC-TRADING-044 Q-C1 확정):
#   - KOSPI: 거래세 0.18% → 0.05% 인하. 농특세 0.15% 유지.
#             매도 합계 = 0.015%(수수료) + 0.05%(거래세) + 0.15%(농특세) = 0.215%
#             기존 0.345%에서 인하 → 비용 모델이 LESS pessimistic (SPEC-040 주의사항 참조)
#   - KOSDAQ: 거래세 0.18% → 0.20% (농특세 없음).
#              매도 합계 = 0.015%(수수료) + 0.20%(거래세)              = 0.215%
#
# - 모의(paper) 환경은 KIS가 수수료 0으로 시뮬한다.
# - 양도소득세는 소액주주(연 25억 미만 단일종목 보유)에 해당 없음.
# ──────────────────────────────────────────────────────────────────────────

# @MX:ANCHOR: [AUTO] config.py 비용 단일소스 — 모든 소비자가 이 블록 파생값을 읽는다.
# @MX:REASON: SPEC-TRADING-044 REQ-044-C5: 세율 변경은 이 블록 한 줄 수정으로 끝난다.

# 브로커 수수료 (매수/매도 공통, 한국투자증권 비대면 일반)
KOSPI_BROKER_FEE: Final[float] = 0.00015    # 0.015% — KOSDAQ 와 동일

# KOSPI 거래세/농특세 (2026-01-01 개편 반영)
KOSPI_TX_TAX: Final[float] = 0.0005         # 거래세 0.05% (2026 인하, 구 0.18%)
KOSPI_RURAL_TAX: Final[float] = 0.0015      # 농어촌특별세 0.15% (변경 없음)

# KOSDAQ 거래세 (농특세 없음, 2026-01-01 기준)
KOSDAQ_TX_TAX: Final[float] = 0.002         # 거래세 0.20%

# 매수 수수료 (양 모드 매수 시 적용)
PAPER_FEE_BUY: Final[float] = 0.0
LIVE_FEE_BUY: Final[float] = KOSPI_BROKER_FEE  # 0.015%

# 매도 수수료 합계 (수수료 + 거래세 + 농특세, 2026 개편 반영)
# 모의는 KIS가 0으로 시뮬하나, 실거래 진입 시 즉시 체감 가능.
# KOSPI 매도: 0.015%(수수료) + 0.05%(거래세) + 0.15%(농특세) = 0.215%
# KOSDAQ 매도: 0.015%(수수료) + 0.20%(거래세)                = 0.215%
PAPER_FEE_SELL_KOSPI: Final[float] = 0.0
PAPER_FEE_SELL_KOSDAQ: Final[float] = 0.0
LIVE_FEE_SELL_KOSPI: Final[float] = KOSPI_BROKER_FEE + KOSPI_TX_TAX + KOSPI_RURAL_TAX  # 0.00215
LIVE_FEE_SELL_KOSDAQ: Final[float] = KOSPI_BROKER_FEE + KOSDAQ_TX_TAX                  # 0.00215

# 시장가 슬리피지 가정 (백테스트와 실거래 평가 기준 일치)
SLIPPAGE_BPS: Final[float] = 0.0005          # 0.05%

# 매수→매도 1사이클 비용 (단일 진실원천 파생):
#   KOSPI: 0.015%(매수) + 0.215%(매도) = 0.23% ≈ 0.0023  (구 ≈0.0036 에서 인하)
#   KOSDAQ: 0.015%(매수) + 0.215%(매도) = 0.23% ≈ 0.0023  (구 ≈0.0021 에서 소폭 인상)
# ※ SPEC-040 주의: KOSPI round-trip 인하로 익절 floor/GO-NO-GO 게이트 재튜닝 필요 (별도 결정)
LIVE_ROUND_TRIP_COST_KOSPI: Final[float] = LIVE_FEE_BUY + LIVE_FEE_SELL_KOSPI   # ≈ 0.0023
LIVE_ROUND_TRIP_COST_KOSDAQ: Final[float] = LIVE_FEE_BUY + LIVE_FEE_SELL_KOSDAQ  # ≈ 0.0023


# ──────────────────────────────────────────────────────────────────────────
# SPEC-TRADING-046 — 결정적 사이징 파라미터 단일 외부 진실원천 (REQ-046-C)
#
# SizingParams 는 SPEC-044 walk_forward 하니스가 그리드 스윕할 수 있도록
# 타입이 명시된 dataclass 로 정의한다. 운영자는 코드 수정 없이 env var 로
# 기본값을 재정의할 수 있다.
#
# 기본값 근거 (ADR-HYBRID-LLM-SIGNAL-001 Option C + spec.md §6):
#   vol_target_per_trade = 0.01  — 1-ATR 역풍이 자산의 1% 를 초과하지 않도록
#                                   (포지션당 변동성 예산 1%)
#   atr_lookback = 14            — 기존 ATR_PERIOD 재사용 (REQ-046-A5, plan.md)
#   fallback_fraction = 0.02     — ATR 부재 시 자산의 2% 고정 notional
#                                   (단건 상한 10% 보다 충분히 보수적)
#   confidence_damp_enabled = False  — [HARD] 기본 OFF (REQ-046-B2, Spearman -0.455)
# ──────────────────────────────────────────────────────────────────────────

# @MX:ANCHOR: [AUTO] SizingParams — SPEC-046 사이징 파라미터 단일진실원천.
# @MX:REASON: SPEC-TRADING-046 REQ-046-C1/C2: walk_forward 그리드 스윕 진입점.
#   파라미터 변경은 이 dataclass 한 곳에서만 한다.

@dataclasses.dataclass
class SizingParams:
    """결정적 사이징 파라미터 (SPEC-TRADING-046 REQ-046-C).

    SPEC-044 walk_forward 하니스가 이 구조체를 그리드 스윕한다.
    env var 로 기본값 재정의 가능 (코드 수정 불필요).
    """

    # 포지션당 변동성 예산: 1-ATR 역풍이 자산의 이 비율을 초과하지 않도록
    vol_target_per_trade: float = dataclasses.field(
        default_factory=lambda: float(os.getenv("SIZING_VOL_TARGET_PER_TRADE", "0.01"))
    )
    # ATR lookback 윈도 (일) — 기존 ATR_PERIOD=14 재사용
    atr_lookback: int = dataclasses.field(
        default_factory=lambda: int(os.getenv("SIZING_ATR_LOOKBACK", "14"))
    )
    # ATR 부재 시 보수 고정 분율 (단건 상한 10% 미만)
    fallback_fraction: float = dataclasses.field(
        default_factory=lambda: float(os.getenv("SIZING_FALLBACK_FRACTION", "0.02"))
    )
    # confidence 하향 damp 활성화 여부 -- [HARD] 기본 OFF (REQ-046-B2)
    confidence_damp_enabled: bool = dataclasses.field(
        default_factory=lambda: (
            os.getenv("SIZING_CONFIDENCE_DAMP_ENABLED", "false").lower() == "true"
        )
    )
    # SPEC-TRADING-048 REQ-048-M1-4: 포트폴리오 heat 상한 (기본 0.08).
    # env var 로 재정의 가능. 주입형 파라미터 — KRX 상수 하드코딩 금지.
    heat_cap: float = dataclasses.field(
        default_factory=lambda: float(os.getenv("SIZING_HEAT_CAP", "0.08"))
    )
    # SPEC-TRADING-048 REQ-048-M1-1: half-Kelly sell_tax_rate 주입 (KRX 기본 0).
    # 호출자가 KRX 거래세율을 주입한다. 코어는 0 허용.
    kelly_sell_tax_rate: float = dataclasses.field(
        default_factory=lambda: float(os.getenv("SIZING_KELLY_SELL_TAX_RATE", "0.0"))
    )


# SPEC-TRADING-046 REQ-046-E1: sizing_mode feature flag.
# 기본값 'llm_direct' — 현재 동작 byte-for-byte 보존.
# 'deterministic' 으로 전환 시 결정적 사이징 모듈이 qty 를 산출.
SIZING_MODE: Final[str] = os.getenv("SIZING_MODE", "llm_direct")


def estimate_fee(*, mode: str, side: str, market: str, notional: int) -> int:
    """Return estimated fee+tax in KRW for a given order.

    mode: 'paper' | 'live'
    side: 'buy' | 'sell'
    market: 'KOSPI' | 'KOSDAQ' (case-insensitive)
    notional: order amount in KRW
    """
    mkt = (market or "KOSPI").upper()
    if mode == "paper":
        if side == "buy":
            rate = PAPER_FEE_BUY
        else:
            rate = PAPER_FEE_SELL_KOSPI if mkt == "KOSPI" else PAPER_FEE_SELL_KOSDAQ
    else:
        if side == "buy":
            rate = LIVE_FEE_BUY
        else:
            rate = LIVE_FEE_SELL_KOSPI if mkt == "KOSPI" else LIVE_FEE_SELL_KOSDAQ
    return int(round(notional * rate))


class KisSecrets(BaseSettings):
    """KIS Developers API credentials. Two parallel sets: paper and live."""

    model_config = SettingsConfigDict(extra="ignore", env_file=None)

    paper_app_key: SecretStr = Field(alias="KIS_PAPER_APP_KEY")
    paper_app_secret: SecretStr = Field(alias="KIS_PAPER_APP_SECRET")
    paper_account: str = Field(alias="KIS_PAPER_ACCOUNT")

    live_app_key: SecretStr = Field(alias="KIS_LIVE_APP_KEY")
    live_app_secret: SecretStr = Field(alias="KIS_LIVE_APP_SECRET")
    live_account: str = Field(alias="KIS_LIVE_ACCOUNT")


class TelegramSecrets(BaseSettings):
    """Telegram bot for trading briefing channel (@sehoon_trd_bot)."""

    model_config = SettingsConfigDict(extra="ignore", env_file=None)

    bot_token: SecretStr = Field(alias="TELEGRAM_BOT_TOKEN_TRADING")
    chat_id: str = Field(alias="TELEGRAM_CHAT_ID")


class DataApiSecrets(BaseSettings):
    """External market-data APIs (M3+). DART is optional."""

    model_config = SettingsConfigDict(extra="ignore", env_file=None)

    fred_api_key: SecretStr | None = Field(default=None, alias="FRED_API_KEY")
    ecos_api_key: SecretStr | None = Field(default=None, alias="ECOS_API_KEY")
    dart_api_key: SecretStr | None = Field(default=None, alias="DART_API_KEY")
    # SPEC-TRADING-036 REQ-036-1(c): KRX OpenAPI key for the V-KOSPI (파생상품
    # 지수) fetch. Optional — when missing the fetcher returns (unavailable)
    # gracefully and never crashes the macro context build.
    krx_openapi_key: SecretStr | None = Field(default=None, alias="KRX_OPENAPI_KEY")


class AnthropicSecrets(BaseSettings):
    """Anthropic API for persona system (M4+)."""

    model_config = SettingsConfigDict(extra="ignore", env_file=None)

    api_key: SecretStr | None = Field(default=None, alias="ANTHROPIC_API_KEY")


class PostgresSecrets(BaseSettings):
    """Postgres credentials. Auto-generated by setup."""

    model_config = SettingsConfigDict(extra="ignore", env_file=None)

    user: str = Field(alias="POSTGRES_USER")
    password: SecretStr = Field(alias="POSTGRES_PASSWORD")
    database: str = Field(alias="POSTGRES_DB")
    # In-container DSN (postgres service name on trading-net).
    dsn_in_container: str = Field(
        default="",
        validation_alias="DATABASE_URL",
    )


class Settings(BaseSettings):
    """Top-level settings. All secrets loaded from .env via env_file."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    trading_mode: TradingMode = Field(default=TradingMode.PAPER, alias="TRADING_MODE")

    @property
    def kis(self) -> KisSecrets:
        return KisSecrets()  # type: ignore[call-arg]

    @property
    def telegram(self) -> TelegramSecrets:
        return TelegramSecrets()  # type: ignore[call-arg]

    @property
    def data_apis(self) -> DataApiSecrets:
        return DataApiSecrets()  # type: ignore[call-arg]

    @property
    def anthropic(self) -> AnthropicSecrets:
        return AnthropicSecrets()  # type: ignore[call-arg]

    @property
    def postgres(self) -> PostgresSecrets:
        return PostgresSecrets()  # type: ignore[call-arg]


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return cached top-level settings instance."""
    return Settings()  # type: ignore[call-arg]


def project_root() -> Path:
    """Return the trading project root."""
    return Path(__file__).resolve().parent.parent.parent
