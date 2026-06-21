"""SPEC-TRADING-055: resolver_health 모듈 단위 테스트.

커버리지 케이스:
- healthy(거래일, 신선, stuck 0, parity True)
- resolver 미발화 — 거래일, last_resolver_run=None → hard anomaly
- resolver 미발화 — 거래일, last_resolver_run=어제 → hard anomaly
- stuck>0 → hard anomaly
- parity False → soft note (hard 아님)
- 비거래일 → resolver_fresh=True (vacuous)
- throttle — 쿨다운 내 2회차 → False
- summary_line 포맷 확인
- tz: last_resolver_run UTC "오늘" → KST 날짜 일치
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta
from unittest.mock import patch

KST_OFFSET = timedelta(hours=9)

# UTC 기준 "오늘 10:00 KST" = "오늘 01:00 UTC"
def _today_kst_as_utc(hour_kst: int = 10, minute: int = 0) -> datetime:
    """오늘 KST HH:MM 을 UTC datetime 으로 반환."""
    from zoneinfo import ZoneInfo
    KST = ZoneInfo("Asia/Seoul")
    now_utc = datetime.now(UTC)
    today_kst = now_utc.astimezone(KST).date()
    return datetime(today_kst.year, today_kst.month, today_kst.day,
                    hour_kst, minute, tzinfo=KST).astimezone(UTC)


def _yesterday_utc() -> datetime:
    return datetime.now(UTC) - timedelta(days=1)


# ---------------------------------------------------------------------------
# evaluate_resolver_health 헬퍼 패치
# ---------------------------------------------------------------------------

def _patch_health(
    *,
    last_resolver_run: datetime | None,
    stuck_count: int = 0,
    parity: bool = True,
    by_ticker: dict | None = None,
    is_trading: bool = True,
    now: datetime | None = None,
):
    """evaluate_resolver_health 의 의존성을 전부 패치한 컨텍스트.

    resolver_health.py 가 지연 임포트를 사용하므로 원본 모듈 경로를 패치한다.
    """
    state = {"last_resolver_run": last_resolver_run}
    divergence = {"parity": parity, "by_ticker": by_ticker or {}}

    stuck_row = {"n": stuck_count}

    class FakeCur:
        def __init__(self): self._row = stuck_row
        def execute(self, *a, **kw): pass
        def fetchone(self): return self._row
        def __enter__(self): return self
        def __exit__(self, *a): pass

    class FakeConn:
        def cursor(self): return FakeCur()
        def __enter__(self): return self
        def __exit__(self, *a): pass

    from contextlib import contextmanager

    @contextmanager
    def fake_connection(**kw):
        yield FakeConn()

    # 지연 임포트 경로를 원본 모듈에서 패치
    patches = [
        patch("trading.db.session.get_system_state", return_value=state),
        patch("trading.db.session.connection", fake_connection),
        patch("trading.kis.ghost_convergence.orders_positions_divergence", return_value=divergence),
        patch("trading.scheduler.calendar.is_trading_day", return_value=is_trading),
    ]
    return patches


# ---------------------------------------------------------------------------
# 케이스 1: healthy — 거래일, 신선, stuck 0, parity True
# ---------------------------------------------------------------------------

def test_healthy_trading_day():
    last_run = _today_kst_as_utc(hour_kst=9, minute=5)
    patches = _patch_health(last_resolver_run=last_run, stuck_count=0, parity=True)
    for p in patches:
        p.start()
    try:
        from trading.ops.resolver_health import evaluate_resolver_health
        h = evaluate_resolver_health()
    finally:
        for p in patches:
            p.stop()

    assert h["resolver_fresh"] is True
    assert h["stuck_count"] == 0
    assert h["parity"] is True
    assert h["hard_anomalies"] == []
    assert h["soft_notes"] == []
    assert h["healthy_hard"] is True


# ---------------------------------------------------------------------------
# 케이스 2: resolver 미발화 — 거래일, last_resolver_run=None
# ---------------------------------------------------------------------------

def test_resolver_not_fired_none():
    patches = _patch_health(last_resolver_run=None, is_trading=True)
    for p in patches:
        p.start()
    try:
        from trading.ops.resolver_health import evaluate_resolver_health
        h = evaluate_resolver_health()
    finally:
        for p in patches:
            p.stop()

    assert h["resolver_fresh"] is False
    assert any("미발화" in a for a in h["hard_anomalies"])
    assert h["healthy_hard"] is False


# ---------------------------------------------------------------------------
# 케이스 3: resolver 미발화 — 거래일, last_resolver_run=어제
# ---------------------------------------------------------------------------

def test_resolver_not_fired_yesterday():
    patches = _patch_health(last_resolver_run=_yesterday_utc(), is_trading=True)
    for p in patches:
        p.start()
    try:
        from trading.ops.resolver_health import evaluate_resolver_health
        h = evaluate_resolver_health()
    finally:
        for p in patches:
            p.stop()

    assert h["resolver_fresh"] is False
    assert h["healthy_hard"] is False
    assert any("미발화" in a for a in h["hard_anomalies"])


# ---------------------------------------------------------------------------
# 케이스 4: stuck > 0 → hard anomaly
# ---------------------------------------------------------------------------

def test_stuck_orders_hard_anomaly():
    last_run = _today_kst_as_utc(hour_kst=10)
    patches = _patch_health(last_resolver_run=last_run, stuck_count=3, parity=True)
    for p in patches:
        p.start()
    try:
        from trading.ops.resolver_health import evaluate_resolver_health
        h = evaluate_resolver_health()
    finally:
        for p in patches:
            p.stop()

    assert h["stuck_count"] == 3
    assert any("submitted" in a for a in h["hard_anomalies"])
    assert h["healthy_hard"] is False


# ---------------------------------------------------------------------------
# 케이스 5: parity False → soft note (hard 아님)
# ---------------------------------------------------------------------------

def test_parity_false_is_soft():
    last_run = _today_kst_as_utc(hour_kst=10)
    by_ticker = {"005930": {"orders_net": 10, "positions_qty": 8, "diff": 2}}
    patches = _patch_health(
        last_resolver_run=last_run, stuck_count=0, parity=False, by_ticker=by_ticker
    )
    for p in patches:
        p.start()
    try:
        from trading.ops.resolver_health import evaluate_resolver_health
        h = evaluate_resolver_health()
    finally:
        for p in patches:
            p.stop()

    assert h["parity"] is False
    # hard_anomalies 에 parity 항목 없어야 함
    assert all("parity" not in a.lower() and "드리프트" not in a for a in h["hard_anomalies"])
    # soft_notes 에는 포함
    assert len(h["soft_notes"]) >= 1
    assert any("드리프트" in n for n in h["soft_notes"])
    # healthy_hard = True (hard 이상 없음)
    assert h["healthy_hard"] is True


# ---------------------------------------------------------------------------
# 케이스 6: 비거래일 → resolver_fresh=True
# ---------------------------------------------------------------------------

def test_non_trading_day_fresh():
    patches = _patch_health(last_resolver_run=None, is_trading=False)
    for p in patches:
        p.start()
    try:
        from trading.ops.resolver_health import evaluate_resolver_health
        h = evaluate_resolver_health()
    finally:
        for p in patches:
            p.stop()

    assert h["resolver_fresh"] is True
    assert h["hard_anomalies"] == []
    assert h["healthy_hard"] is True


# ---------------------------------------------------------------------------
# 케이스 7: throttle — 쿨다운 내 2회차 → False
# ---------------------------------------------------------------------------

def test_throttle_within_cooldown():
    """maybe_notify_resolver_anomaly 가 쿨다운 내 2회차 호출 시 False 반환."""
    h_anomaly = {
        "hard_anomalies": ["resolver 오늘 미발화(거래일)"],
        "soft_notes": [],
        "healthy_hard": False,
    }

    now_fixed = datetime.now(UTC)
    # 1회차: 쿨다운 없는 세계 (notified_at=None)
    state_no_stamp = {"resolver_anomaly_notified_at": None}
    # 2회차: 방금 전 stamp (cooldown 내)
    state_stamped = {"resolver_anomaly_notified_at": now_fixed - timedelta(seconds=10)}

    call_count = 0

    def fake_get_state_seq():
        nonlocal call_count
        # 첫 번째 maybe_notify 호출에서 update 호출 전 → None
        # 두 번째 호출에서 → stamped
        return state_no_stamp if call_count == 0 else state_stamped

    with (
        patch("trading.db.session.get_system_state", side_effect=fake_get_state_seq),
        patch("trading.db.session.update_system_state"),
        patch("trading.alerts.telegram.system_briefing") as mock_brief,
    ):
        from trading.ops.resolver_health import maybe_notify_resolver_anomaly

        # 1회차: 발송 돼야 함
        sent1 = maybe_notify_resolver_anomaly(
            h_anomaly, cooldown_seconds=3600, now_provider=lambda: now_fixed
        )
        call_count += 1

        # 2회차: throttled
        sent2 = maybe_notify_resolver_anomaly(
            h_anomaly, cooldown_seconds=3600, now_provider=lambda: now_fixed + timedelta(seconds=30)
        )

    assert sent1 is True
    assert sent2 is False
    assert mock_brief.call_count == 1


# ---------------------------------------------------------------------------
# 케이스 8: summary_line 포맷
# ---------------------------------------------------------------------------

def test_summary_line_healthy():
    from trading.ops.resolver_health import summary_line
    last_run = _today_kst_as_utc(hour_kst=9, minute=5)
    h = {
        "last_resolver_run": last_run,
        "resolver_fresh": True,
        "stuck_count": 0,
        "parity": True,
        "parity_detail": {},
        "hard_anomalies": [],
        "soft_notes": [],
        "healthy_hard": True,
    }
    line = summary_line(h)
    assert "SPEC-042 운영점검" in line
    assert "✓ 오늘" in line
    assert "stuck 0" in line
    assert "parity OK" in line


def test_summary_line_anomaly():
    from trading.ops.resolver_health import summary_line
    h = {
        "last_resolver_run": None,
        "resolver_fresh": False,
        "stuck_count": 2,
        "parity": False,
        "parity_detail": {"005930": {"orders_net": 5, "positions_qty": 3, "diff": 2}},
        "hard_anomalies": ["resolver 오늘 미발화(거래일)", "submitted 주문 2건 체결 미전환"],
        "soft_notes": ["드리프트..."],
        "healthy_hard": False,
    }
    line = summary_line(h)
    assert "⚠ 미발화" in line
    assert "⚠2" in line
    assert "드리프트" in line


# ---------------------------------------------------------------------------
# 케이스 9: tz — last_resolver_run UTC "오늘" → KST 날짜 일치
# ---------------------------------------------------------------------------

def test_tz_utc_today_matches_kst_today():
    """UTC 00:30 이 KST 09:30 → 오늘 KST 이므로 resolver_fresh=True."""
    from zoneinfo import ZoneInfo
    KST_tz = ZoneInfo("Asia/Seoul")
    # KST 오늘 09:30 → UTC 오늘 00:30
    now_utc = datetime.now(UTC)
    today_kst_date = now_utc.astimezone(KST_tz).date()
    # last_resolver_run = 오늘 KST 09:30 (UTC 변환)
    last_run_kst = datetime(today_kst_date.year, today_kst_date.month, today_kst_date.day,
                             9, 30, tzinfo=KST_tz)
    last_run_utc = last_run_kst.astimezone(UTC)

    patches = _patch_health(last_resolver_run=last_run_utc, is_trading=True)
    for p in patches:
        p.start()
    try:
        from trading.ops.resolver_health import evaluate_resolver_health
        h = evaluate_resolver_health()
    finally:
        for p in patches:
            p.stop()

    assert h["resolver_fresh"] is True
