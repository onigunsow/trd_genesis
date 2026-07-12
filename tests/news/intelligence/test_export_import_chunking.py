"""SPEC-TRADING-062 Stage 2 (REQ-062-C1~C4) — 호스트 CLI 청킹 검증.

2026-07-09 인시던트: 94~98개 기사 단일배치가 거의 100% 스크램블되어 하루 전량
거부됨(fail-closed는 작동했으나 throughput 0). export/import를 청크 단위(<=
HOST_CHUNK_SIZE)로 나누어 한 청크의 오염이 다른 청크 저장을 막지 않도록 한다.

기존 관례 재사용: cursor 더블(RecordingCursor/RecordingConn, test_store_alignment.py
와 동형) — mock 이 못 잡는 거짓그린 방지([[reference_integration_tests]]).
"""

from __future__ import annotations

import json
from contextlib import contextmanager
from typing import Any
from unittest.mock import patch

from trading.news.intelligence.analyzer import (
    HOST_CHUNK_SIZE,
    export_pending_for_host,
    import_host_results,
)

# ---------------------------------------------------------------------------
# cursor 더블 (실 SQL 실행 경로 검증용)
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


def _fake_article(aid: int, title: str) -> dict:
    return {
        "id": aid,
        "title": title,
        "source_name": "src",
        "sector": "",
        "body_text": "본문 내용",
        "summary": "",
        "published_at": None,
    }


def _cli_result(idx: int, title_head: str, tag: str) -> dict:
    """CLI 응답 JSON 원소(idx + title_head 포함)의 최소 fixture."""
    return {
        "idx": idx,
        "title_head": title_head,
        "classification": "company_specific",
        "impact_score": 3,
        "investment_implication": tag,
        "keywords": [],
        "sentiment": "neutral",
    }


# ---------------------------------------------------------------------------
# (a) export_pending_for_host — 45개 기사 -> 3개 청크(20/20/5), local [1..n]
# ---------------------------------------------------------------------------


class TestExportChunking:
    def test_splits_45_articles_into_20_20_5_chunks_with_local_labels(self, tmp_path):
        assert HOST_CHUNK_SIZE == 20

        articles = [_fake_article(i, f"기업 실적 발표 뉴스 {i}") for i in range(1, 46)]

        chunks_dir = tmp_path / "pending_chunks"
        results_dir = tmp_path / "analysis_chunks"
        chunks_dir.mkdir()
        results_dir.mkdir()
        # 이전 사이클의 잔재 — export 시 정리돼야 한다(REQ-062-C1).
        (chunks_dir / "chunk_00.json").write_text("stale-pending")
        (chunks_dir / "chunk_07.json").write_text("stale-pending-2")
        (results_dir / "result_00.json").write_text("stale-result")

        with (
            patch("trading.news.intelligence.analyzer._DATA_DIR", tmp_path),
            patch(
                "trading.news.intelligence.analyzer.get_unanalyzed_articles",
                return_value=articles,
            ),
            patch("trading.news.intelligence.analyzer.audit") as mock_audit,
        ):
            exported = export_pending_for_host()

        assert exported == 45

        chunk_files = sorted(chunks_dir.glob("chunk_*.json"))
        assert [f.name for f in chunk_files] == [
            "chunk_00.json", "chunk_01.json", "chunk_02.json",
        ]

        sizes = []
        for f in chunk_files:
            data = json.loads(f.read_text())
            n = len(data["article_ids"])
            sizes.append(n)
            assert data["chunk_id"] == f.stem.removeprefix("chunk_")
            # local [1..n] 라벨 — 청크마다 새로 1부터 시작해야 한다(REQ-062-C1).
            assert "[1] Title:" in data["prompt"]
            assert f"[{n}] Title:" in data["prompt"]
            assert f"[{n + 1}] Title:" not in data["prompt"]
        assert sizes == [20, 20, 5]

        # stale 잔재는 전부 제거됨 — 새 청크로만 대체.
        assert not (results_dir / "result_00.json").exists()
        assert not (chunks_dir / "chunk_07.json").exists()

        meta = json.loads((tmp_path / "pending_metadata.json").read_text())
        assert [c["chunk_id"] for c in meta["chunks"]] == ["00", "01", "02"]
        assert [len(c["article_ids"]) for c in meta["chunks"]] == [20, 20, 5]
        assert meta["chunks"][0]["article_ids"] == [a["id"] for a in articles[0:20]]
        assert meta["chunks"][1]["article_ids"] == [a["id"] for a in articles[20:40]]
        assert meta["chunks"][2]["article_ids"] == [a["id"] for a in articles[40:45]]
        assert meta["count"] == 45

        mock_audit.assert_called_once()
        event, kwargs = mock_audit.call_args[0][0], mock_audit.call_args[1]
        assert event == "NEWS_INTEL_EXPORT_PENDING"
        assert kwargs["details"]["chunks"] == 3
        assert kwargs["details"]["articles_exported"] == 45


# ---------------------------------------------------------------------------
# (a2) export가 청소 전에 완료-미수입 결과를 drain — 2026-07-13 라이브 레이스 재현
# ---------------------------------------------------------------------------


class TestExportDrainsPendingResults:
    def test_completed_results_imported_not_wiped_at_next_export(self, tmp_path):
        """레이스 재현: :15 import가 호스트의 늦은 청크 완료 전에 실행되고,
        다음 슬롯 export의 stale 청소가 완료된 결과를 폐기 — 슬롯당 1~2청크만
        저장되던 7/10~13 라이브 증상. export는 청소 전에 잔여 결과를 drain해야
        한다."""
        results_dir = tmp_path / "analysis_chunks"
        results_dir.mkdir()

        # 이전 슬롯의 메타데이터 + 호스트가 늦게 완료한 결과 파일.
        (tmp_path / "pending_metadata.json").write_text(json.dumps({
            "chunks": [{"chunk_id": "00", "article_ids": [101, 102]}],
            "exported_at": "2026-07-13T00:00:00",
            "count": 2,
        }))
        titles = {101: "Samsung Q1 profit report", 102: "Hyundai launches new EV"}
        (results_dir / "result_00.json").write_text(json.dumps([
            _cli_result(1, "Samsung Q1 p", "a"),
            _cli_result(2, "Hyundai laun", "b"),
        ]))

        new_articles = [_fake_article(i, f"새 뉴스 {i}") for i in range(201, 204)]

        patch_conn, cur = _make_conn_patch(
            "trading.news.intelligence.analyzer.connection"
        )
        with (
            patch("trading.news.intelligence.analyzer._DATA_DIR", tmp_path),
            patch(
                "trading.news.intelligence.analyzer.RESULTS_FILE",
                tmp_path / "analysis_results.json",
            ),
            patch(
                "trading.news.intelligence.analyzer.get_unanalyzed_articles",
                return_value=new_articles,
            ),
            patch_conn,
            patch("trading.news.intelligence.analyzer.audit"),
            patch(
                "trading.news.intelligence.analyzer._fetch_articles_by_ids",
                side_effect=lambda ids: {
                    aid: {"title": titles[aid], "sector": ""} for aid in ids
                },
            ),
        ):
            exported = export_pending_for_host()

        # 완료돼 있던 이전 슬롯 결과는 폐기가 아니라 저장돼야 한다.
        inserted_ids = {p[0] for p in cur.insert_calls()}
        assert inserted_ids == {101, 102}

        # 새 export도 정상 진행.
        assert exported == 3
        assert [f.name for f in sorted((tmp_path / "pending_chunks").glob("*.json"))] \
            == ["chunk_00.json"]


# ---------------------------------------------------------------------------
# (b) import_host_results — 청크 하나 스크램블 -> 그 청크만 거부, 나머지는 저장
# ---------------------------------------------------------------------------


class TestImportChunkedResults:
    @staticmethod
    def _write_meta(tmp_path, chunks):
        (tmp_path / "pending_metadata.json").write_text(json.dumps({
            "chunks": chunks,
            "exported_at": "2026-07-09T00:00:00",
            "count": sum(len(c["article_ids"]) for c in chunks),
        }))

    def test_one_scrambled_chunk_rejected_others_stored_aggregate_correct(self, tmp_path):
        results_dir = tmp_path / "analysis_chunks"
        results_dir.mkdir()

        chunks_meta = [
            {"chunk_id": "00", "article_ids": [101, 102]},
            {"chunk_id": "01", "article_ids": [103, 104]},
            {"chunk_id": "02", "article_ids": [105, 106]},
        ]
        self._write_meta(tmp_path, chunks_meta)

        titles = {
            101: "Samsung Q1 profit report",
            102: "Hyundai launches new EV",
            103: "LG display unit expands",
            104: "SK hynix chip output up",
            105: "Naver cloud expansion news",
            106: "Kakao pay service growth",
        }

        (results_dir / "result_00.json").write_text(json.dumps([
            _cli_result(1, "Samsung Q1 p", "a"),
            _cli_result(2, "Hyundai laun", "b"),
        ]))
        # chunk 01: idx 는 완전한 순열(1,2)이나 title_head 가 뒤바뀜 — 제2 실패모드.
        (results_dir / "result_01.json").write_text(json.dumps([
            _cli_result(1, "SK hynix chi", "wrong-c"),
            _cli_result(2, "LG display u", "wrong-d"),
        ]))
        (results_dir / "result_02.json").write_text(json.dumps([
            _cli_result(1, "Naver cloud ", "e"),
            _cli_result(2, "Kakao pay se", "f"),
        ]))

        patch_conn, cur = _make_conn_patch(
            "trading.news.intelligence.analyzer.connection"
        )
        with (
            patch("trading.news.intelligence.analyzer._DATA_DIR", tmp_path),
            patch(
                "trading.news.intelligence.analyzer.RESULTS_FILE",
                tmp_path / "analysis_results.json",
            ),
            patch_conn,
            patch("trading.news.intelligence.analyzer.audit") as mock_audit,
            patch(
                "trading.news.intelligence.analyzer._fetch_articles_by_ids",
                side_effect=lambda ids: {
                    aid: {"title": titles[aid], "sector": ""} for aid in ids
                },
            ),
        ):
            count = import_host_results()

        assert count == 4  # chunk00(2) + chunk02(2); chunk01 거부(0)

        inserted_ids = {p[0] for p in cur.insert_calls()}
        assert inserted_ids == {101, 102, 105, 106}

        reject_calls = [
            c for c in mock_audit.call_args_list if c.args[0] == "NEWS_INTEL_ALIGN_REJECT"
        ]
        assert len(reject_calls) == 1
        assert reject_calls[0].kwargs["details"]["chunk_id"] == "01"
        assert reject_calls[0].kwargs["details"]["anchor_mismatch_count"] == 2

        ok_calls = [
            c for c in mock_audit.call_args_list if c.args[0] == "NEWS_INTEL_IMPORT_OK"
        ]
        assert len(ok_calls) == 1
        assert ok_calls[0].kwargs["details"] == {
            "chunks_ok": 2,
            "chunks_rejected": 1,
            "articles_imported": 4,
            "articles_rejected": 2,
        }

        # 소비된 결과 파일은 성공/거부 무관하게 삭제(REQ-062-C3).
        assert not (results_dir / "result_00.json").exists()
        assert not (results_dir / "result_01.json").exists()
        assert not (results_dir / "result_02.json").exists()
        # 모든 청크가 해소됐으므로 메타데이터도 정리된다.
        assert not (tmp_path / "pending_metadata.json").exists()

    def test_missing_chunk_result_still_imports_others_and_keeps_it_pending(self, tmp_path):
        results_dir = tmp_path / "analysis_chunks"
        results_dir.mkdir()

        chunks_meta = [
            {"chunk_id": "00", "article_ids": [201, 202]},
            {"chunk_id": "01", "article_ids": [203, 204]},  # 호스트가 아직 처리 못함
        ]
        self._write_meta(tmp_path, chunks_meta)

        titles = {201: "Title A goes here", 202: "Title B goes here"}
        (results_dir / "result_00.json").write_text(json.dumps([
            _cli_result(1, "Title A goes", "a"),
            _cli_result(2, "Title B goes", "b"),
        ]))
        # result_01.json 없음 — 호스트 실패/미처리.

        patch_conn, cur = _make_conn_patch(
            "trading.news.intelligence.analyzer.connection"
        )
        with (
            patch("trading.news.intelligence.analyzer._DATA_DIR", tmp_path),
            patch(
                "trading.news.intelligence.analyzer.RESULTS_FILE",
                tmp_path / "analysis_results.json",
            ),
            patch_conn,
            patch("trading.news.intelligence.analyzer.audit") as mock_audit,
            patch(
                "trading.news.intelligence.analyzer._fetch_articles_by_ids",
                side_effect=lambda ids: {
                    aid: {"title": titles.get(aid, ""), "sector": ""} for aid in ids
                },
            ),
        ):
            count = import_host_results()

        assert count == 2
        assert {p[0] for p in cur.insert_calls()} == {201, 202}

        # chunk 01 은 다음 슬롯 재시도 대상으로 메타데이터에 남는다.
        meta = json.loads((tmp_path / "pending_metadata.json").read_text())
        assert [c["chunk_id"] for c in meta["chunks"]] == ["01"]
        assert meta["chunks"][0]["article_ids"] == [203, 204]

        ok_calls = [
            c for c in mock_audit.call_args_list if c.args[0] == "NEWS_INTEL_IMPORT_OK"
        ]
        assert len(ok_calls) == 1
        assert ok_calls[0].kwargs["details"] == {
            "chunks_ok": 1,
            "chunks_rejected": 0,
            "articles_imported": 2,
            "articles_rejected": 0,
        }


# ---------------------------------------------------------------------------
# (d) 레거시 단일배치 하위호환 — 기존 경로로 1회 흡수(REQ-062-C4)
# ---------------------------------------------------------------------------


class TestLegacyFallbackOnce:
    def test_legacy_results_imported_once_via_existing_path(self, tmp_path):
        results_file = tmp_path / "analysis_results.json"
        pending_file = tmp_path / "pending_analysis.json"
        meta_file = tmp_path / "pending_metadata.json"

        article_ids = [601, 602]
        meta_file.write_text(json.dumps({"article_ids": article_ids}))
        raw = json.dumps([
            {"idx": 1, "classification": "company_specific", "impact_score": 3,
             "investment_implication": "A", "keywords": ["A"], "sentiment": "neutral"},
            {"idx": 2, "classification": "company_specific", "impact_score": 3,
             "investment_implication": "B", "keywords": ["B"], "sentiment": "neutral"},
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

        assert count == 2
        assert {p[0] for p in cur.insert_calls()} == {601, 602}
        assert not results_file.exists()
        assert not meta_file.exists()
        assert not pending_file.exists()

        ok_calls = [
            c for c in mock_audit.call_args_list if c.args[0] == "NEWS_INTEL_IMPORT_OK"
        ]
        assert len(ok_calls) == 1
        # 레거시 경로(기존 그대로) 상세 형태 — 신규 청크 집계 형태가 아님을 확인.
        assert "results_parsed" in ok_calls[0].kwargs["details"]
        assert "chunks_ok" not in ok_calls[0].kwargs["details"]
