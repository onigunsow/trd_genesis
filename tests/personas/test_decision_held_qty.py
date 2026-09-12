"""결정 시점의 보유 수량을 남긴다 (2026-09-12).

진입 품질 반사실이 이 값의 부재로 불가능했다. 매수 결정의 91.4%가 분할 추가매수인데
신규 진입과 가를 방법이 없었다 — position_eval_snapshot 은 2026-06-20 부터 59일치뿐,
그 이전을 체결기록으로 재구성하면 2026-05 오류율이 66.9%. 최엄격 정의로 걸러내면
신규 진입 표본이 43건·7종목·유효 n=6 까지 줄어 판정 자체가 불가능했다.
"""
from __future__ import annotations

from unittest.mock import patch

from trading.personas import decision


class _Cur:
    def __init__(self, rows): self.rows = rows
    def execute(self, *a, **k): pass
    def fetchall(self): return self.rows
    def __enter__(self): return self
    def __exit__(self, *a): return False


class _Conn:
    def __init__(self, rows): self.rows = rows
    def cursor(self): return _Cur(self.rows)
    def __enter__(self): return self
    def __exit__(self, *a): return False


def test_보유_종목의_수량을_돌려준다():
    rows = [{"ticker": "096770", "qty": 9}, {"ticker": "055550", "qty": 4}]
    with patch.object(decision, "connection", lambda *a, **k: _Conn(rows)):
        assert decision._held_qty_map() == {"096770": 9, "055550": 4}


def test_조회_실패는_None이다():
    """'모름' 을 '미보유' 로 기록하면 같은 함정이 반복된다 — 0 과 구분한다."""
    def _boom(*a, **k):
        raise RuntimeError("DB down")

    with patch.object(decision, "connection", _boom):
        assert decision._held_qty_map() is None


def test_보유가_없으면_빈_맵이고_None이_아니다():
    with patch.object(decision, "connection", lambda *a, **k: _Conn([])):
        m = decision._held_qty_map()
    assert m == {}
    assert m is not None   # 미보유는 0 으로 기록된다, NULL 이 아니다
