"""SPEC-TRADING-024 REQ-024-2 — ATR-based price-threshold watcher.

For each target ticker (holdings union dynamic_tickers union today's micro
candidates), compute today's ATR-derived threshold =
`atr_multiplier * atr_14 / close_price`
(default multiplier 1.5x, per resolved Open Question Q-4). If the KIS-reported
`change_pct` (signed, absolute value used) exceeds the threshold, fire a
`price_threshold` event.

Throttling: shared `TickerThrottle` (300s cooldown, 20/day cap shared across
all three Stage 1 watchers).

Event handling: delegates to `trading.watchers.event_handler.handle_trigger_event`
which invokes the standard `orchestrator.run_intraday_cycle`. Stage 1 does NOT
narrow the cycle to the specific ticker — Stage 2 will introduce multi-tier
dispatch.

@MX:SPEC: SPEC-TRADING-024
"""

from __future__ import annotations

import json
import logging
from typing import Any

from trading.strategy.volatility.atr import compute_atr
from trading.watchers.throttle import TickerThrottle

LOG = logging.getLogger(__name__)

# Stage 1 defaults — overridable via scheduler.yaml (resolved Q-4: ATR-based
# threshold, multiplier 1.5x).
DEFAULT_ATR_MULTIPLIER: float = 1.5
DEFAULT_COOLDOWN_SECONDS: int = 300
DEFAULT_DAILY_CAP: int = 20

# Module-level shared throttle. _get_shared_throttle() is the test seam.
_SHARED_THROTTLE: TickerThrottle | None = None


def _get_shared_throttle() -> TickerThrottle:
    """Return the process-global throttle (lazy-initialised)."""
    global _SHARED_THROTTLE
    if _SHARED_THROTTLE is None:
        _SHARED_THROTTLE = TickerThrottle(
            min_interval_sec=DEFAULT_COOLDOWN_SECONDS,
            daily_cap=DEFAULT_DAILY_CAP,
        )
    return _SHARED_THROTTLE


def _get_target_tickers() -> list[str]:
    """holdings union dynamic_tickers union today's micro buy candidates.

    Falls back gracefully when sources are unavailable so a stale watcher
    poll cannot crash the scheduler.
    """
    try:
        from trading.data.universe import _read_active_holdings, _read_dynamic_tickers
    except Exception:
        return []

    seen: set[str] = set()
    out: list[str] = []
    for src_fn, label in (
        (_read_active_holdings, "holdings"),
        (_read_dynamic_tickers, "dynamic_tickers"),
        (_read_micro_candidate_tickers, "micro_candidates"),
    ):
        try:
            for t in src_fn() or []:
                if isinstance(t, str) and t and t not in seen:
                    seen.add(t)
                    out.append(t)
        except Exception as exc:
            LOG.warning("price_threshold: source %s failed: %s", label, exc)
    return out


def _read_micro_candidate_tickers() -> list[str]:
    """Today's micro candidate tickers (buy list) from latest cached run."""
    try:
        from trading.personas import micro as micro_persona
    except Exception:
        return []
    try:
        row = micro_persona.latest_cached(max_age_days=1)
    except Exception as exc:
        LOG.warning("price_threshold: micro.latest_cached failed: %s", exc)
        return []
    if not row:
        return []
    response_json = row.get("response_json") or {}
    candidates = response_json.get("candidates", {}) or {}
    buy = candidates.get("buy") or []
    return [c.get("ticker") for c in buy if isinstance(c, dict) and c.get("ticker")]


def _get_kis_quote(ticker: str) -> dict[str, Any] | None:
    """Fetch current KIS quote for `ticker`; returns None on failure."""
    try:
        from trading.config import get_settings
        from trading.kis.client import KisClient
        from trading.kis.market import current_price

        s = get_settings()
        client = KisClient(s.trading_mode)
        return current_price(client, ticker)
    except Exception as exc:
        LOG.warning("price_threshold: KIS quote failed for %s: %s", ticker, exc)
        return None


def _fired_max_by_direction(ticker: str) -> dict[bool, float] | None:
    """오늘(KST) 이 종목의 방향별 최대 발사 변동률. {상승여부: 절대변동률 최대}.

    없거나 조회 실패면 None — 발사를 막지 않는다(감시자를 침묵시키지 않는다).
    ``volume_anomaly._already_fired_for_bar`` 와 같은 규약이다.

    ``MAX`` 를 쓰는 이유: ``price_change_pct`` 는 단조증가가 아니다(실측 9/09:
    10.94 -> 10.94 -> 10.72 -> 10.58 -> 11.52). 마지막 1건을 기준으로 삼으면 값이
    내려간 발사 뒤에 문턱도 함께 낮아져 재발사 문이 다시 열린다.

    방향별로 따로 센다. 같은 방향에서는 문턱이 단조 상승하고, 방향이 뒤집히면
    그 방향의 최대치와 비교한다 — +9% 와 -9% 를 오가며 매번 새 신호로 통과하는
    구멍을 막는다.

    구 행은 ``price_change_pct_signed`` 가 없다. 그 경우 절대값을 양수로 간주한다 —
    이행기에만 해당하고, 틀려도 방향 전환 1회를 더 허용할 뿐이다.

    없거나 조회 실패면 None — 발사를 막지 않는다.
    """
    from trading.db.session import connection

    # COALESCE: 구 행은 signed 키가 없으므로 절대값(양수)으로 본다.
    sql = """
        SELECT COALESCE((metadata->>'price_change_pct_signed')::float,
                        (metadata->>'price_change_pct')::float) >= 0 AS up,
               MAX((metadata->>'price_change_pct')::float)            AS pc
          FROM trigger_events
         WHERE ticker = %s
           AND trigger_type = 'price_threshold'
           AND (metadata->>'price_change_pct') IS NOT NULL
           AND (fired_at AT TIME ZONE 'Asia/Seoul')::date
               = (now() AT TIME ZONE 'Asia/Seoul')::date
         GROUP BY 1
    """
    try:
        with connection() as conn, conn.cursor() as cur:
            cur.execute(sql, (ticker,))
            rows = cur.fetchall() or []
    except Exception:
        LOG.warning("price_threshold: 직전 발사 조회 실패 — 발사 진행", exc_info=True)
        return None
    out = {bool(r["up"]): float(r["pc"]) for r in rows if r.get("pc") is not None}
    return out or None


def _fire_trigger_event(ticker: str, trigger_type: str, metadata: dict[str, Any]) -> None:
    """Record event + invoke shared event handler."""
    from trading.watchers.event_handler import handle_trigger_event

    handle_trigger_event(ticker, trigger_type, metadata)


# @MX:ANCHOR: SPEC-TRADING-024 REQ-024-2 entry-point for adaptive price polling
# @MX:REASON: fan_in >= 3 (scheduler cron, manual CLI smoke test,
#             future Stage 2 reuse)
# @MX:SPEC: SPEC-TRADING-024
def poll_price_threshold(
    atr_multiplier: float = DEFAULT_ATR_MULTIPLIER,
) -> dict[str, Any]:
    """Single poll iteration. Returns metrics dict for observability."""
    metrics = {
        "checked": 0,
        "fired": 0,
        "skipped_no_atr": 0,
        "skipped_no_quote": 0,
        "throttled": 0,
        "level_suppressed": 0,
    }
    throttle = _get_shared_throttle()
    for ticker in _get_target_tickers():
        metrics["checked"] += 1

        quote = _get_kis_quote(ticker)
        if quote is None:
            metrics["skipped_no_quote"] += 1
            continue

        atr_row = compute_atr(ticker)
        if atr_row is None:
            metrics["skipped_no_atr"] += 1
            continue

        close_price = float(atr_row.get("close_price") or 0)
        atr_14 = float(atr_row.get("atr_14") or 0)
        if close_price <= 0 or atr_14 <= 0:
            metrics["skipped_no_atr"] += 1
            continue

        # ATR-relative threshold percentage. atr_14 is absolute KRW units;
        # divide by close_price to get a fractional move, multiply by 100 for
        # pct, then by `atr_multiplier` (default 1.5x).
        threshold_pct = atr_multiplier * (atr_14 / close_price) * 100.0
        signed_change_pct = float(quote.get("change_pct") or 0)
        price_change_pct = abs(signed_change_pct)

        if price_change_pct < threshold_pct:
            continue

        # 2026-09-12: 레벨 트리거 -> 엣지 트리거.
        # change_pct 는 전일 종가 대비 *당일 누적* 변동률이라 한 번 임계를 넘으면
        # 장 마감까지 조건이 계속 참이다. 쿨다운(300초)마다 재발사되어 같은 신호로
        # 전체 사이클(LLM)을 반복 기동한다. 2026-09-09 실측: 096770 한 종목이 13회
        # 발사(8.99 -> 11.52 를 오가며, 값이 *내려가도* 발사), 정규 intraday 사이클
        # 5개가 CYCLE_SKIPPED_IN_FLIGHT 로 파괴됐다. 2026-09-02 에 volume_anomaly 를
        # bar_date 엣지로 고쳤는데(2246f59) 이 파일은 손대지 않았다 — 같은 결함이다.
        #
        # 직전 발사보다 threshold_pct 만큼 더 움직였을 때만 새 신호로 본다.
        # 방향이 뒤집힌 경우(+9% -> -9%)는 크기와 무관하게 새 신호다.
        fired = _fired_max_by_direction(ticker)
        if fired is not None:
            prev_max = fired.get(signed_change_pct >= 0)
            if prev_max is not None and price_change_pct < prev_max + threshold_pct:
                metrics["level_suppressed"] += 1
                continue

        if not throttle.can_fire(ticker):
            metrics["throttled"] += 1
            continue

        throttle.record(ticker)
        metadata = {
            "atr_14": atr_14,
            "close_price": close_price,
            "price_change_pct": price_change_pct,
            "price_change_pct_signed": signed_change_pct,
            "atr_threshold_pct": threshold_pct,
            "atr_multiplier": atr_multiplier,
        }
        _fire_trigger_event(ticker, "price_threshold", metadata)
        metrics["fired"] += 1
        LOG.info(
            "price_threshold fired ticker=%s change_pct=%.2f threshold_pct=%.2f",
            ticker,
            price_change_pct,
            threshold_pct,
        )

    if metrics["checked"]:
        LOG.info("price_threshold poll: %s", json.dumps(metrics))
    return metrics
