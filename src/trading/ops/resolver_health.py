"""SPEC-TRADING-055 M2: resolver cron 자가점검 + throttle 경고 모듈.

설계 판단:
- hard 이상(즉시 경고): resolver 미발화(거래일 한정) · stuck_count > 0.
- soft 신호(리포트 라인 + 경고 주석): parity False
  (D3: reconcile 지연 / 정상 드리프트 구분 불가 → 결함 단정 금지).
- 비거래일 분기(resolver_fresh=True 무조건): 방어용 수동 호출 전용(D6).
  _wrap() 가 비거래일 16:00 cron 을 차단하므로 크론 경로론 도달 안 됨.
- tz 명시(D5): last_resolver_run(UTC) → .astimezone(KST).date() 비교.
"""
from __future__ import annotations

import logging
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any
from zoneinfo import ZoneInfo

LOG = logging.getLogger(__name__)

# 기본 throttle 쿨다운: 6시간 (maybe_notify_halt 와 동일 철학)
_DEFAULT_COOLDOWN_SECONDS = 6 * 60 * 60

# KST 타임존 — runner.py 는 pytz, telegram.py 는 ZoneInfo 를 쓴다.
# 여기서는 stdlib ZoneInfo 를 사용해 외부 의존성 0.
KST = ZoneInfo("Asia/Seoul")


# ---------------------------------------------------------------------------
# 핵심 평가 함수
# ---------------------------------------------------------------------------

# @MX:ANCHOR: [AUTO] evaluate_resolver_health
# @MX:REASON: daily_report.generate_and_send, maybe_notify_resolver_anomaly, 테스트에서 fan_in >= 3


def evaluate_resolver_health(*, now: datetime | None = None) -> dict[str, Any]:
    """resolver 상태를 점검하고 이상 분류를 반환한다(SPEC-TRADING-055 REQ).

    Parameters
    ----------
    now:
        현재 시각 (테스트 seam). None 이면 UTC now 를 사용.

    Returns
    -------
    dict:
        last_resolver_run  : datetime | None  — 마지막 발화 시각(UTC)
        resolver_fresh     : bool             — 거래일이면 오늘 KST 발화 여부
        stuck_count        : int              — status='submitted' 주문 수
        parity             : bool             — orders/positions 일치 여부
        parity_detail      : dict             — by_ticker 드리프트 상세
        hard_anomalies     : list[str]        — 즉시 경고 항목
        soft_notes         : list[str]        — 리포트 라인 포함 · 경고 주석 항목
        healthy_hard       : bool             — hard_anomalies 비어있으면 True
    """
    # 지연 임포트 — 순환 임포트 방지
    from trading.db.session import connection, get_system_state
    from trading.kis.ghost_convergence import orders_positions_divergence
    from trading.scheduler.calendar import is_trading_day

    _now = now or datetime.now(UTC)
    today_kst = _now.astimezone(KST).date()

    # ── last_resolver_run 신선도 ──────────────────────────────────────────
    state = get_system_state()
    last_resolver_run: datetime | None = state.get("last_resolver_run")

    if is_trading_day(today_kst):
        # 거래일: 오늘 KST 날짜에 발화 기록이 있어야 신선.
        # D5: UTC 저장값을 반드시 astimezone(KST) 후 .date() 비교.
        if last_resolver_run is None:
            resolver_fresh = False
        else:
            resolver_fresh = last_resolver_run.astimezone(KST).date() == today_kst
    else:
        # 비거래일: resolver 발화 안 하는 게 정상 → 무조건 True(D6).
        # 이 분기는 16:00 크론으론 도달 불가(_wrap 차단).
        # 수동 docker exec 검증 전용 방어 분기.
        resolver_fresh = True

    # ── stuck 주문 수 ────────────────────────────────────────────────────
    with connection() as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT COUNT(*) AS n FROM orders WHERE status = 'submitted'"
        )
        row = cur.fetchone() or {}
        stuck_count: int = int(row.get("n") or row.get("count") or 0)

    # ── parity ────────────────────────────────────────────────────────────
    # D3: orders 와 positions 의 괴리.
    # KIS ReadTimeout 으로 reconcile 실패 시 거짓 False 발생 가능(cry-wolf).
    # → soft 신호로만 처리; 결함 단정 금지.
    divergence = orders_positions_divergence()
    parity: bool = divergence["parity"]
    parity_detail: dict[str, Any] = divergence.get("by_ticker", {})

    # ── 이상 분류 ────────────────────────────────────────────────────────
    hard_anomalies: list[str] = []
    soft_notes: list[str] = []

    if not resolver_fresh:
        hard_anomalies.append("resolver 오늘 미발화(거래일)")

    if stuck_count > 0:
        hard_anomalies.append(f"submitted 주문 {stuck_count}건 체결 미전환")

    if not parity:
        # 드리프트 종목 수와 최대 diff 요약
        drifting = {t: v for t, v in parity_detail.items() if v.get("diff", 0) != 0}
        drifting_summary = ", ".join(
            f"{t}(diff={v['diff']})" for t, v in list(drifting.items())[:3]
        )
        if len(drifting) > 3:
            drifting_summary += f" 외 {len(drifting) - 3}종목"
        soft_notes.append(
            f"orders/positions 드리프트 {len(drifting)}종목({drifting_summary})"
            " — 드리프트 또는 reconcile 지연(확인 필요, 결함 단정 불가)"
        )

    return {
        "last_resolver_run": last_resolver_run,
        "resolver_fresh": resolver_fresh,
        "stuck_count": stuck_count,
        "parity": parity,
        "parity_detail": parity_detail,
        "hard_anomalies": hard_anomalies,
        "soft_notes": soft_notes,
        "healthy_hard": len(hard_anomalies) == 0,
    }


# ---------------------------------------------------------------------------
# 요약 라인 생성
# ---------------------------------------------------------------------------


def summary_line(h: dict[str, Any]) -> str:
    """일일리포트 삽입용 한 줄 운영점검 텍스트(SPEC-TRADING-055 REQ).

    예시:
        SPEC-042 운영점검: resolver ✓ 오늘 09:05 · stuck 0 · parity OK
        SPEC-042 운영점검: resolver ⚠ 미발화 · stuck ⚠3 · parity ⚠ 2종목 드리프트(diff…)
    """
    last: datetime | None = h.get("last_resolver_run")
    if h.get("resolver_fresh") and last is not None:
        # 마지막 발화 시각을 KST HH:MM 으로 표기
        last_kst = last.astimezone(KST).strftime("%H:%M")
        resolver_part = f"✓ 오늘 {last_kst}"
    else:
        resolver_part = "⚠ 미발화"

    stuck = h.get("stuck_count", 0)
    stuck_part = str(stuck) if stuck == 0 else f"⚠{stuck}"

    if h.get("parity"):
        parity_part = "OK"
    else:
        drifting = {
            t: v for t, v in (h.get("parity_detail") or {}).items()
            if v.get("diff", 0) != 0
        }
        n = len(drifting)
        sample = list(drifting.items())[:2]
        sample_str = ", ".join(f"{t}={v['diff']}" for t, v in sample)
        if n > 2:
            sample_str += f" 외 {n - 2}종목"
        parity_part = f"⚠ {n}종목 드리프트({sample_str})"

    return (
        f"SPEC-042 운영점검: resolver {resolver_part} · stuck {stuck_part} · parity {parity_part}"
    )


# ---------------------------------------------------------------------------
# throttle 경고 발송
# ---------------------------------------------------------------------------


def maybe_notify_resolver_anomaly(
    h: dict[str, Any],
    *,
    cooldown_seconds: int | None = None,
    now_provider: Callable[[], datetime] | None = None,
) -> bool:
    """resolver 이상 시 6h throttle 경고를 발송한다(SPEC-TRADING-055 REQ).

    maybe_notify_halt 패턴 미러(circuit_breaker.py:35-70).

    Parameters
    ----------
    h:
        evaluate_resolver_health() 반환값.
    cooldown_seconds:
        쿨다운 override (테스트 seam, 기본 6h).
    now_provider:
        현재 시각 provider (테스트 seam).

    Returns
    -------
    True if the briefing was sent this call, False if throttled or no anomaly.
    """
    # hard 이상 없고 soft 주석도 없으면 no-op
    if h.get("healthy_hard") and not h.get("soft_notes"):
        return False

    # 지연 임포트 — 순환 임포트 방지
    from trading.alerts.telegram import system_briefing
    from trading.db.session import get_system_state, update_system_state

    cooldown = _DEFAULT_COOLDOWN_SECONDS if cooldown_seconds is None else cooldown_seconds
    now = (now_provider or (lambda: datetime.now(UTC)))()

    # D5: tz 명시 — resolver_anomaly_notified_at(UTC) 와 now(UTC) 비교
    last = get_system_state().get("resolver_anomaly_notified_at")
    if last is not None:
        # last 가 naive 인 경우 방어 (DB tz-aware 보장이지만 테스트 seam 보호)
        if last.tzinfo is None:
            last = last.replace(tzinfo=UTC)
        if (now - last).total_seconds() < cooldown:
            return False

    # 스탬프 먼저 기록 — Telegram 실패해도 cooldown 적용
    update_system_state(
        resolver_anomaly_notified_at=now,
        updated_by="resolver_health",
    )

    hard = h.get("hard_anomalies") or []
    soft = h.get("soft_notes") or []

    lines: list[str] = ["⚠ resolver 운영 이상 감지"]
    if hard:
        lines.append("[즉시 확인] " + " / ".join(hard))
    if soft:
        lines.append("[참고] " + " / ".join(soft))
    lines.append("권장조치: docker exec trading-app trading migrate 후 로그 확인")

    try:
        system_briefing("운영 이상", "\n".join(lines))
    except Exception:
        LOG.exception("resolver-health telegram briefing 실패")

    return True
