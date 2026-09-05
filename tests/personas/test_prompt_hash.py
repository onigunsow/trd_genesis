"""persona_runs 에 프롬프트 세트 버전을 못 박는다 (2026-09-05).

배경: decision.jinja 를 고칠 때마다 성과 시계열이 조용히 다른 전략의 것과 섞였다.
8/27 stat_cls 정정 → 8/28 수급표 주입 → 9/2 자기검열 수정 이 전부 프롬프트를
바꿨는데 persona_runs 에는 흔적이 없어 PF 를 한 덩어리로 재고 있었다.
"""
from __future__ import annotations

from trading.personas import base


def _fresh() -> str | None:
    base.prompt_set_hash.cache_clear()
    return base.prompt_set_hash()


def test_실제_프롬프트_디렉터리에서_해시가_나온다():
    h = _fresh()
    assert h is not None
    assert len(h) == 12


def test_프롬프트_내용이_바뀌면_해시가_바뀐다(tmp_path, monkeypatch):
    a = tmp_path / "a"
    a.mkdir()
    (a / "decision.jinja").write_text("원본")
    monkeypatch.setattr(base, "_prompt_dir", lambda: a)
    before = _fresh()

    (a / "decision.jinja").write_text("한 글자 바뀜")
    after = _fresh()

    assert before != after


def test_파일명만_바뀌어도_해시가_바뀐다(tmp_path, monkeypatch):
    """이름 변경도 다른 프롬프트 세트다 — 내용 해시만 쓰면 놓친다."""
    a = tmp_path / "a"
    a.mkdir()
    (a / "decision.jinja").write_text("같은 내용")
    monkeypatch.setattr(base, "_prompt_dir", lambda: a)
    before = _fresh()

    (a / "decision.jinja").rename(a / "decision_v2.jinja")
    after = _fresh()

    assert before != after


def test_디렉터리가_없어도_매매를_막지_않는다(tmp_path, monkeypatch):
    """해시는 감사용 부가정보다. 산출 실패는 None 이고 예외가 아니다."""
    monkeypatch.setattr(base, "_prompt_dir", lambda: tmp_path / "없음")
    assert _fresh() is None
