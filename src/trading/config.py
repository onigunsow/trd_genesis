"""Configuration loader and risk-limit constants for SPEC-TRADING-001.

Loads secrets from .env via pydantic-settings. Risk limits are hard-coded constants
that the circuit breaker (REQ-RISK-05-1, REQ-RISK-05-2) enforces independently of
any persona decision.

Implements REQ-INFRA-01-2 (.env loading) and REQ-INFRA-01-5 (paper/live mode
separation via TRADING_MODE).
"""

from __future__ import annotations

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
RISK_DAILY_MAX_LOSS: Final[float] = -0.01           # -1.0%
RISK_PER_TICKER_MAX_POSITION: Final[float] = 0.20    # 20.0%
RISK_TOTAL_INVESTED_MAX: Final[float] = 0.80         # 80.0%
RISK_SINGLE_ORDER_MAX: Final[float] = 0.10           # 10.0%
RISK_DAILY_ORDER_COUNT_MAX: Final[int] = 10

# REQ-INFRA-01-3 — Healthcheck SLA
HEALTHCHECK_TIMEOUT_SECONDS: Final[int] = 60

# REQ-BRIEF-04-8 — Telegram briefing SLA
TELEGRAM_BRIEFING_SLA_SECONDS: Final[int] = 5

# REQ-EVENT-04-6 — Event trigger SLA (decision invocation)
EVENT_TRIGGER_SLA_SECONDS: Final[int] = 60

# REQ-DATA-03-4 — Backfill epoch for OHLCV
BACKFILL_START_DATE: Final[str] = "2019-01-01"

# REQ-KIS-02-2 — Token cache reuse window
KIS_TOKEN_CACHE_WINDOW_SECONDS: Final[int] = 60

# ──────────────────────────────────────────────────────────────────────────
# 한국투자증권 + 한국 주식시장 매매 비용 모델 (2026-05 기준)
#
# - 모의(paper) 환경은 KIS가 수수료 0으로 시뮬한다.
# - 실전(live) 환경은 비대면 일반 수수료 + 거래세 + (KOSPI 한정) 농어촌특별세.
# - 양도소득세는 소액주주(연 25억 미만 단일종목 보유)에 해당 없음 — 박세훈 님 무관.
# ──────────────────────────────────────────────────────────────────────────

# 매수 수수료 (양 모드 매수 시 적용)
PAPER_FEE_BUY: Final[float] = 0.0
LIVE_FEE_BUY: Final[float] = 0.00015        # 0.015%

# 매도 수수료 + 거래세 + 농특세 (양 모드 매도 시)
# 모의는 KIS가 0으로 시뮬하나, 실거래 진입 시 즉시 체감 가능.
# KOSPI 매도: 0.015% + 0.18% + 0.15% = 0.345% ≈ 0.0035
# KOSDAQ 매도: 0.015% + 0.18%        = 0.195% ≈ 0.002
PAPER_FEE_SELL_KOSPI: Final[float] = 0.0
PAPER_FEE_SELL_KOSDAQ: Final[float] = 0.0
LIVE_FEE_SELL_KOSPI: Final[float] = 0.00345
LIVE_FEE_SELL_KOSDAQ: Final[float] = 0.00195

# 시장가 슬리피지 가정 (백테스트와 실거래 평가 기준 일치)
SLIPPAGE_BPS: Final[float] = 0.0005          # 0.05%

# 매수→매도 1사이클 비용 (KOSPI live 기준): 0.015 + 0.345 = 0.36%
# → "+0.5% 이상 평가익이어야 0.14% 순익 보장"  (페르소나 익절 룰의 근거)
LIVE_ROUND_TRIP_COST_KOSPI: Final[float] = LIVE_FEE_BUY + LIVE_FEE_SELL_KOSPI  # ≈ 0.0036
LIVE_ROUND_TRIP_COST_KOSDAQ: Final[float] = LIVE_FEE_BUY + LIVE_FEE_SELL_KOSDAQ  # ≈ 0.0021


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
