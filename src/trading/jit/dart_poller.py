"""DART disclosure polling — 5-minute interval during market hours.

REQ-DELTA-01-5: Start/stop with market hours.
REQ-DELTA-01-8: New disclosures become delta_events with material event triggers.
"""

from __future__ import annotations

import logging
import threading
import time
from datetime import datetime
from typing import Any, Callable

from trading.db.session import audit, connection
from trading.jit.events import insert_delta
from trading.jit.merge import invalidate_cache
from trading.jit.models import DeltaEvent

LOG = logging.getLogger(__name__)

# REQ-DELTA-01-4: Poll every 5 minutes during market hours
DART_POLL_INTERVAL_S: int = 300  # 5 minutes

# Material disclosure types that trigger event signals
MATERIAL_REPORT_TYPES: set[str] = {
    "major_event",
    "earnings",
    "governance",
}


class DartPoller:
    """Polls DART API for new disclosures at regular intervals.

    REQ-DELTA-01-5: Active only during market hours.
    REQ-DELTA-01-8: Material disclosures emit event trigger.
    """

    def __init__(
        self,
        tickers: list[str] | None = None,
        on_material_disclosure: Callable[[DeltaEvent], None] | None = None,
    ) -> None:
        self._tickers = tickers or []
        self._on_material = on_material_disclosure
        self._thread: threading.Thread | None = None
        self._running = False
        self._last_seen_ids: set[str] = set()

    @property
    def running(self) -> bool:
        return self._running

    def start(self, tickers: list[str] | None = None) -> None:
        """Start DART polling in background thread."""
        if self._running:
            return
        if tickers:
            self._tickers = tickers
        self._running = True
        self._thread = threading.Thread(
            target=self._poll_loop,
            name="dart-poller",
            daemon=True,
        )
        self._thread.start()
        LOG.info("DART poller started for %d tickers", len(self._tickers))

    def stop(self) -> None:
        """Stop DART polling."""
        self._running = False
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=10)
        LOG.info("DART poller stopped")

    def _poll_loop(self) -> None:
        """Main polling loop — fetch disclosures every 5 minutes."""
        while self._running:
            try:
                self._poll_once()
            except Exception:
                LOG.exception("DART poll cycle failed")

            # Wait for next interval (check running flag every second)
            for _ in range(DART_POLL_INTERVAL_S):
                if not self._running:
                    return
                time.sleep(1)

    def _poll_once(self) -> None:
        """Execute a single DART poll cycle."""
        if not self._tickers:
            return

        disclosures = self._fetch_disclosures()
        new_count = 0

        for disc in disclosures:
            disc_id = disc.get("rcept_no", "")
            if disc_id in self._last_seen_ids:
                continue

            self._last_seen_ids.add(disc_id)
            ticker = disc.get("stock_code", "")
            if not ticker or ticker not in self._tickers:
                continue

            # Create delta event
            event = DeltaEvent(
                event_type="disclosure",
                source="dart_api",
                ticker=ticker,
                payload={
                    "ticker": ticker,
                    "title": disc.get("report_nm", ""),
                    "report_type": disc.get("report_type", ""),
                    "url": disc.get("url", ""),
                    "filing_date": disc.get("rcept_dt", ""),
                    "summary": disc.get("report_nm", "")[:200],
                    "rcept_no": disc_id,
                },
                event_ts=datetime.now(),
            )

            insert_delta(event)
            new_count += 1
            invalidate_cache("micro")

            # Check if material disclosure
            report_type = disc.get("report_type", "")
            if report_type in MATERIAL_REPORT_TYPES and self._on_material:
                self._on_material(event)

        if new_count > 0:
            audit(
                "DART_POLL_COMPLETED",
                actor="jit_dart",
                details={"new_disclosures": new_count, "tickers_monitored": len(self._tickers)},
            )
            LOG.info("DART poll: %d new disclosures", new_count)

    def _fetch_disclosures(self) -> list[dict[str, Any]]:
        """Fetch recent disclosures from DART API for monitored tickers.

        Reuses existing dart_adapter pattern.
        """
        try:
            from trading.data.dart_adapter import fetch_recent_disclosures
            # Query today's disclosures for all monitored tickers
            return fetch_recent_disclosures(self._tickers, days=1)
        except ImportError:
            LOG.debug("dart_adapter not available — DART polling disabled")
            return []
        except Exception:
            LOG.exception("DART API fetch failed")
            return []
