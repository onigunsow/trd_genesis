"""SPEC-TRADING-026 (c3) — content-based sector reclassification.

Articles inherit the *feed's* sector at crawl time, so a bio article pulled from
a generic "energy" feed (or a broad Google-News query) is mislabelled. This
classifier overrides the feed sector ONLY when the article's own text strongly
and unambiguously matches a different sector; otherwise it keeps the feed value
(conservative — avoids mis-routing correctly-tagged articles).

Real 2026-05-22 misclassifications motivating this:
- "한올바이오파마 개발 신약, 류마티스관절염서 효과"  (feed=semiconductor) → biotech_pharma
- "K-진단기술, 세계시장 진출"                        (feed=energy_commodities) → biotech_pharma
"""

from __future__ import annotations

from trading.news.sector_classifier import classify_sector


class TestReclassifiesObviousMismatches:
    def test_bio_article_from_semiconductor_feed(self):
        out = classify_sector(
            "한올바이오파마 개발 신약, 류마티스관절염서 효과",
            "임상 결과 항체 치료제가 효과를 보였다",
            fallback="semiconductor",
        )
        assert out == "biotech_pharma"

    def test_diagnostics_article_from_energy_feed(self):
        out = classify_sector(
            "K-진단기술, 세계시장 진출…'역대 최대 기술이전' 성사",
            "체외진단 기업의 기술 수출",
            fallback="energy_commodities",
        )
        assert out == "biotech_pharma"


class TestKeepsFeedSectorWhenUnsure:
    def test_correctly_tagged_semiconductor_kept(self):
        out = classify_sector(
            "쎄크, 하이브리드 본딩·TGV 검사 장비 준비...반도체 매출 520억 목표",
            "후공정 패키징 장비",
            fallback="semiconductor",
        )
        assert out == "semiconductor"

    def test_generic_english_title_kept(self):
        out = classify_sector("Breaking News", None, fallback="it_ai")
        assert out == "it_ai"

    def test_no_override_when_feed_sector_also_matches(self):
        # 반도체(semiconductor) and 은행(finance) both appear → ambiguous → keep feed.
        out = classify_sector(
            "삼성전자 반도체 호황에 은행주 동반 상승",
            None,
            fallback="semiconductor",
        )
        assert out == "semiconductor"

    def test_single_incidental_keyword_does_not_flip(self):
        # One body mention of an off-sector keyword must not override (needs >=2).
        out = classify_sector(
            "원·달러 환율 1500원대 마감",
            "일부 제약주가 영향을 받았다",  # single 제약 mention in body
            fallback="finance_banking",
        )
        assert out == "finance_banking"


class TestFallbackHandling:
    def test_empty_fallback_returned_when_no_match(self):
        assert classify_sector("그냥 일반 뉴스", None, fallback="") == ""

    def test_title_weight_lets_strong_title_win(self):
        # 신약 + 임상 in the title → biotech beats feed.
        out = classify_sector("신약 임상 3상 성공", None, fallback="stock_market")
        assert out == "biotech_pharma"
