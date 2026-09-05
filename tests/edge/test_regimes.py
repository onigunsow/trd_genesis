"""체제 분할은 성과 비교가 아니라 이벤트 귀속에 쓴다 (2026-09-05).

실측 근거: 경계를 8/17·8/23·8/27 어디로 잘라도 성과 결론은 안 바뀐다(모든 체제가
진다). 반면 rotate 는 60일 창 21건 -> 8/17 이후 0건으로 메커니즘 결론이 뒤집힌다.
"""
from __future__ import annotations

from datetime import datetime, timedelta

from trading.edge import regimes as rg

T0 = datetime(2026, 9, 1, 9, 0)


def _w(code, start, end, last=None):
    return rg.Regime(code_hash=code, prompt_hash="p", start=start, end=end,
                     last_seen=last or start)


class TestWindows:
    def test_마지막_체제만_열려있다(self, monkeypatch):
        rows = [
            {"code_hash": "a", "prompt_hash": "p", "first_seen": T0, "last_seen": T0},
            {"code_hash": "b", "prompt_hash": "p",
             "first_seen": T0 + timedelta(hours=1), "last_seen": T0 + timedelta(hours=2)},
        ]
        monkeypatch.setattr(rg, "connection", _fake_conn(rows))

        w = rg.regime_windows()
        assert [x.code_hash for x in w] == ["a", "b"]
        assert w[0].end == T0 + timedelta(hours=1)
        assert w[1].end is None
        assert w[1].is_current

    def test_기록이_없으면_빈_목록이고_예외가_아니다(self, monkeypatch):
        monkeypatch.setattr(rg, "connection", _fake_conn([]))
        assert rg.regime_windows() == []
        assert rg.current_regime() is None

    def test_DB가_죽어도_빈_목록을_준다(self, monkeypatch):
        def _boom(*a, **k):
            raise RuntimeError("DB down")
        monkeypatch.setattr(rg, "connection", _boom)
        assert rg.regime_windows() == []


class TestOverlap:
    def test_롤백으로_겹친_구간을_찾아낸다(self):
        """같은 버전이 두 구간에 걸쳐 돌면 그 구간 귀속은 신뢰할 수 없다."""
        a = _w("a", T0, T0 + timedelta(hours=1), last=T0 + timedelta(hours=3))
        b = _w("b", T0 + timedelta(hours=1), None)
        assert rg.overlaps([a, b]) == [(a, b)]

    def test_겹치지_않으면_빈_목록(self):
        a = _w("a", T0, T0 + timedelta(hours=1), last=T0 + timedelta(minutes=30))
        b = _w("b", T0 + timedelta(hours=1), None)
        assert rg.overlaps([a, b]) == []


class TestUnderpowered:
    def test_현재_표본에서는_비교를_거절한다(self):
        """오늘의 실제 숫자 — 59 vs 14."""
        msg = rg.underpowered(59, 14)
        assert msg is not None
        assert "259" in msg
        assert "이벤트 귀속" in msg

    def test_양쪽_모두_충분해야_통과한다(self):
        assert rg.underpowered(300, 258) is not None
        assert rg.underpowered(259, 259) is None


def _fake_conn(rows):
    class _Cur:
        def execute(self, *a, **k): pass
        def fetchall(self): return rows
        def fetchone(self): return {"n": 0}
        def __enter__(self): return self
        def __exit__(self, *a): return False

    class _Conn:
        def cursor(self): return _Cur()
        def __enter__(self): return self
        def __exit__(self, *a): return False

    return lambda *a, **k: _Conn()
