"""KRX trading-day calendar.

Korean holidays via the `holidays` package + weekend detection. Additionally:
- 12/31 (KRX 연말 폐장) is treated as a non-trading day every year.
- Optional override via DB table `trading_calendar` (future use, not implemented yet).
"""

from __future__ import annotations

import logging
from datetime import date
from functools import lru_cache

import holidays

LOG = logging.getLogger(__name__)


@lru_cache(maxsize=1)
def _kr_holidays() -> holidays.HolidayBase:
    # holidays.KR() supports lazy year resolution.
    return holidays.country_holidays("KR")


def is_trading_day(d: date | None = None) -> bool:
    """Return True if KRX정규장 holds on this day.

    Rules:
    - Mon-Fri only
    - Skip Korean public holidays (어린이날, 부처님오신날, 추석, 설날 등)
    - Skip 12/31 (KRX 연말 폐장)
    """
    d = d or date.today()
    if d.weekday() >= 5:                      # Sat/Sun
        return False
    if d in _kr_holidays():                   # public holiday
        return False
    if d.month == 12 and d.day == 31:         # KRX year-end close
        return False
    return True


def reason_if_closed(d: date | None = None) -> str | None:
    """Return a human-readable reason if `d` is closed, else None."""
    d = d or date.today()
    if d.weekday() == 5:
        return "토요일"
    if d.weekday() == 6:
        return "일요일"
    name = _kr_holidays().get(d)
    if name:
        return f"공휴일 ({name})"
    if d.month == 12 and d.day == 31:
        return "연말 폐장 (12/31)"
    return None
