"""decision.jinja — confidence 정의 + entry_freshness (2026-08-15).

라이브 실측: 매수 결정 831건의 20일 반사실에서 confidence 가 수익과 역상관
(0.4구간 +7.92%/승률81%, 0.6구간 -3.67%/승률24%). 원인은 프롬프트에
confidence 의 정의가 없고 결과 규칙(<0.7 절반 진입 등)만 있어, 모델이 확률이
아니라 사이징 레버로 숫자를 채운 것. 또한 높은 confidence 의 근거가 전부
"MA 돌파·수급 쌍끌이·모멘텀 가속" 같은 후행 확인 신호 — 즉 이미 오른 뒤였다.

수정: confidence 를 "20거래일 뒤 수익일 확률"로 명시 정의하고, 진입 시점을
별도 라벨(entry_freshness)로 받는다. 코드는 confidence 를 소비하지 않으므로
매매에는 영향이 없고, 8/17~ 새 정의의 상관을 재는 것이 목적이다.

이 테스트는 그 정의가 템플릿에서 사라지지 않게 지킨다.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from jinja2 import Environment, FileSystemLoader

_PROMPTS = (
    Path(__file__).resolve().parent.parent.parent / "src" / "trading" / "personas" / "prompts"
)


@pytest.fixture
def rendered() -> str:
    env = Environment(loader=FileSystemLoader(str(_PROMPTS)))
    return env.get_template("decision.jinja").render(
        today="2026-08-15", cycle_kind="intraday", assets={}, blocked_tickers={},
    )


class TestConfidenceIsDefinedAsProbability:
    def test_definition_present(self, rendered):
        assert "20거래일 뒤 수익일 확률" in rendered

    def test_forbids_fitting_number_to_sizing_rule(self, rendered):
        """모델이 '0.7 임계 통과 → 절반 진입' 결과에 맞춰 숫자를 채우던 패턴 차단."""
        assert "규칙 통과 여부를 먼저 정하고" in rendered
        assert "룰 통과를 위해 숫자를 맞추지 않는다" in rendered

    def test_warns_confirmation_may_be_priced_in(self, rendered):
        """높은 confidence = 후행 확인 신호 누적 = 이미 오른 뒤 — 를 명시."""
        assert "이미 가격에 반영됐을 수 있다" in rendered

    def test_low_confidence_not_penalised(self, rendered):
        assert "낮은 confidence 는 벌점이 아니다" in rendered


class TestEntryFreshnessLabel:
    def test_schema_has_field(self, rendered):
        assert '"entry_freshness"' in rendered

    def test_three_levels_defined(self, rendered):
        for level in ("early", "confirmed", "late"):
            assert f"`{level}`" in rendered

    def test_independent_from_confidence(self, rendered):
        """freshness 로 confidence 를 정당화하면 분리 측정이 무의미해진다."""
        assert "confidence 와 별개로 산출" in rendered
        assert "late 라고 confidence 를 올리지 말고" in rendered


def test_raw_jsonb_will_carry_freshness_without_code_change():
    """decision.py 는 sig 전체를 raw jsonb 로 저장하므로 entry_freshness 는
    코드 변경 없이 raw->>'entry_freshness' 로 조회 가능해야 한다."""
    src = (Path(__file__).resolve().parent.parent.parent
           / "src" / "trading" / "personas" / "decision.py").read_text(encoding="utf-8")
    assert "json.dumps(sig)" in src
