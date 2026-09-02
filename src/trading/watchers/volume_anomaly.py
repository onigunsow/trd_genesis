"""SPEC-TRADING-024 REQ-024-3 — Volume + volatility anomaly watcher.

For each target ticker, fire `volume_anomaly` when:

  today_volume / avg_20d_volume >= volume_ratio_min  (default 2.0)
  AND
  atr_today / atr_20d_median   >= atr_ratio_min      (default 1.5)

Both conditions must hold simultaneously. Throttling shares the same
`TickerThrottle` instance as price_threshold (300s cooldown, 20/day cap).

Stage 2 (REQ-024-8 multi-tier dispatch) will swap the lightweight handler
for a tier-aware dispatcher; for Stage 1 we re-use the existing intraday
cycle via `event_handler.handle_trigger_event`.

@MX:SPEC: SPEC-TRADING-024
"""

from __future__ import annotations

import json
import logging
from datetime import date
from typing import Any

LOG = logging.getLogger(__name__)

# Stage 1 defaults — overridable via scheduler.yaml.
DEFAULT_VOLUME_RATIO_MIN: float = 2.0
DEFAULT_ATR_RATIO_MIN: float = 1.5


def _get_target_tickers() -> list[str]:
    """Same target set as price_threshold (holdings, dynamic, micro)."""
    from trading.watchers.price_threshold import _get_target_tickers as _ttl

    return _ttl()


def _get_shared_throttle():
    from trading.watchers.price_threshold import _get_shared_throttle as _stl

    return _stl()


def _fire_trigger_event(ticker: str, trigger_type: str, metadata: dict[str, Any]) -> None:
    from trading.watchers.event_handler import handle_trigger_event

    handle_trigger_event(ticker, trigger_type, metadata)


def _get_volume_volatility_stats(ticker: str) -> dict[str, Any] | None:
    """Pull today + 20-day-window OHLCV stats for `ticker`.

    Returns a dict with today_volume, avg_20d_volume, atr_today,
    atr_20d_median, or None when insufficient data.
    """
    try:
        from trading.db.session import connection
    except Exception:
        return None

    sql = """
        SELECT ts, open, high, low, close, volume
          FROM ohlcv
         WHERE symbol = %s
         ORDER BY ts DESC
         LIMIT 22
    """
    try:
        with connection() as conn, conn.cursor() as cur:
            cur.execute(sql, (ticker,))
            rows = list(cur.fetchall())
    except Exception as exc:
        LOG.warning("volume_anomaly: DB error for %s: %s", ticker, exc)
        return None

    if len(rows) < 5:
        return None

    rows.reverse()  # chronological
    today = rows[-1]
    history = rows[:-1]

    try:
        today_volume = float(today["volume"] or 0)
        prior_volumes = [float(r["volume"] or 0) for r in history if r["volume"]]
        if not prior_volumes:
            return None
        avg_20d_volume = sum(prior_volumes) / len(prior_volumes)

        # Daily True Range proxy (high-low) for ATR-today
        atr_today = float(today["high"]) - float(today["low"])
        prior_ranges = sorted(
            [float(r["high"]) - float(r["low"]) for r in history if r["high"] and r["low"]]
        )
        if not prior_ranges:
            return None
        mid = len(prior_ranges) // 2
        if len(prior_ranges) % 2:
            atr_20d_median = prior_ranges[mid]
        else:
            atr_20d_median = (prior_ranges[mid - 1] + prior_ranges[mid]) / 2.0
    except Exception as exc:
        LOG.warning("volume_anomaly: stats compute failed for %s: %s", ticker, exc)
        return None

    # bar_date: 비교 대상이 된 '마지막 완성 일봉'의 날짜. ohlcv 는 장 마감 후
    # 갱신되므로 장중에는 이게 대체로 전 거래일이다 — 벽시계 날짜가 아니다.
    bar_ts = today.get("ts") if isinstance(today, dict) else None
    bar_date = bar_ts.date().isoformat() if hasattr(bar_ts, "date") else str(bar_ts)

    return {
        "today_volume": today_volume,
        "avg_20d_volume": avg_20d_volume,
        "atr_today": atr_today,
        "atr_20d_median": atr_20d_median,
        "bar_date": bar_date,
    }


def _already_fired_for_bar(ticker: str, bar_date: str) -> bool:
    """같은 일봉으로 이미 발사했으면 True (레벨 트리거 → 엣지 트리거).

    거래량·변동성 비율은 '마지막 완성 일봉' 하나로 계산되므로 그 봉이 바뀌기
    전까지 값이 상수다. 조건이 한 번 참이면 쿨다운(300초)마다 재발사돼
    일일 상한(20회)까지 같은 신호로 전체 사이클(LLM)을 반복 기동한다.
    2026-09-02 실측: 096770 한 종목이 20회 발사, 그중 9회는 정규 사이클과
    충돌해 CYCLE_SKIPPED_IN_FLIGHT 로 버려졌다.

    조회 실패는 False — 발사를 막지 않는다(감시자를 침묵시키지 않는다).
    """
    from trading.db.session import connection

    sql = """
        SELECT 1 FROM trigger_events
         WHERE ticker = %s
           AND trigger_type = 'volume_anomaly'
           AND metadata->>'bar_date' = %s
         LIMIT 1
    """
    try:
        with connection() as conn, conn.cursor() as cur:
            cur.execute(sql, (ticker, bar_date))
            return cur.fetchone() is not None
    except Exception:
        LOG.warning("volume_anomaly: 중복 봉 조회 실패 — 발사 진행", exc_info=True)
        return False


# @MX:ANCHOR: SPEC-TRADING-024 REQ-024-3 entry-point for volume anomaly polling
# @MX:REASON: fan_in >= 3 (scheduler cron, manual smoke test, Stage 2 reuse)
# @MX:SPEC: SPEC-TRADING-024
def poll_volume_anomaly(
    volume_ratio_min: float = DEFAULT_VOLUME_RATIO_MIN,
    atr_ratio_min: float = DEFAULT_ATR_RATIO_MIN,
) -> dict[str, Any]:
    """Single poll iteration. Returns metrics dict for observability."""
    metrics = {
        "checked": 0,
        "fired": 0,
        "skipped_no_stats": 0,
        "throttled": 0,
        "dup_bar": 0,
    }
    throttle = _get_shared_throttle()
    for ticker in _get_target_tickers():
        metrics["checked"] += 1

        stats = _get_volume_volatility_stats(ticker)
        if stats is None:
            metrics["skipped_no_stats"] += 1
            continue

        try:
            avg_20d = float(stats["avg_20d_volume"])
            atr_median = float(stats["atr_20d_median"])
            if avg_20d <= 0 or atr_median <= 0:
                metrics["skipped_no_stats"] += 1
                continue
            volume_ratio = float(stats["today_volume"]) / avg_20d
            atr_ratio = float(stats["atr_today"]) / atr_median
        except (KeyError, TypeError, ZeroDivisionError):
            metrics["skipped_no_stats"] += 1
            continue

        if volume_ratio < volume_ratio_min or atr_ratio < atr_ratio_min:
            continue

        # 엣지 트리거: 같은 일봉으론 한 번만. 쿨다운은 조건이 상수인 한
        # 재발사를 늦출 뿐 막지 못한다 — 봉이 바뀌어야 새 신호다.
        bar_date = str(stats.get("bar_date") or "")
        if bar_date and _already_fired_for_bar(ticker, bar_date):
            metrics["dup_bar"] += 1
            continue

        if not throttle.can_fire(ticker):
            metrics["throttled"] += 1
            continue

        throttle.record(ticker)
        metadata = {
            "today_volume": stats["today_volume"],
            "avg_20d_volume": avg_20d,
            "atr_today": stats["atr_today"],
            "atr_20d_median": atr_median,
            "volume_ratio": volume_ratio,
            "atr_ratio": atr_ratio,
            "bar_date": bar_date,
            # as_of 는 발사 시각(벽시계)이다. 지표를 만든 봉은 bar_date 쪽이고,
            # 장중에는 둘이 다르다 — 종전엔 as_of 만 있어 같은 날로 오독됐다.
            "as_of": date.today().isoformat(),
        }
        _fire_trigger_event(ticker, "volume_anomaly", metadata)
        metrics["fired"] += 1
        LOG.info(
            "volume_anomaly fired ticker=%s vol_ratio=%.2f atr_ratio=%.2f",
            ticker,
            volume_ratio,
            atr_ratio,
        )

    if metrics["checked"]:
        LOG.info("volume_anomaly poll: %s", json.dumps(metrics))
    return metrics
