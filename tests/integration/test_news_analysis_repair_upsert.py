"""SPEC-TRADING-061 REQ-061-4 — 실 Postgres UPSERT 덮어쓰기 통합테스트.

news_analysis.article_id 는 UNIQUE 제약이 있어 신규 import 는
``ON CONFLICT DO NOTHING`` 이면 충분하지만, 재수리(repair) 경로는 이미
분석된(오염 가능) 행을 재분석 결과로 **덮어써야** 한다 —
``ON CONFLICT (article_id) DO UPDATE`` 가 필요하다(REQ-061-4).

mock 테스트는 SQL 문법·제약 상호작용의 거짓그린을 준다
([[reference_integration_tests]]) — 실 trading_test DB 에 대해
``import_repair_results()`` 를 끝까지 실행해 검증한다.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from typing import Any
from unittest.mock import patch

import pytest

pytestmark = pytest.mark.integration


def _insert_article(conn: Any, *, title: str, sector: str, published_at) -> int:
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO news_articles
                (title, url, source_name, sector, language, published_at, content_hash)
            VALUES (%s, %s, %s, %s, %s, %s, %s)
            RETURNING id
            """,
            (
                title, f"http://test.invalid/{title}", "TestSource", sector, "ko",
                published_at, f"hash-{title}-{published_at.isoformat()}",
            ),
        )
        row = cur.fetchone()
        assert row is not None
    conn.commit()
    return row["id"]


def _insert_polluted_analysis(conn: Any, *, article_id: int) -> None:
    """RC1/RC2 로 오염된(엉뚱한 기사에 붙은) 기존 news_analysis 행을 시뮬레이션."""
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO news_analysis
                (article_id, summary_2line, impact_score, keywords, sentiment,
                 classification, model_used, token_input, token_output, cost_krw)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            """,
            (
                article_id, "오염된 잘못된 요약 — 다른 기사 내용", 1,
                ["오염", "잘못된키워드"], "neutral", "noise", "claude-cli", 0, 0, 0.0,
            ),
        )
    conn.commit()


class TestRepairUpsertOverwrite:
    """REQ-061-4: 정렬 검증 통과 시 UPSERT 로 기존(오염) 행을 덮어쓴다."""

    def test_import_repair_results_upserts_existing_row(self, migrated_db, tmp_path):
        now = datetime.now(UTC)
        article_id = _insert_article(
            migrated_db, title="삼성전자 1분기 실적 발표", sector="semiconductor",
            published_at=now - timedelta(days=1),
        )
        _insert_polluted_analysis(migrated_db, article_id=article_id)

        results_file = tmp_path / "analysis_results.json"
        pending_file = tmp_path / "pending_analysis.json"
        repair_meta_file = tmp_path / "repair_pending_metadata.json"

        repair_meta_file.write_text(json.dumps({
            "article_ids": [article_id],
            "since": "2026-01-01",
            "model_used": "claude-cli",
        }))
        results_file.write_text(json.dumps([
            {"idx": 1, "classification": "company_specific", "impact_score": 4,
             "investment_implication": "정정된 올바른 요약. 실적 호조로 반도체 섹터 주목.",
             "keywords": ["반도체", "실적"], "sentiment": "positive"},
        ]))

        from trading.news.intelligence import repair as repair_mod

        with (
            patch.object(repair_mod, "RESULTS_FILE", results_file),
            patch.object(repair_mod, "PENDING_FILE", pending_file),
            patch.object(repair_mod, "REPAIR_META_FILE", repair_meta_file),
            patch.object(repair_mod, "audit"),
        ):
            repaired = repair_mod.import_repair_results()

        assert repaired == 1

        with migrated_db.cursor() as cur:
            cur.execute(
                "SELECT article_id, summary_2line, impact_score, classification, "
                "keywords, sentiment FROM news_analysis WHERE article_id = %s",
                (article_id,),
            )
            row = cur.fetchone()

        assert row is not None
        # UPSERT 가 정정된 값으로 덮어썼는지(오염 행이 남아있지 않은지) 확인
        assert row["summary_2line"] == "정정된 올바른 요약. 실적 호조로 반도체 섹터 주목."
        assert row["impact_score"] == 4
        assert row["classification"] == "company_specific"
        assert list(row["keywords"]) == ["반도체", "실적"]
        assert row["sentiment"] == "positive"

        # UNIQUE(article_id) 위반 없이 단일 행만 존재(진짜 UPSERT, 중복 INSERT 아님)
        with migrated_db.cursor() as cur:
            cur.execute(
                "SELECT COUNT(*) AS n FROM news_analysis WHERE article_id = %s",
                (article_id,),
            )
            count_row = cur.fetchone()
        assert count_row["n"] == 1

        # 처리된 파일은 정리된다(다음 재실행과 충돌하지 않도록).
        assert not results_file.exists()

    def test_repair_idempotent_rerun_same_result(self, migrated_db, tmp_path):
        """REQ-061-4: 같은 구간 재실행해도 안전(멱등) — 재실행해도 행 1개, 값 동일."""
        now = datetime.now(UTC)
        article_id = _insert_article(
            migrated_db, title="현대차 신차 발표", sector="auto_ev_battery",
            published_at=now - timedelta(days=2),
        )
        _insert_polluted_analysis(migrated_db, article_id=article_id)

        from trading.news.intelligence import repair as repair_mod

        def _run_once() -> int:
            results_file = tmp_path / f"analysis_results_{article_id}.json"
            pending_file = tmp_path / f"pending_analysis_{article_id}.json"
            repair_meta_file = tmp_path / f"repair_pending_metadata_{article_id}.json"
            repair_meta_file.write_text(json.dumps({
                "article_ids": [article_id],
                "since": "2026-01-01",
                "model_used": "claude-cli",
            }))
            results_file.write_text(json.dumps([
                {"idx": 1, "classification": "sector_specific", "impact_score": 3,
                 "investment_implication": "전기차 수요 회복 조짐. 완성차 롱 포지션 고려.",
                 "keywords": ["전기차", "완성차"], "sentiment": "positive"},
            ]))
            with (
                patch.object(repair_mod, "RESULTS_FILE", results_file),
                patch.object(repair_mod, "PENDING_FILE", pending_file),
                patch.object(repair_mod, "REPAIR_META_FILE", repair_meta_file),
                patch.object(repair_mod, "audit"),
            ):
                return repair_mod.import_repair_results()

        first = _run_once()
        second = _run_once()

        assert first == 1
        assert second == 1  # 재실행도 동일하게 UPSERT 성공(부작용 누적 없음)

        with migrated_db.cursor() as cur:
            cur.execute(
                "SELECT COUNT(*) AS n FROM news_analysis WHERE article_id = %s",
                (article_id,),
            )
            row = cur.fetchone()
        assert row["n"] == 1  # 중복 행 없음
