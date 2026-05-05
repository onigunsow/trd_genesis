"""JIT Pipeline Manager — orchestrates start/stop of delta event sources.

REQ-DELTA-01-4: Start at market open (09:00 KST).
REQ-DELTA-01-5: Stop at market close (15:30 KST).
REQ-DELTA-01-10: Reject start on non-market days/hours.
"""

from __future__ import annotations

import logging
from datetime import datetime

import pytz

from trading.db.session import audit, connection, get_system_state
from trading.jit.dart_poller import DartPoller
from trading.jit.events import get_delta_count_today
from trading.jit.news_poller import NewsPoller
from trading.jit.websocket import KisWebSocketManager
from trading.scheduler.calendar import is_trading_day

LOG = logging.getLogger(__name__)

KST = pytz.timezone("Asia/Seoul")
MARKET_OPEN_HOUR: int = 9
MARKET_OPEN_MINUTE: int = 0
MARKET_CLOSE_HOUR: int = 15
MARKET_CLOSE_MINUTE: int = 30


class JitPipelineManager:
    """Manages the full JIT delta event pipeline lifecycle.

    Coordinates WebSocket, DART poller, and news poller based on
    market hours and feature flags.
    """

    def __init__(self) -> None:
        self._ws_manager: KisWebSocketManager | None = None
        self._dart_poller: DartPoller | None = None
        self._news_poller: NewsPoller | None = None
        self._active = False

    @property
    def active(self) -> bool:
        return self._active

    def start(self, tickers: list[str] | None = None) -> bool:
        """Start the JIT pipeline if conditions are met.

        REQ-DELTA-01-10: Rejects start on non-market days/hours.

        Args:
            tickers: Stock codes to monitor. If None, reads from positions/watchlist.

        Returns:
            True if started successfully, False if rejected.
        """
        # Check market day
        if not is_trading_day():
            LOG.warning("JIT pipeline rejected: non-trading day")
            audit(
                "DELTA_PIPELINE_REJECTED_NON_MARKET",
                actor="jit_pipeline",
                details={"reason": "non_trading_day"},
            )
            return False

        # Check market hours
        now = datetime.now(KST)
        if not self._is_market_hours(now):
            LOG.warning("JIT pipeline rejected: outside market hours (%s)", now.strftime("%H:%M"))
            audit(
                "DELTA_PIPELINE_REJECTED_NON_MARKET",
                actor="jit_pipeline",
                details={"reason": "outside_market_hours", "time": now.isoformat()},
            )
            return False

        # Check feature flags
        state = get_system_state()
        if not state.get("jit_pipeline_enabled", False):
            LOG.info("JIT pipeline not enabled (jit_pipeline_enabled=false)")
            return False

        if self._active:
            LOG.info("JIT pipeline already active")
            return True

        # Resolve tickers
        if not tickers:
            tickers = self._resolve_tickers()

        # Start sub-components based on feature flags
        if state.get("jit_websocket_enabled", False):
            self._start_websocket(tickers, state)

        if state.get("jit_dart_polling_enabled", False):
            self._start_dart(tickers)

        if state.get("jit_news_polling_enabled", False):
            self._start_news(tickers)

        self._active = True
        audit(
            "DELTA_PIPELINE_STARTED",
            actor="jit_pipeline",
            details={
                "tickers": len(tickers),
                "ws_enabled": state.get("jit_websocket_enabled", False),
                "dart_enabled": state.get("jit_dart_polling_enabled", False),
                "news_enabled": state.get("jit_news_polling_enabled", False),
            },
        )
        LOG.info("JIT pipeline started with %d tickers", len(tickers))
        return True

    def stop(self) -> None:
        """Stop the JIT pipeline gracefully.

        REQ-DELTA-01-5: Disconnect WebSocket, stop polling, write audit.
        """
        if not self._active:
            return

        # Stop sub-components
        if self._ws_manager:
            self._ws_manager.stop()
            self._ws_manager = None

        if self._dart_poller:
            self._dart_poller.stop()
            self._dart_poller = None

        if self._news_poller:
            self._news_poller.stop()
            self._news_poller = None

        # Write day summary
        counts = get_delta_count_today()
        audit(
            "DELTA_PIPELINE_STOPPED",
            actor="jit_pipeline",
            details={
                "day_summary": counts,
                "total_events": sum(counts.values()),
            },
        )

        self._active = False
        LOG.info("JIT pipeline stopped. Day summary: %s", counts)

    def stop_websocket(self) -> None:
        """Stop only WebSocket (granular control via /jit ws off)."""
        if self._ws_manager:
            self._ws_manager.stop()
            self._ws_manager = None
            LOG.info("WebSocket stopped (pipeline continues)")

    def stop_dart(self) -> None:
        """Stop only DART polling."""
        if self._dart_poller:
            self._dart_poller.stop()
            self._dart_poller = None
            LOG.info("DART poller stopped (pipeline continues)")

    def stop_news(self) -> None:
        """Stop only news polling."""
        if self._news_poller:
            self._news_poller.stop()
            self._news_poller = None
            LOG.info("News poller stopped (pipeline continues)")

    def _start_websocket(self, tickers: list[str], state: dict) -> None:
        """Initialize and start KIS WebSocket connection."""
        try:
            from trading.config import get_settings
            settings = get_settings()
            mode = str(settings.trading_mode.value)
            if mode == "paper":
                app_key = settings.kis.paper_app_key.get_secret_value()
                app_secret = settings.kis.paper_app_secret.get_secret_value()
            else:
                app_key = settings.kis.live_app_key.get_secret_value()
                app_secret = settings.kis.live_app_secret.get_secret_value()

            self._ws_manager = KisWebSocketManager(
                mode=mode,
                app_key=app_key,
                app_secret=app_secret,
            )
            self._ws_manager.start(tickers)
        except Exception:
            LOG.exception("Failed to start WebSocket")

    def _start_dart(self, tickers: list[str]) -> None:
        """Start DART disclosure polling."""
        self._dart_poller = DartPoller(tickers=tickers)
        self._dart_poller.start()

    def _start_news(self, tickers: list[str]) -> None:
        """Start news RSS polling."""
        self._news_poller = NewsPoller(tickers=tickers)
        self._news_poller.start()

    def _resolve_tickers(self) -> list[str]:
        """Get ticker list from positions + watchlist."""
        tickers: list[str] = []
        try:
            with connection() as conn, conn.cursor() as cur:
                # Active positions
                cur.execute("SELECT DISTINCT ticker FROM positions WHERE shares > 0")
                for row in cur.fetchall():
                    tickers.append(row["ticker"])

                # Watchlist (if table exists)
                try:
                    cur.execute(
                        "SELECT DISTINCT ticker FROM persona_decisions "
                        "WHERE ts::date = CURRENT_DATE AND side = 'buy'"
                    )
                    for row in cur.fetchall():
                        if row["ticker"] not in tickers:
                            tickers.append(row["ticker"])
                except Exception:
                    pass  # Table may not exist yet
        except Exception:
            LOG.warning("Failed to resolve tickers for JIT pipeline")

        return tickers[:40]  # KIS WebSocket limit

    @staticmethod
    def _is_market_hours(now: datetime) -> bool:
        """Check if current time is within KRX market hours (09:00-15:30)."""
        market_open = now.replace(hour=MARKET_OPEN_HOUR, minute=MARKET_OPEN_MINUTE, second=0)
        market_close = now.replace(hour=MARKET_CLOSE_HOUR, minute=MARKET_CLOSE_MINUTE, second=0)
        return market_open <= now <= market_close


# Module-level singleton
_pipeline: JitPipelineManager | None = None


def get_pipeline() -> JitPipelineManager:
    """Return the singleton JIT pipeline manager."""
    global _pipeline
    if _pipeline is None:
        _pipeline = JitPipelineManager()
    return _pipeline
