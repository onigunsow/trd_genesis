"""KIS WebSocket connection manager — real-time price updates.

REQ-DELTA-01-4: Connect at market open, subscribe to positions + watchlist.
REQ-DELTA-01-6: Auto-reconnect with exponential backoff, heartbeat, health monitoring.
REQ-DELTA-01-7: Parse messages, insert delta_events, update price cache.
"""

from __future__ import annotations

import json
import logging
import random
import threading
import time
from datetime import datetime
from typing import Any, Callable

from trading.db.session import audit
from trading.jit.events import insert_delta
from trading.jit.merge import invalidate_cache
from trading.jit.models import DeltaEvent

LOG = logging.getLogger(__name__)

# REQ-DELTA-01-6: Reconnection parameters
INITIAL_BACKOFF_S: float = 1.0
MAX_BACKOFF_S: float = 60.0
JITTER_MS: int = 500
MAX_RECONNECT_ATTEMPTS: int = 10
HEARTBEAT_INTERVAL_S: int = 30
NO_DATA_TIMEOUT_S: int = 60

# KIS WebSocket endpoints
KIS_WS_PAPER: str = "ws://ops.koreainvestment.com:21000"
KIS_WS_LIVE: str = "ws://ops.koreainvestment.com:31000"


class KisWebSocketManager:
    """Manages KIS WebSocket lifecycle — connect, subscribe, reconnect.

    Runs in a background thread (not a new container).
    REQ-DELTA-01-4: Subscribes to up to 40 tickers.
    """

    def __init__(
        self,
        mode: str = "paper",
        app_key: str = "",
        app_secret: str = "",
        on_price_update: Callable[[DeltaEvent], None] | None = None,
    ) -> None:
        self._mode = mode
        self._app_key = app_key
        self._app_secret = app_secret
        self._on_price_update = on_price_update

        self._ws: Any = None
        self._thread: threading.Thread | None = None
        self._running = False
        self._connected = False
        self._reconnect_count = 0
        self._last_data_time: float = 0
        self._subscribed_tickers: list[str] = []

        self._ws_url = KIS_WS_PAPER if mode == "paper" else KIS_WS_LIVE

    @property
    def connected(self) -> bool:
        return self._connected

    @property
    def reconnect_count(self) -> int:
        return self._reconnect_count

    def start(self, tickers: list[str]) -> None:
        """Start WebSocket connection in background thread.

        Args:
            tickers: KRX stock codes to subscribe (max 40).
        """
        if self._running:
            LOG.warning("WebSocket already running")
            return

        self._subscribed_tickers = tickers[:40]  # KIS limit
        self._running = True
        self._reconnect_count = 0
        self._thread = threading.Thread(
            target=self._run_loop,
            name="kis-websocket",
            daemon=True,
        )
        self._thread.start()
        LOG.info("KIS WebSocket thread started for %d tickers", len(self._subscribed_tickers))

    def stop(self) -> None:
        """Gracefully disconnect WebSocket.

        REQ-DELTA-01-5: Graceful disconnect at market close.
        """
        self._running = False
        if self._ws:
            try:
                self._ws.close()
            except Exception:
                pass
        self._connected = False
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=5)
        LOG.info("KIS WebSocket stopped")

    def _run_loop(self) -> None:
        """Main WebSocket loop with reconnection logic."""
        while self._running:
            try:
                self._connect()
                self._subscribe_all()
                self._listen()
            except Exception as e:
                LOG.warning("WebSocket error: %s", e)

            if not self._running:
                break

            # Reconnection with exponential backoff
            self._reconnect_count += 1
            if self._reconnect_count > MAX_RECONNECT_ATTEMPTS:
                LOG.error("WebSocket max reconnect attempts (%d) exceeded", MAX_RECONNECT_ATTEMPTS)
                audit(
                    "WEBSOCKET_MAX_RETRY",
                    actor="jit_websocket",
                    details={"attempts": self._reconnect_count},
                )
                self._running = False
                # Alert via Telegram
                try:
                    from trading.alerts.telegram import send_alert
                    send_alert(
                        "[JIT] WebSocket max reconnect exceeded. "
                        "Disabled for today. Falling back to static data."
                    )
                except Exception:
                    pass
                break

            backoff = min(
                INITIAL_BACKOFF_S * (2 ** (self._reconnect_count - 1)),
                MAX_BACKOFF_S,
            )
            jitter = random.uniform(-JITTER_MS / 1000, JITTER_MS / 1000)
            wait = backoff + jitter
            LOG.info(
                "WebSocket reconnecting in %.1fs (attempt %d/%d)",
                wait, self._reconnect_count, MAX_RECONNECT_ATTEMPTS,
            )
            audit(
                "WEBSOCKET_RECONNECT",
                actor="jit_websocket",
                details={"attempt": self._reconnect_count, "backoff_s": round(wait, 1)},
            )
            time.sleep(wait)

    def _connect(self) -> None:
        """Establish WebSocket connection to KIS."""
        try:
            import websockets.sync.client as ws_client
            self._ws = ws_client.connect(self._ws_url)
            self._connected = True
            self._last_data_time = time.time()
            audit("WEBSOCKET_CONNECTED", actor="jit_websocket", details={"url": self._ws_url})
            LOG.info("WebSocket connected to %s", self._ws_url)
        except ImportError:
            # Fallback: websockets may not be installed yet
            LOG.error("websockets package not installed — WebSocket disabled")
            self._running = False
            raise
        except Exception as e:
            self._connected = False
            audit("WEBSOCKET_DISCONNECTED", actor="jit_websocket", details={"error": str(e)})
            raise

    def _subscribe_all(self) -> None:
        """Subscribe to price updates for all configured tickers."""
        for ticker in self._subscribed_tickers:
            self._send_subscribe(ticker)
            time.sleep(0.1)  # Avoid flooding

    def _send_subscribe(self, ticker: str) -> None:
        """Send subscription message for a single ticker."""
        if not self._ws:
            return
        # KIS WebSocket subscription format
        msg = json.dumps({
            "header": {
                "appkey": self._app_key,
                "appsecret": self._app_secret,
                "tr_type": "1",  # subscribe
                "custtype": "P",
            },
            "body": {
                "input": {
                    "tr_id": "H0STCNT0",  # real-time price
                    "tr_key": ticker,
                }
            },
        })
        try:
            self._ws.send(msg)
        except Exception as e:
            LOG.warning("Failed to subscribe %s: %s", ticker, e)

    def _listen(self) -> None:
        """Listen for messages until disconnected or stopped."""
        while self._running and self._connected:
            # Health check: no data timeout
            if (time.time() - self._last_data_time) > NO_DATA_TIMEOUT_S:
                LOG.warning("No data for %ds — triggering reconnect", NO_DATA_TIMEOUT_S)
                self._connected = False
                break

            try:
                if not self._ws:
                    break
                msg = self._ws.recv(timeout=HEARTBEAT_INTERVAL_S)
                if msg:
                    self._last_data_time = time.time()
                    self._handle_message(msg)
            except TimeoutError:
                # Send heartbeat ping
                try:
                    if self._ws:
                        self._ws.ping()
                except Exception:
                    self._connected = False
                    break
            except Exception as e:
                LOG.warning("WebSocket recv error: %s", e)
                self._connected = False
                break

    def _handle_message(self, raw: str) -> None:
        """Parse KIS WebSocket message and create delta event.

        REQ-DELTA-01-7: Parse, insert delta_events, update price cache.
        """
        try:
            # KIS sends pipe-delimited data for real-time prices
            # Format varies; handle both JSON and pipe-delimited
            if raw.startswith("{"):
                data = json.loads(raw)
                # Subscription confirmation or system messages
                if data.get("header", {}).get("tr_id") == "PINGPONG":
                    return
                return

            # Pipe-delimited price data: "0|H0STCNT0|003|005930^78500^..."
            parts = raw.split("|")
            if len(parts) < 4:
                return

            tr_id = parts[1]
            if tr_id != "H0STCNT0":
                return

            # Parse the data section (KIS specific format)
            data_section = parts[3]
            fields = data_section.split("^")
            if len(fields) < 10:
                return

            ticker = fields[0]
            price = int(fields[2]) if fields[2] else 0
            volume = int(fields[8]) if len(fields) > 8 and fields[8] else 0
            change_pct = float(fields[5]) if len(fields) > 5 and fields[5] else 0.0
            high = int(fields[6]) if len(fields) > 6 and fields[6] else 0
            low = int(fields[7]) if len(fields) > 7 and fields[7] else 0

            event = DeltaEvent(
                event_type="price_update",
                source="kis_ws",
                ticker=ticker,
                payload={
                    "ticker": ticker,
                    "price": price,
                    "volume": volume,
                    "change_pct": change_pct,
                    "high": high,
                    "low": low,
                    "timestamp": datetime.now().isoformat(),
                },
                event_ts=datetime.now(),
            )

            # Persist to DB
            insert_delta(event)

            # Invalidate merge cache for fresh reads
            invalidate_cache("micro")

            # Callback for external consumers (e.g., event trigger check)
            if self._on_price_update:
                self._on_price_update(event)

        except Exception as e:
            LOG.debug("Failed to parse WebSocket message: %s", e)
