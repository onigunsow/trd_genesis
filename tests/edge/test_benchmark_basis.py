"""알파의 비교 기준을 산문이 아니라 수치로 남긴다 (2026-09-05).

배경: 2026-08-23 에 "현금 78% 를 전략 쪽만 제외한 사과-오렌지 알파 +12.69p" 가
확인됐다. 그때 붙인 경고 문장은 *얼마나* 부풀려졌는지 답하지 않아서 매번 손으로
환산해야 했다. 이제 compute() 가 투자비중·라운드트립 수·투입원가를 함께 싣는다.

DB 를 타지 않도록 ``invested`` 를 주입한다(``closes`` 주입과 같은 패턴).
"""
from __future__ import annotations

from datetime import date

from trading.edge.benchmark import compute
from trading.edge.roundtrips import RoundTrip

_CLOSES = [(date(2026, 1, 1), 2500.0), (date(2026, 1, 31), 2550.0)]


def _rt(qty: int, ep: float, xp: float) -> RoundTrip:
    return RoundTrip(
        ticker="A",
        entry_date=date(2026, 1, 1),
        exit_date=date(2026, 1, 31),
        qty=qty,
        entry_price=ep,
        exit_price=xp,
        entry_fee=0,
        exit_fee=0,
        confidence=None,
        verdict=None,
    )


def test_투자비중이_기준문에_수치로_실린다():
    b = compute([_rt(10, 100, 120)], closes=_CLOSES, invested=(21.6, 18))

    assert b.invested_share_pct == 21.6
    assert b.invested_days == 18
    # 분모가 화면에 남는가 — 투자비중과 그 나머지(현금) 둘 다.
    assert "21.6%" in b.comparison_basis
    assert "78.4%" in b.comparison_basis
    # 현금이 아니라 현금+미결제다 — cash 컬럼은 cash_d2(D+2 예수금)라 당일 현금과 다르다.
    assert "현금·미결제" in b.comparison_basis
    assert "18일" in b.comparison_basis


def test_기준문이_구간과_투입원가를_밝힌다():
    b = compute([_rt(10, 100, 120), _rt(5, 200, 190)], closes=_CLOSES, invested=(50.0, 3))

    assert b.n_roundtrips == 2
    assert b.total_cost_basis == 10 * 100 + 5 * 200
    assert "2026-01-01~2026-01-31" in b.comparison_basis
    assert "라운드트립 2건" in b.comparison_basis
    assert "2,000원" in b.comparison_basis


def test_스냅샷이_없으면_침묵하지_않고_모른다고_적는다(monkeypatch):
    """미측정을 '왜곡 없음'으로 읽히게 두지 않는다.

    DB 유무에 결과가 흔들리지 않도록 로더를 직접 None 으로 고정한다.
    """
    monkeypatch.setattr("trading.edge.benchmark.invested_share", lambda s, e: None)
    b = compute([_rt(10, 100, 120)], closes=_CLOSES, invested=None)

    # 측정 실패해도 알파 자체는 그대로 나온다.
    assert b.available is True
    assert b.invested_days == 0
    assert "미측정" in b.comparison_basis


def test_라운드트립이_없으면_기준문도_없다():
    b = compute([], closes=_CLOSES)
    assert b.available is False
    assert b.comparison_basis == ""


def test_수수료가_0이면_비용_미반영이라고_밝힌다():
    """페이퍼 체결은 fee 를 안 남긴다 — 알파가 비용 반영 전임을 화면이 말해야 한다."""
    b = compute([_rt(10, 100, 120)], closes=_CLOSES, invested=(13.2, 39))

    assert b.total_fees == 0
    assert "비용 반영 전" in b.comparison_basis


def test_수수료가_있으면_비용_문구를_붙이지_않는다():
    rt = _rt(10, 100, 120)
    rt.entry_fee, rt.exit_fee = 150.0, 150.0
    b = compute([rt], closes=_CLOSES, invested=(13.2, 39))

    assert b.total_fees == 300.0
    assert "비용 반영 전" not in b.comparison_basis


def test_지수_구간이_짧으면_경고를_붙인다():
    """지수(1001)에는 갱신 잡이 없어 2026-08-20 에서 멈춰 있었다. 그 결과 한 달짜리
    전략 수익률을 9일짜리 지수 수익률과 비교하고도 화면은 아무 말이 없었다."""
    closes = [(date(2026, 1, 1), 2500.0), (date(2026, 1, 10), 2550.0)]  # end 보다 21일 이름
    b = compute([_rt(10, 100, 120)], closes=closes, invested=(13.2, 39))

    assert b.available is True          # 알파 자체는 그대로 낸다
    assert b.kospi_end == date(2026, 1, 10)
    assert "【경고】" in b.comparison_basis
    assert "같은 기간 비교가 아니다" in b.comparison_basis


def test_지수가_구간을_덮으면_경고가_없다():
    b = compute([_rt(10, 100, 120)], closes=_CLOSES, invested=(13.2, 39))
    assert "【경고】" not in b.comparison_basis
