"""SPEC-TRADING-062 REQ-062-B2/B4 — repair.import_repair_results 의 content-anchor
검증 배선. 저장 경로 3곳(_store_results/import_host_results/repair.import_repair_
results) 중 세 번째인 repair 경로가 앵커 불일치를 fail-closed 로 거부하는지 cursor
더블로 실 SQL 실행 경로를 검증한다(mock 이 못 잡는 거짓그린 방지 관례,
project memory: [[reference_integration_tests]] — tests/news/intelligence/
test_store_alignment.py 와 동형 패턴).
"""

from __future__ import annotations

import json
from contextlib import contextmanager
from typing import Any
from unittest.mock import MagicMock, patch


class RecordingCursor:
    def __init__(self) -> None:
        self.executed: list[tuple[str, Any]] = []

    def execute(self, sql: str, params: Any = None) -> None:
        self.executed.append((sql, params))

    def fetchone(self):
        return None

    def fetchall(self):
        return []

    def __enter__(self) -> RecordingCursor:
        return self

    def __exit__(self, *_: Any) -> None:
        pass

    def upsert_calls(self) -> list[tuple]:
        return [p for sql, p in self.executed if sql.strip().startswith("INSERT")]


class RecordingConn:
    def __init__(self, cursor: RecordingCursor) -> None:
        self._cur = cursor

    def cursor(self) -> RecordingCursor:
        return self._cur

    def commit(self) -> None:
        pass

    def __enter__(self) -> RecordingConn:
        return self

    def __exit__(self, *_: Any) -> None:
        pass


def _make_conn_patch(target: str):
    cursor = RecordingCursor()
    conn = RecordingConn(cursor)

    @contextmanager
    def _conn(autocommit: bool = False):
        yield conn

    return patch(target, side_effect=_conn), cursor


def _cli_result(idx: int, title_head: str, tag: str) -> dict:
    """CLI 응답 JSON 원소(REQ-062-B1 title_head 포함)의 최소 fixture."""
    return {
        "idx": idx,
        "title_head": title_head,
        "classification": "company_specific",
        "impact_score": 3,
        "investment_implication": tag,
        "keywords": [],
        "sentiment": "neutral",
    }


class TestRepairImportContentAnchor:
    def test_scrambled_content_rejects_and_audits_mismatch_count(self, tmp_path):
        from trading.news.intelligence import repair as repair_mod

        results_file = tmp_path / "analysis_results.json"
        pending_file = tmp_path / "pending_analysis.json"
        repair_meta_file = tmp_path / "repair_pending_metadata.json"

        article_ids = [501, 502]
        repair_meta_file.write_text(json.dumps({
            "article_ids": article_ids, "since": "2026-01-01", "model_used": "claude-cli",
        }))
        raw = json.dumps([
            _cli_result(1, "Hyundai laun", "x"),
            _cli_result(2, "Samsung Q1 p", "y"),
        ])
        results_file.write_text(raw)

        article_titles = {
            501: "Samsung Q1 profit report",
            502: "Hyundai launches new EV",
        }

        patch_conn, cur = _make_conn_patch(
            "trading.news.intelligence.repair.connection"
        )
        # 앵커 검증(_verify_content_anchor)은 analyzer.py 에 정의돼 있어 analyzer
        # 모듈의 audit 바인딩을 통해 감사로그를 남긴다 — 두 지점 모두 같은
        # mock 으로 패치해 어느 경로든 잡히도록 한다.
        mock_audit = MagicMock()
        with (
            patch.object(repair_mod, "RESULTS_FILE", results_file),
            patch.object(repair_mod, "PENDING_FILE", pending_file),
            patch.object(repair_mod, "REPAIR_META_FILE", repair_meta_file),
            patch.object(repair_mod, "audit", new=mock_audit),
            patch("trading.news.intelligence.analyzer.audit", new=mock_audit),
            patch.object(
                repair_mod, "_fetch_articles_by_ids",
                return_value={
                    aid: {"title": title, "sector": ""}
                    for aid, title in article_titles.items()
                },
            ),
            patch_conn,
        ):
            repaired = repair_mod.import_repair_results()

        assert repaired == 0
        assert cur.upsert_calls() == []
        reject_calls = [
            c for c in mock_audit.call_args_list if c.args[0] == "NEWS_INTEL_ALIGN_REJECT"
        ]
        assert len(reject_calls) == 1
        assert reject_calls[0].kwargs["details"]["anchor_mismatch_count"] == 2
        assert reject_calls[0].kwargs["details"]["path"] == "repair_import"
        assert not results_file.exists()

    def test_matching_content_anchor_upserts_all(self, tmp_path):
        from trading.news.intelligence import repair as repair_mod

        results_file = tmp_path / "analysis_results.json"
        pending_file = tmp_path / "pending_analysis.json"
        repair_meta_file = tmp_path / "repair_pending_metadata.json"

        article_ids = [601, 602]
        repair_meta_file.write_text(json.dumps({
            "article_ids": article_ids, "since": "2026-01-01", "model_used": "claude-cli",
        }))
        raw = json.dumps([
            _cli_result(1, "Samsung Q1 p", "x"),
            _cli_result(2, "Hyundai laun", "y"),
        ])
        results_file.write_text(raw)

        article_titles = {
            601: "Samsung Q1 profit report",
            602: "Hyundai launches new EV",
        }

        patch_conn, cur = _make_conn_patch(
            "trading.news.intelligence.repair.connection"
        )
        with (
            patch.object(repair_mod, "RESULTS_FILE", results_file),
            patch.object(repair_mod, "PENDING_FILE", pending_file),
            patch.object(repair_mod, "REPAIR_META_FILE", repair_meta_file),
            patch.object(repair_mod, "audit") as mock_audit,
            patch.object(
                repair_mod, "_fetch_articles_by_ids",
                return_value={
                    aid: {"title": title, "sector": ""}
                    for aid, title in article_titles.items()
                },
            ),
            patch_conn,
        ):
            repaired = repair_mod.import_repair_results()

        assert repaired == 2
        assert len(cur.upsert_calls()) == 2
        assert all(
            call.args[0] != "NEWS_INTEL_ALIGN_REJECT" for call in mock_audit.call_args_list
        )
