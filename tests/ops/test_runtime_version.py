"""실행 코드 버전을 시간축에 남긴다 (2026-09-05).

2026-09-05 에 60일 성과 창이 여러 배포를 섞는 바람에 이미 고쳐진 결함 셋을
현재 결함으로 진단했다(장전 매도 8/29 수정, rotate 8/17 이후 0건, 원장 음수는
parity=True). 사람이 배포 이력을 대조해 겨우 걸렀다 — 주간 자동 에이전트에는
그 제동이 없으므로 체제를 데이터로 갈 수 있어야 한다.
"""
from __future__ import annotations

from trading.ops import runtime_version as rv


def _fresh_code_hash() -> str | None:
    rv.code_set_hash.cache_clear()
    return rv.code_set_hash()


def test_실제_소스트리에서_해시가_나온다():
    h = _fresh_code_hash()
    assert h is not None
    assert len(h) == 12


def test_파이썬_파일이_바뀌면_해시가_바뀐다(tmp_path, monkeypatch):
    root = tmp_path / "trading"
    (root / "kis").mkdir(parents=True)
    (root / "kis" / "order.py").write_text("x = 1")
    monkeypatch.setattr(rv, "_code_dir", lambda: root)
    before = _fresh_code_hash()

    (root / "kis" / "order.py").write_text("x = 2")
    assert _fresh_code_hash() != before


def test_파일이_추가돼도_해시가_바뀐다(tmp_path, monkeypatch):
    """파일 추가·삭제·이름 변경도 다른 코드 체제다."""
    root = tmp_path / "trading"
    root.mkdir()
    (root / "a.py").write_text("x = 1")
    monkeypatch.setattr(rv, "_code_dir", lambda: root)
    before = _fresh_code_hash()

    (root / "b.py").write_text("y = 2")
    assert _fresh_code_hash() != before


def test_pycache_는_해시에_안_들어간다(tmp_path, monkeypatch):
    """소스가 같아도 바이트코드는 달라진다 — 같은 코드가 다른 버전으로 갈리면 안 된다."""
    root = tmp_path / "trading"
    root.mkdir()
    (root / "a.py").write_text("x = 1")
    monkeypatch.setattr(rv, "_code_dir", lambda: root)
    before = _fresh_code_hash()

    cache = root / "__pycache__"
    cache.mkdir()
    (cache / "a.cpython-314.py").write_text("컴파일 산출물")
    assert _fresh_code_hash() == before


def test_소스가_없어도_매매를_막지_않는다(tmp_path, monkeypatch):
    """버전 표식은 관측용이다 — 산출 실패는 None 이고 예외가 아니다."""
    monkeypatch.setattr(rv, "_code_dir", lambda: tmp_path / "없음")
    assert _fresh_code_hash() is None


def test_DB가_죽어도_예외를_올리지_않는다(monkeypatch):
    """기록 실패가 스케줄러 기동을 막아서는 안 된다."""
    def _boom(*a, **k):
        raise RuntimeError("DB down")

    monkeypatch.setattr(rv, "connection", _boom)
    rv.code_set_hash.cache_clear()   # 앞 테스트가 남긴 None 캐시를 지운다
    rv.record_runtime_version.cache_clear()
    code, _prompt = rv.record_runtime_version()

    assert code is not None      # 해시 자체는 DB 없이도 나온다
    rv.record_runtime_version.cache_clear()
