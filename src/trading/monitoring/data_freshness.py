"""SPEC-TRADING-019 REQ-019-5: Stale data monitoring + Telegram alert.

09:00 KST mon-fri cron checks the four data tables (ohlcv / fundamentals /
flows / disclosures) and fires a Telegram alert when any table exceeds the
stale threshold (KRX holiday-adjusted).

Q-5 routing decision (2026-05-11): alerts should go to the dev bot
(@onitrddev_bot). The repo currently exposes a single ``TELEGRAM_BOT_TOKEN_TRADING``
in ``.env``, so we route through the existing ``trading.alerts.telegram.
system_briefing`` helper which uses that token. The shared helper sends to the
trading prod chat — see ``# TODO(SPEC-019)`` below for the eventual dev-bot
split.

Implementation notes:
- Clock and table-latest lookups are injected so tests can monkeypatch without
  hitting the DB or the wall clock (per plan.md "Testing Strategy" hint).
- Expected-ts is computed via ``trading.scheduler.calendar.is_trading_day``
  so a Friday → Monday "Friday data" snapshot does not raise a false alert.
"""

# @MX:ANCHOR: SPEC-019 REQ-019-5 operational visibility entrypoint
# @MX:REASON: data-pipeline stale alerts gate the whole trading system
# @MX:SPEC: SPEC-TRADING-019

from __future__ import annotations

import logging
from collections.abc import Callable
from datetime import date, datetime, timedelta
from typing import Any

from trading.db.session import audit, connection
from trading.scheduler.calendar import is_trading_day

LOG = logging.getLogger(__name__)

# REQ-019-5 (e) — base stale threshold (KRX holidays / weekends adjusted below).
STALE_THRESHOLD_HOURS = 36
FUNDAMENTALS_STALE_DAYS = 8  # REQ-019-5 (d): weekly + 1d grace
DEFAULT_TABLES = ("ohlcv", "fundamentals", "flows", "disclosures")


def _latest_ts_from_db(table: str) -> date | None:
    """SELECT MAX(ts) for ohlcv / flows / fundamentals.

    `disclosures` uses ``rcept_dt`` instead of ``ts``.
    """
    if table == "disclosures":
        sql = "SELECT MAX(rcept_dt) AS hi FROM disclosures"
    elif table in ("ohlcv", "fundamentals", "flows"):
        sql = f"SELECT MAX(ts) AS hi FROM {table}"  # noqa: S608 (whitelisted)
    else:
        raise ValueError(f"unsupported table: {table}")

    with connection() as conn, conn.cursor() as cur:
        cur.execute(sql)
        row = cur.fetchone()
    if not row:
        return None
    hi = row.get("hi") if isinstance(row, dict) else row[0]
    return hi  # date | None


def _previous_trading_day(d: date) -> date:
    """Return the most recent trading day strictly before ``d``."""
    cur = d - timedelta(days=1)
    for _ in range(14):  # bounded scan — holidays don't span > 2 weeks
        if is_trading_day(cur):
            return cur
        cur = cur - timedelta(days=1)
    return cur


def _expected_ts(table: str, now: datetime) -> date:
    """REQ-019-5 (d): KRX-aware expected latest timestamp per table.

    For ohlcv/flows/disclosures: previous KRX trading day. Disclosures run
    365/yr in production but the staleness *check* runs 09:00 mon-fri, and
    after a weekend the most-recent expected disclosure publication is also
    bounded by the previous trading day (REQ-019-5 (h) false-positive
    avoidance — see Scenario 8 in acceptance.md).
    """
    today = now.date()
    if table == "fundamentals":
        # Weekly — expected the most recent Sunday + 1d grace.
        # `weekday()`: Monday = 0 .. Sunday = 6.
        days_since_sun = (today.weekday() + 1) % 7
        last_sunday = today - timedelta(days=days_since_sun)
        return last_sunday
    # ohlcv / flows / disclosures — previous KRX trading day.
    return _previous_trading_day(today)


def _hours_between(now: datetime, latest: date) -> float:
    """Return hours between `latest` (end-of-day) and `now`."""
    latest_dt = datetime.combine(latest, datetime.min.time()).replace(tzinfo=now.tzinfo)
    delta = now - latest_dt
    return delta.total_seconds() / 3600.0


def _threshold_hours_for(table: str) -> float:
    if table == "fundamentals":
        return FUNDAMENTALS_STALE_DAYS * 24
    return STALE_THRESHOLD_HOURS


def _format_alert(entries: list[dict[str, Any]]) -> str:
    """REQ-019-5 (f): structured message with table / latest / expected / stale."""
    lines = ["[SPEC-019] STALE DATA DETECTED", ""]
    for e in entries:
        if not e["stale"]:
            continue
        days_stale = (
            max(0, (e["expected"] - e["latest"]).days) if e["latest"] else "n/a"
        )
        latest_str = e["latest"].isoformat() if e["latest"] else "(empty)"
        lines.append(
            f"table: {e['table']}\n"
            f"latest: {latest_str}\n"
            f"expected: {e['expected'].isoformat()}\n"
            f"stale: {days_stale} days"
        )
        lines.append("")
    lines.append("=> check container logs / rerun refresh_market_data.py")
    return "\n".join(lines)


def _default_alert_sender(category: str, message: str) -> None:
    """Default alert sender — delegates to existing Telegram briefing helper.

    TODO(SPEC-019): Split TELEGRAM_BOT_TOKEN into dev/prod once the dev bot
    @onitrddev_bot has a dedicated token in .env. Per user decision Q-5
    (2026-05-11), SPEC-019 alerts should route to the dev bot; currently the
    repo only exposes ``TELEGRAM_BOT_TOKEN_TRADING`` so we share that token
    with prod trade briefings.
    """
    from trading.alerts.telegram import system_briefing

    system_briefing(category, message)


# 2026-09-12: 심볼 축 점검. 기존 점검은 테이블당 `MAX(ts)` 하나만 봤다 — ohlcv 는
# 심볼로 나뉜 테이블이라 매매 종목 55개가 매일 들어오면 MAX(ts) 는 언제나 어제이고,
# KOSPI 지수(1001)가 8/20 에 멈춘 3주 내내 "fresh" 로 통과했다. 그 사이 알파 지표가
# 한 달 전략 수익률을 9일 지수 수익률과 비교했다(+6.36p 로 보고, 실제 -7.59p).
# 점검이 통과한 게 아니라 볼 수 있는 축이 없었다.
#
# 폐지·유니버스 이탈 종목까지 알리면 소음이 되므로 "최근에 살아 있던" 시리즈만 본다:
# active_window 안에 데이터가 있었는데 expected 이후로 끊긴 것.
STALE_SYMBOL_ACTIVE_WINDOW_DAYS = 60
STALE_SYMBOL_REPORT_LIMIT = 10
# 지수·매크로 시리즈: 매매 종목이 아니라 유니버스에 안 잡히지만 알파·레짐 계산의
# 입력이라 멈추면 지표가 조용히 틀린다. 1001 이 정확히 그 사례였다.
_BENCHMARK_SYMBOLS = ("1001",)


def _must_be_fresh_symbols() -> set[str]:
    """신선해야 마땅한 ohlcv 심볼 집합.

    전체 심볼을 보면 안 된다 — ``refresh_ohlcv`` 는 ``get_data_universe()`` 만 돌므로
    유니버스에서 빠진 종목은 정상적으로 갱신이 멈춘다. 실측(2026-09-12) 전체 기준
    25개가 잡히는데 대부분 그 부류라, 그대로 알리면 매일 25건짜리 소음이 되고
    아무도 보지 않는다.

    반드시 신선해야 하는 것은 셋이다:
      - 현재 데이터 유니버스 (스크리너·ATR·손절선의 입력)
      - 현재 보유 종목 (평가·워치독의 입력)
      - 지수/벤치마크 시리즈 (알파 계산의 입력 — 2026-08-20~09-11 공백의 당사자)
    """
    from trading.db.session import connection

    out: set[str] = set(_BENCHMARK_SYMBOLS)
    try:
        from trading.data.universe import get_data_universe

        out |= {str(t) for t in get_data_universe()}
    except Exception:  # 유니버스 조회 실패 — 보유·지수만으로 점검한다.
        LOG.warning("data_freshness: 유니버스 조회 실패", exc_info=True)
    try:
        with connection() as conn, conn.cursor() as cur:
            cur.execute("SELECT ticker FROM positions WHERE qty > 0")
            out |= {r["ticker"] for r in cur.fetchall()}
    except Exception:
        LOG.warning("data_freshness: 보유 종목 조회 실패", exc_info=True)
    return out


def _stale_symbols_from_db(
    expected: date, active_window_days: int = STALE_SYMBOL_ACTIVE_WINDOW_DAYS
) -> list[tuple[str, date]]:
    """``expected`` 이후로 끊겼지만 신선해야 마땅한 ohlcv 심볼 (오래된 순)."""
    from trading.db.session import connection

    must = _must_be_fresh_symbols()
    if not must:
        return []

    sql = """
        SELECT symbol, MAX(ts) AS mx
          FROM ohlcv
         WHERE symbol = ANY(%s)
         GROUP BY symbol
        HAVING MAX(ts) < %s
           AND MAX(ts) >= %s
         ORDER BY mx ASC
    """
    floor = expected - timedelta(days=active_window_days)
    with connection() as conn, conn.cursor() as cur:
        cur.execute(sql, (list(must), expected, floor))
        return [(r["symbol"], r["mx"]) for r in cur.fetchall()]


def check_and_alert(
    clock: Callable[[], datetime] = datetime.now,
    latest_ts_fn: Callable[[str], date | None] = _latest_ts_from_db,
    alert_sender: Callable[[str, str], None] = _default_alert_sender,
    tables: tuple[str, ...] = DEFAULT_TABLES,
    stale_symbols_fn: Callable[[date], list[tuple[str, date]]] | None = None,
) -> dict[str, Any]:
    """REQ-019-5: Check 4 data tables and alert on stale state.

    Args:
        clock: Returns current datetime (KST aware in production; naive in tests).
        latest_ts_fn: ``table_name -> latest ts (date | None)``.
        alert_sender: ``(category, message) -> None`` Telegram bridge.
        tables: List of tables to check.

    Returns:
        Summary dict ``{"entries": [...], "alert_sent": bool}``.
    """
    now = clock()
    entries: list[dict[str, Any]] = []
    stale_entries: list[dict[str, Any]] = []

    for table in tables:
        try:
            latest = latest_ts_fn(table)
        except Exception as exc:
            LOG.warning("data_freshness: %s latest_ts lookup failed: %s", table, exc)
            latest = None

        expected = _expected_ts(table, now)
        threshold = _threshold_hours_for(table)

        if latest is None:
            stale = True
            hours_stale = float("inf")
        else:
            hours_stale = _hours_between(now, latest)
            # REQ-019-5 (d): KRX-aware — if latest >= expected we're fresh.
            stale = (latest < expected) and (hours_stale > threshold)

        entry = {
            "table": table,
            "latest": latest,
            "expected": expected,
            "hours_stale": hours_stale,
            "stale": stale,
        }
        entries.append(entry)

        latest_str = latest.isoformat() if latest else "(empty)"
        LOG.info(
            "data_freshness: table=%s latest=%s expected=%s stale=%s",
            table,
            latest_str,
            expected.isoformat(),
            "yes" if stale else "ok",
        )

        if stale:
            stale_entries.append(entry)

    # 심볼 축: 테이블 MAX 가 신선해도 개별 시리즈가 멈춰 있을 수 있다.
    symbol_fn = stale_symbols_fn or _stale_symbols_from_db
    try:
        expected_ohlcv = _expected_ts("ohlcv", now)
        stale_syms = symbol_fn(expected_ohlcv)
    except Exception as exc:
        LOG.warning("data_freshness: 심볼 축 점검 실패: %s", exc)
        stale_syms = []

    if stale_syms:
        entry = {
            "table": "ohlcv:symbols",
            "latest": stale_syms[0][1],
            "expected": expected_ohlcv,
            "hours_stale": _hours_between(now, stale_syms[0][1]),
            "stale": True,
            "stale_symbol_count": len(stale_syms),
            "stale_symbols": [
                {"symbol": sym, "latest": d.isoformat()}
                for sym, d in stale_syms[:STALE_SYMBOL_REPORT_LIMIT]
            ],
        }
        entries.append(entry)
        stale_entries.append(entry)
        LOG.info(
            "data_freshness: 멈춘 심볼 %d개 (최악 %s=%s)",
            len(stale_syms), stale_syms[0][0], stale_syms[0][1].isoformat(),
        )

    alert_sent = False
    if stale_entries:
        category = "SPEC-019 STALE DATA"
        message = _format_alert(entries)
        try:
            alert_sender(category, message)
            alert_sent = True
        except Exception as exc:
            LOG.exception("data_freshness alert delivery failed: %s", exc)

    # 2026-08-08: KRX 비밀번호 만료로 5거래일치 데이터가 비었을 때, 이 점검이
    # 돌긴 했는지·알림을 보냈는지를 사후에 확인할 수 없었다. 도커 로그는 컨테이너
    # 재생성으로 사라지고 여기엔 아무 기록도 남지 않았기 때문이다. 감시자가 잤는지
    # 감시 대상이 멀쩡했는지 구분하려면 점검 자체가 흔적을 남겨야 한다
    # (SPEC-TRADING-063 에서 주문 거부에 대해 얻은 것과 같은 교훈).
    try:
        audit(
            "DATA_FRESHNESS_CHECK",
            actor="scheduler",
            details={
                "alert_sent": alert_sent,
                "tables": [
                    {
                        "table": e["table"],
                        "latest": e["latest"].isoformat() if e["latest"] else None,
                        "stale": e["stale"],
                        "hours_stale": e["hours_stale"],
                    }
                    for e in entries
                ],
            },
        )
    except Exception:
        # 기록 실패가 점검·알림을 깨뜨리면 관측성 추가가 되레 위험이 된다.
        LOG.exception("data_freshness audit insert failed")

    return {"entries": entries, "alert_sent": alert_sent}
