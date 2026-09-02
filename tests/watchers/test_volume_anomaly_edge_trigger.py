"""2026-09-02 — volume_anomaly 가 같은 일봉으로 반복 발사하던 결함 고정.

거래량·변동성 비율은 '마지막 완성 일봉' 하나로 계산된다. ohlcv 는 장 마감 후
갱신되므로 장중에는 그 값이 상수다 — 조건이 한 번 참이면 쿨다운(300초)마다
재발사돼 일일 상한(20회)까지 같은 신호로 전체 사이클(LLM)을 반복 기동했다.

실측(2026-09-02): 096770 한 종목이 20회 발사. 그중 9회는 정규 사이클과 충돌해
CYCLE_SKIPPED_IN_FLIGHT 로 버려졌고, 나머지는 중복 LLM 사이클로 소모됐다.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import patch

from trading.watchers import volume_anomaly as va

_STATS = {
    "today_volume": 3_000_000.0,
    "avg_20d_volume": 1_000_000.0,   # ratio 3.0 > 2.0
    "atr_today": 12_000.0,
    "atr_20d_median": 4_000.0,       # ratio 3.0 > 1.5
    "bar_date": "2026-09-01",
}


class _AlwaysFire:
    def can_fire(self, _ticker: str) -> bool:
        return True

    def record(self, _ticker: str) -> None:
        pass


def _run(already_fired: bool) -> tuple[dict[str, Any], list[tuple[str, str, dict]]]:
    fired: list[tuple[str, str, dict]] = []

    with (
        patch.object(va, "_get_target_tickers", return_value=["096770"]),
        patch.object(va, "_get_shared_throttle", return_value=_AlwaysFire()),
        patch.object(va, "_get_volume_volatility_stats", return_value=dict(_STATS)),
        patch.object(va, "_already_fired_for_bar", return_value=already_fired),
        patch.object(va, "_fire_trigger_event",
                     side_effect=lambda t, k, m: fired.append((t, k, m))),
    ):
        metrics = va.poll_volume_anomaly()
    return metrics, fired


def test_fires_once_for_a_new_bar() -> None:
    """새 일봉이면 정상 발사한다 — 감시자를 침묵시키지 않는다."""
    metrics, fired = _run(already_fired=False)

    assert metrics["fired"] == 1
    assert metrics["dup_bar"] == 0
    assert len(fired) == 1


def test_does_not_refire_for_the_same_bar() -> None:
    """같은 봉으론 다시 쏘지 않는다 — 이게 20회 반복 발사를 끊는 지점."""
    metrics, fired = _run(already_fired=True)

    assert metrics["fired"] == 0
    assert metrics["dup_bar"] == 1
    assert fired == []


def test_metadata_carries_the_bar_date() -> None:
    """지표를 만든 봉 날짜가 기록돼야 한다.

    종전엔 as_of(벽시계)만 있어서 장중 발사가 '오늘 봉' 으로 오독됐다.
    """
    _, fired = _run(already_fired=False)

    _ticker, _kind, meta = fired[0]
    assert meta["bar_date"] == "2026-09-01"
    assert "as_of" in meta


def test_dedup_query_failure_does_not_silence_the_watcher() -> None:
    """중복 조회가 터져도 발사는 진행한다 — 조회 실패로 감시자를 죽이지 않는다."""
    with patch("trading.db.session.connection", side_effect=RuntimeError("db")):
        assert va._already_fired_for_bar("096770", "2026-09-01") is False


# ---------------------------------------------------------------------------
# 결정 프롬프트: '오늘 매수한 종목' = 단기과열 이라는 추정 차단
# ---------------------------------------------------------------------------


def _render(blocked: dict[str, Any]) -> str:
    from trading.personas.base import render_prompt

    return render_prompt("decision.jinja", **{
        "today": "2026-09-02",
        "cycle_kind": "intraday",
        "event_trigger": None,
        "car_context": None,
        "dynamic_thresholds_enabled": False,
        "assets": {},
        "bought_today": {"316140": 1},
        "blocked_tickers": blocked,
        "overheat_stat_cls": "59",
    })


def test_non_overheated_bought_ticker_is_not_presented_as_code_blocked() -> None:
    """316140(stat_cls=55)은 코드가 막지 않는다 — 그렇게 프롬프트에 써야 한다.

    실측: 페르소나가 '단기과열 stat_cls=59 추정' 이라 쓰고 자기검열했는데
    실제 값은 55(신용가능)였다. 코드가 허용한 매수를 스스로 포기한 것이다.
    """
    prompt = _render({})

    assert "코드는 막지 않는다" in prompt
    assert "추정하지 마라" in prompt


def test_genuinely_overheated_bought_ticker_is_marked_blocked() -> None:
    """진짜 단기과열(59)이면 하드 차단이라고 정확히 알린다."""
    prompt = _render({"316140": {"reason": "단기과열", "stat_cls": "59"}})

    assert "추가 매수 제안 금지" in prompt
