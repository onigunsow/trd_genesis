"""진입 근거 귀속 — 상시 측정 (2026-08-27).

종목이 아니라 판단을 채점한다. 체결 매수를 진입 직전 5거래일 외인+기관 수급으로
갈라 20거래일 뒤 성과를 낸다.

이 측정의 위험은 숫자가 아니라 **오독**이다. 2026-08-27 실측에서 전체로는
순매수 진입 승률 19.6퍼센트 / 순매도 81.2퍼센트로 강한 신호처럼 보였지만,
같은 달 안에서 비교하니 5월은 부호가 뒤집혔고 순매도 표본 16건 중 11건이
6월 한 달에 몰려 있었다. 채권 z-스코어 때와 같은 함정이다.

그래서 이 함수는 머리기사와 반증을 같은 응답에 담는다. 아래 테스트는 그
정직성 장치들이 조용히 빠지지 않게 지킨다 — 평균값 계산이 아니라 그것이
본체다.
"""

from __future__ import annotations

from contextlib import contextmanager
from datetime import date
from typing import Any
from unittest.mock import MagicMock, patch

from trading.dashboard import gate_queries as gq


def _ro(rows: list[dict[str, Any]]):
    @contextmanager
    def _conn(autocommit: bool = False):
        conn = MagicMock()
        cur = conn.cursor.return_value.__enter__.return_value
        cur.fetchall.return_value = rows
        yield conn
    return patch("trading.dashboard.gate_queries.ro_connection", side_effect=_conn)


def _row(*, ticker="005930", d="2026-06-01", qty=1, px=1000, conf=0.60,
         net_flow=100_00000000, flow_rows=5, px_fwd=1100):
    return {
        "ticker": ticker, "d": date.fromisoformat(d), "qty": qty,
        "px": px, "confidence": conf, "net_flow": net_flow,
        "flow_rows": flow_rows, "px_fwd": px_fwd,
    }


def _fetch(rows):
    gq._gate_cache.clear()
    with _ro(rows):
        return gq.fetch_entry_attribution()


class TestRegimeControlIsMandatory:
    """머리기사만 보면 틀린 결론이 난다 — 월별 셀과 뒤집힘 횟수가 함께 나와야 한다."""

    def test_sign_flip_is_counted(self):
        rows = [
            # 5월: 순매수가 순매도보다 나음 → 전체 방향과 어긋남 = 뒤집힘
            _row(d="2026-05-04", net_flow=+1e10, px=1000, px_fwd=1050),
            _row(d="2026-05-06", net_flow=-1e10, px=1000, px_fwd=900),
            # 6월: 순매도가 나음 → 전체 방향과 일치
            _row(d="2026-06-02", net_flow=+1e10, px=1000, px_fwd=900),
            _row(d="2026-06-04", net_flow=-1e10, px=1000, px_fwd=1100),
        ]
        out = _fetch(rows)
        assert out["sign_flip_months"] == 1
        may = next(m for m in out["by_month"] if m["month"] == "2026-05")
        assert may["sign_flipped"] is True
        jun = next(m for m in out["by_month"] if m["month"] == "2026-06")
        assert jun["sign_flipped"] is False

    def test_regime_robust_false_when_any_month_flips(self):
        rows = [
            _row(d="2026-05-04", net_flow=+1e10, px=1000, px_fwd=1050),
            _row(d="2026-05-06", net_flow=-1e10, px=1000, px_fwd=900),
            _row(d="2026-06-02", net_flow=+1e10, px=1000, px_fwd=900),
            _row(d="2026-06-04", net_flow=-1e10, px=1000, px_fwd=1100),
            _row(d="2026-07-02", net_flow=+1e10, px=1000, px_fwd=900),
            _row(d="2026-07-04", net_flow=-1e10, px=1000, px_fwd=1100),
        ]
        assert _fetch(rows)["regime_robust"] is False

    def test_regime_robust_false_when_too_few_comparable_months(self):
        """뒤집힘이 0이어도 비교 가능한 달이 3개 미만이면 견고하다고 하지 않는다."""
        rows = [
            _row(d="2026-06-02", net_flow=+1e10, px=1000, px_fwd=900),
            _row(d="2026-06-04", net_flow=-1e10, px=1000, px_fwd=1100),
        ]
        out = _fetch(rows)
        assert out["sign_flip_months"] == 0
        assert out["months_comparable"] == 1
        assert out["regime_robust"] is False

    def test_month_with_only_one_side_is_not_comparable(self):
        rows = [
            _row(d="2026-05-04", net_flow=+1e10),
            _row(d="2026-05-06", net_flow=+1e10),
        ]
        may = next(m for m in _fetch(rows)["by_month"] if m["month"] == "2026-05")
        assert may["comparable"] is False
        assert may["sign_flipped"] is False


class TestMissingDataIsNotASignal:
    def test_no_flow_history_is_unknown_not_outflow(self):
        """수급 이력이 없는 종목을 순매도로 접으면 결측이 신호로 둔갑한다."""
        rows = [_row(net_flow=0, flow_rows=0)]
        out = _fetch(rows)
        cohorts = {c["label"]: c for c in out["flow_cohorts"]}
        assert cohorts["수급 데이터 없음"]["n"] == 1
        assert cohorts["순매도 구간 진입"]["n"] == 0
        assert cohorts["순매수 구간 진입"]["n"] == 0

    def test_scored_is_separate_from_total(self):
        """미래봉 없는 건은 평균에서 빠지되 n 에는 남아야 한다 — 절단 은폐 금지."""
        rows = [_row(px_fwd=1100), _row(px_fwd=None)]
        out = _fetch(rows)
        inflow = out["flow_cohorts"][0]
        assert inflow["n"] == 2
        assert inflow["n_scored"] == 1
        assert out["n_total"] == 2
        assert out["n_scored"] == 1


class TestConfidenceDefinitionsAreNotMixed:
    def test_split_at_boundary(self):
        """8/15 이전 confidence 는 사이징 레버, 이후는 확률 — 섞으면 무의미하다."""
        rows = [
            _row(d="2026-07-01", conf=0.60, px_fwd=900),
            _row(d="2026-08-20", conf=0.60, px_fwd=1100),
        ]
        out = _fetch(rows)
        assert out["confidence_definition_boundary"] == gq.CONFIDENCE_DEF_BOUNDARY
        old = {c["label"]: c for c in out["confidence_cohorts"]["old_definition"]}
        new = {c["label"]: c for c in out["confidence_cohorts"]["new_definition"]}
        assert old["0.56 - 0.65"]["n"] == 1
        assert new["0.56 - 0.65"]["n"] == 1

    def test_new_definition_reports_unscored_rather_than_borrowing_old(self):
        """새 정의 표본이 아직 20거래일을 못 채웠으면 None 이어야 한다."""
        rows = [
            _row(d="2026-07-01", conf=0.60, px_fwd=900),
            _row(d="2026-08-26", conf=0.60, px_fwd=None),
        ]
        new = {c["label"]: c for c in
               _fetch(rows)["confidence_cohorts"]["new_definition"]}
        assert new["0.56 - 0.65"]["n"] == 1
        assert new["0.56 - 0.65"]["n_scored"] == 0
        assert new["0.56 - 0.65"]["ret"] is None
