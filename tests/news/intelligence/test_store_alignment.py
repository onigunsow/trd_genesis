"""SPEC-TRADING-061 REQ-061-2/3 — 저장 경로(CLI import / Haiku store)의 ID 기반
정렬 배선 검증. dict 직접 주입이 아니라 SQL 실행 경로(cursor 더블)로 검증한다
(project memory: mock 이 못 잡는 거짓그린 방지, [[reference_integration_tests]] 계열
관례 — tests/kis/test_ghost_convergence.py 의 MultiCursor 패턴 재사용).
"""

from __future__ import annotations

import json
from contextlib import contextmanager
from typing import Any
from unittest.mock import patch

from trading.news.intelligence.analyzer import (
    _store_results,
    import_host_results,
)

# ---------------------------------------------------------------------------
# cursor 더블 (실 SQL 실행 경로 검증용, test_ghost_convergence.py 와 동형)
# ---------------------------------------------------------------------------


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

    def insert_calls(self) -> list[tuple]:
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


def _result(idx: int, tag: str) -> dict:
    return {
        "idx": idx,
        "summary_2line": tag,
        "impact_score": 3,
        "keywords": [tag],
        "sentiment": "neutral",
        "classification": "company_specific",
        "sector": "",
    }


class TestStoreResultsAlignment:
    """RC2 — _store_results (Haiku 폴백 저장 루프)."""

    def test_reordered_results_store_correct_article_pairing(self):
        articles = [
            {"id": 101, "title": "A", "sector": ""},
            {"id": 102, "title": "B", "sector": ""},
            {"id": 103, "title": "C", "sector": ""},
        ]
        # LLM 이 순서를 뒤섞어 반환 — idx 로만 정렬돼야 한다.
        results = [_result(2, "B"), _result(3, "C"), _result(1, "A")]

        patch_conn, cur = _make_conn_patch(
            "trading.news.intelligence.analyzer.connection"
        )
        with patch_conn, patch("trading.news.intelligence.analyzer.audit") as mock_audit:
            stored = _store_results(articles, results, in_tok=300, out_tok=150)

        assert len(stored) == 3
        by_id = {r.article_id: r.summary_2line for r in stored}
        assert by_id == {101: "A", 102: "B", 103: "C"}

        insert_by_id = {p[0]: p[1] for p in cur.insert_calls()}
        assert insert_by_id[101] == "A"
        assert insert_by_id[102] == "B"
        assert insert_by_id[103] == "C"
        mock_audit.assert_not_called()

    def test_misaligned_results_store_nothing_and_audit_reject(self):
        articles = [
            {"id": 101, "title": "A", "sector": ""},
            {"id": 102, "title": "B", "sector": ""},
        ]
        # idx=2 중복, idx=1 없음(REQ-061-3 duplicate/missing)
        results = [_result(2, "B1"), _result(2, "B2")]

        patch_conn, cur = _make_conn_patch(
            "trading.news.intelligence.analyzer.connection"
        )
        with patch_conn, patch("trading.news.intelligence.analyzer.audit") as mock_audit:
            stored = _store_results(articles, results, in_tok=200, out_tok=100)

        assert stored == []
        assert cur.insert_calls() == []  # fail-closed — 아무것도 저장하지 않음
        mock_audit.assert_called_once()
        event, kwargs = mock_audit.call_args[0][0], mock_audit.call_args[1]
        assert event == "NEWS_INTEL_ALIGN_REJECT"
        assert kwargs["details"]["duplicate_count"] == 1
        assert kwargs["details"]["missing_count"] == 1


class TestImportHostResultsAlignment:
    """RC1 — import_host_results (CLI import 저장 루프, 활성 정본 경로)."""

    def test_reordered_cli_results_align_by_id_not_position(self, tmp_path):
        results_file = tmp_path / "analysis_results.json"
        pending_file = tmp_path / "pending_analysis.json"

        article_ids = [301, 302, 303]
        (tmp_path / "pending_metadata.json").write_text(
            json.dumps({"article_ids": article_ids})
        )
        # 파서가 REORDER 된 결과를 돌려준다 — echo idx 로만 정렬돼야 한다.
        raw = json.dumps([
            {"idx": 2, "classification": "company_specific", "impact_score": 3,
             "investment_implication": "B", "keywords": ["B"], "sentiment": "neutral"},
            {"idx": 3, "classification": "company_specific", "impact_score": 3,
             "investment_implication": "C", "keywords": ["C"], "sentiment": "neutral"},
            {"idx": 1, "classification": "company_specific", "impact_score": 3,
             "investment_implication": "A", "keywords": ["A"], "sentiment": "neutral"},
        ])
        results_file.write_text(raw)

        patch_conn, cur = _make_conn_patch(
            "trading.news.intelligence.analyzer.connection"
        )
        with (
            patch("trading.news.intelligence.analyzer.RESULTS_FILE", results_file),
            patch("trading.news.intelligence.analyzer.PENDING_FILE", pending_file),
            patch("trading.news.intelligence.analyzer._DATA_DIR", tmp_path),
            patch_conn,
            patch("trading.news.intelligence.analyzer.audit") as mock_audit,
            patch(
                "trading.news.intelligence.analyzer._fetch_articles_by_ids",
                return_value={aid: {"title": "", "sector": ""} for aid in article_ids},
            ),
        ):
            count = import_host_results()

        assert count == 3
        insert_by_id = {p[0]: p[1] for p in cur.insert_calls()}
        assert insert_by_id[301] == "A"
        assert insert_by_id[302] == "B"
        assert insert_by_id[303] == "C"
        # 성공 경로는 ALIGN_REJECT 를 발행하지 않는다 (IMPORT_OK 만).
        assert all(
            call.args[0] != "NEWS_INTEL_ALIGN_REJECT" for call in mock_audit.call_args_list
        )

    def test_id_mismatch_rejects_all_and_audits(self, tmp_path):
        results_file = tmp_path / "analysis_results.json"
        pending_file = tmp_path / "pending_analysis.json"

        article_ids = [401, 402]
        (tmp_path / "pending_metadata.json").write_text(
            json.dumps({"article_ids": article_ids})
        )
        # idx=1 만 있고 idx=2 는 없음 (missing) — fail-closed.
        raw = json.dumps([
            {"idx": 1, "classification": "company_specific", "impact_score": 3,
             "investment_implication": "A", "keywords": ["A"], "sentiment": "neutral"},
        ])
        results_file.write_text(raw)

        patch_conn, cur = _make_conn_patch(
            "trading.news.intelligence.analyzer.connection"
        )
        with (
            patch("trading.news.intelligence.analyzer.RESULTS_FILE", results_file),
            patch("trading.news.intelligence.analyzer.PENDING_FILE", pending_file),
            patch("trading.news.intelligence.analyzer._DATA_DIR", tmp_path),
            patch_conn,
            patch("trading.news.intelligence.analyzer.audit") as mock_audit,
        ):
            count = import_host_results()

        assert count == 0
        assert cur.insert_calls() == []
        reject_calls = [
            c for c in mock_audit.call_args_list if c.args[0] == "NEWS_INTEL_ALIGN_REJECT"
        ]
        assert len(reject_calls) == 1
        assert reject_calls[0].kwargs["details"]["missing_count"] == 1
        # 오염 배치를 소비해 다음 슬롯에서 무한 재시도되지 않도록 정리한다.
        assert not results_file.exists()


class TestStoreResultsContentAnchor:
    """SPEC-TRADING-062 REQ-062-B2 — idx 정렬은 통과하지만 content-anchor 불일치
    (제2 실패모드: 완전한 idx 순열이되 내용이 뒤바뀐 경우, 2026-07-08 인시던트)."""

    @staticmethod
    def _r(idx: int, title_head: str, tag: str) -> dict:
        r = _result(idx, tag)
        r["title_head"] = title_head
        return r

    def test_scrambled_content_with_valid_idx_rejects_all(self):
        articles = [
            {"id": 101, "title": "Samsung Q1 profit report", "sector": ""},
            {"id": 102, "title": "Hyundai launches new EV", "sector": ""},
            {"id": 103, "title": "LG display unit expands", "sector": ""},
        ]
        # idx 는 완전한 순열(1,2,3) — SPEC-061 정렬은 통과하지만 title_head 가
        # 뒤바뀐 기사 내용을 가리킨다.
        results = [
            self._r(1, "Hyundai laun", "wrong-A"),
            self._r(2, "LG display u", "wrong-B"),
            self._r(3, "Samsung Q1 p", "wrong-C"),
        ]

        patch_conn, cur = _make_conn_patch(
            "trading.news.intelligence.analyzer.connection"
        )
        with patch_conn, patch("trading.news.intelligence.analyzer.audit") as mock_audit:
            stored = _store_results(articles, results, in_tok=300, out_tok=150)

        assert stored == []
        assert cur.insert_calls() == []
        mock_audit.assert_called_once()
        event, kwargs = mock_audit.call_args[0][0], mock_audit.call_args[1]
        assert event == "NEWS_INTEL_ALIGN_REJECT"
        assert kwargs["details"]["anchor_mismatch_count"] == 3
        assert kwargs["details"]["path"] == "haiku_store"

    def test_matching_content_anchor_stores_all(self):
        articles = [
            {"id": 101, "title": "Samsung Q1 profit report", "sector": ""},
            {"id": 102, "title": "Hyundai launches new EV", "sector": ""},
        ]
        results = [
            self._r(1, "Samsung Q1 p", "A"),
            self._r(2, "Hyundai laun", "B"),
        ]

        patch_conn, cur = _make_conn_patch(
            "trading.news.intelligence.analyzer.connection"
        )
        with patch_conn, patch("trading.news.intelligence.analyzer.audit") as mock_audit:
            stored = _store_results(articles, results, in_tok=300, out_tok=150)

        assert len(stored) == 2
        assert len(cur.insert_calls()) == 2
        mock_audit.assert_not_called()

    def test_missing_title_head_backward_compat_not_rejected(self):
        """REQ-062-B3: title_head 없는(구버전) 결과는 앵커 부재만으로 거부하지 않는다."""
        articles = [
            {"id": 101, "title": "Samsung Q1 profit report", "sector": ""},
            {"id": 102, "title": "Hyundai launches new EV", "sector": ""},
        ]
        results = [_result(1, "A"), _result(2, "B")]  # title_head 필드 자체가 없음

        patch_conn, _cur = _make_conn_patch(
            "trading.news.intelligence.analyzer.connection"
        )
        with patch_conn, patch("trading.news.intelligence.analyzer.audit") as mock_audit:
            stored = _store_results(articles, results, in_tok=300, out_tok=150)

        assert len(stored) == 2
        mock_audit.assert_not_called()


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


class TestImportHostResultsContentAnchor:
    """SPEC-TRADING-062 REQ-062-B2 — CLI import 경로 content-anchor 배선."""

    def test_scrambled_content_rejects_and_audits_mismatch_count(self, tmp_path):
        results_file = tmp_path / "analysis_results.json"
        pending_file = tmp_path / "pending_analysis.json"

        article_ids = [301, 302, 303]
        (tmp_path / "pending_metadata.json").write_text(
            json.dumps({"article_ids": article_ids})
        )
        raw = json.dumps([
            _cli_result(1, "Hyundai laun", "x"),
            _cli_result(2, "LG display u", "y"),
            _cli_result(3, "Samsung Q1 p", "z"),
        ])
        results_file.write_text(raw)

        article_titles = {
            301: "Samsung Q1 profit report",
            302: "Hyundai launches new EV",
            303: "LG display unit expands",
        }

        patch_conn, cur = _make_conn_patch(
            "trading.news.intelligence.analyzer.connection"
        )
        with (
            patch("trading.news.intelligence.analyzer.RESULTS_FILE", results_file),
            patch("trading.news.intelligence.analyzer.PENDING_FILE", pending_file),
            patch("trading.news.intelligence.analyzer._DATA_DIR", tmp_path),
            patch_conn,
            patch("trading.news.intelligence.analyzer.audit") as mock_audit,
            patch(
                "trading.news.intelligence.analyzer._fetch_articles_by_ids",
                return_value={
                    aid: {"title": title, "sector": ""}
                    for aid, title in article_titles.items()
                },
            ),
        ):
            count = import_host_results()

        assert count == 0
        assert cur.insert_calls() == []
        reject_calls = [
            c for c in mock_audit.call_args_list if c.args[0] == "NEWS_INTEL_ALIGN_REJECT"
        ]
        assert len(reject_calls) == 1
        assert reject_calls[0].kwargs["details"]["anchor_mismatch_count"] == 3
        assert reject_calls[0].kwargs["details"]["path"] == "cli_import"
        # 오염 배치를 소비해 다음 슬롯 재시도를 막는다(기존 idx-mismatch 경로와 동일 정책).
        assert not results_file.exists()

    def test_matching_content_anchor_imports_all(self, tmp_path):
        results_file = tmp_path / "analysis_results.json"
        pending_file = tmp_path / "pending_analysis.json"

        article_ids = [401, 402]
        (tmp_path / "pending_metadata.json").write_text(
            json.dumps({"article_ids": article_ids})
        )
        raw = json.dumps([
            _cli_result(1, "Samsung Q1 p", "x"),
            _cli_result(2, "Hyundai laun", "y"),
        ])
        results_file.write_text(raw)

        article_titles = {
            401: "Samsung Q1 profit report",
            402: "Hyundai launches new EV",
        }

        patch_conn, _cur = _make_conn_patch(
            "trading.news.intelligence.analyzer.connection"
        )
        with (
            patch("trading.news.intelligence.analyzer.RESULTS_FILE", results_file),
            patch("trading.news.intelligence.analyzer.PENDING_FILE", pending_file),
            patch("trading.news.intelligence.analyzer._DATA_DIR", tmp_path),
            patch_conn,
            patch("trading.news.intelligence.analyzer.audit") as mock_audit,
            patch(
                "trading.news.intelligence.analyzer._fetch_articles_by_ids",
                return_value={
                    aid: {"title": title, "sector": ""}
                    for aid, title in article_titles.items()
                },
            ),
        ):
            count = import_host_results()

        assert count == 2
        assert all(
            call.args[0] != "NEWS_INTEL_ALIGN_REJECT" for call in mock_audit.call_args_list
        )
