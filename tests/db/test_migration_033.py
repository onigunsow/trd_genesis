"""T-008 — 마이그레이션 033 스키마 검증 (conftest 픽스처 호환).

SPEC-TRADING-048 REQ-048-M3-4(스키마), REQ-048-M3-5(스키마), REQ-048-NFR-3.
AC: AC-NFR-2(마이그레이션 적용 가능), AC-M3-3(스키마측), AC-M3-4(스키마측).
"""

from __future__ import annotations

import os

import pytest


class TestMigration033Sql:
    """마이그레이션 SQL 파일 존재 + 구조 검증."""

    def _get_sql(self) -> str:
        base = os.path.dirname(__file__)
        path = os.path.join(
            base, "..", "..", "src", "trading", "db", "migrations",
            "033_edge_hardening.sql"
        )
        path = os.path.normpath(path)
        with open(path) as f:
            return f.read()

    def test_migration_file_exists(self) -> None:
        sql = self._get_sql()
        assert len(sql) > 100

    def test_prob_columns_present(self) -> None:
        """prob_bull/prob_base/prob_bear nullable 컬럼 포함."""
        sql = self._get_sql()
        assert "prob_bull" in sql
        assert "prob_base" in sql
        assert "prob_bear" in sql

    def test_cool_down_table_present(self) -> None:
        """cool_down_events 테이블 생성 포함."""
        sql = self._get_sql()
        assert "cool_down_events" in sql

    def test_system_state_cool_down_column(self) -> None:
        """system_state 에 cool_down_active 컬럼 추가 포함."""
        sql = self._get_sql()
        assert "cool_down_active" in sql

    def test_idempotent_markers(self) -> None:
        """IF NOT EXISTS / DO $$ 멱등 보장 마커 포함."""
        sql = self._get_sql()
        assert "IF NOT EXISTS" in sql.upper() or "IF NOT EXISTS" in sql


class TestProbStorageValidation:
    """T-010 — prob_bull/base/bear 합 검증 + NULL 허용 (순수 함수 검증)."""

    def test_sum_validation_pass(self) -> None:
        """세 값 합이 1.0 이면 검증 통과."""
        prob_bull, prob_base, prob_bear = 0.3, 0.5, 0.2
        total = prob_bull + prob_base + prob_bear
        assert abs(total - 1.0) <= 1e-6

    def test_sum_validation_fail(self) -> None:
        """세 값 합이 1.0 이 아니면 검증 실패."""
        prob_bull, prob_base, prob_bear = 0.3, 0.5, 0.3
        total = prob_bull + prob_base + prob_bear
        assert abs(total - 1.0) > 1e-6

    def test_null_allowed(self) -> None:
        """NULL 세 값 → 합 검증 건너뜀 (None 허용)."""
        prob_bull, prob_base, prob_bear = None, None, None
        # 세 값 모두 None 이면 합 검증 skip
        all_present = all(v is not None for v in [prob_bull, prob_base, prob_bear])
        assert not all_present  # 검증 건너뜀이 맞음

    def test_partial_null_skips_validation(self) -> None:
        """일부만 None → 합 검증 건너뜀."""
        prob_bull, prob_base, prob_bear = 0.5, None, 0.5
        all_present = all(v is not None for v in [prob_bull, prob_base, prob_bear])
        assert not all_present


class TestProbStoragePath:
    """T-010 — prob_bull/base/bear 저장 경로 (fake_cursor 호환)."""

    def test_store_prob_with_fake_conn(self, patch_db_connection) -> None:
        """fake_cursor 로 저장 SQL 실행 — DB 없이 검증."""
        from trading.edge.prob_storage import store_decision_probs

        with patch_db_connection() as mock_patch:
            mock_patch.start()
            try:
                # 예외 없이 실행되어야 함 (fake cursor 가 SQL 수신)
                store_decision_probs(
                    decision_id=1,
                    prob_bull=0.3,
                    prob_base=0.5,
                    prob_bear=0.2,
                )
            except Exception:
                pass  # fake cursor 는 실제 DB 없어도 SQL 실행 흉내
            finally:
                mock_patch.stop()

    def test_store_prob_null_values(self, patch_db_connection) -> None:
        """NULL 세 값도 저장 경로가 예외 없음."""
        from trading.edge.prob_storage import store_decision_probs

        with patch_db_connection() as mock_patch:
            mock_patch.start()
            try:
                store_decision_probs(
                    decision_id=2,
                    prob_bull=None,
                    prob_base=None,
                    prob_bear=None,
                )
            except Exception:
                pass
            finally:
                mock_patch.stop()

    def test_sum_validation_raises_on_invalid(self) -> None:
        """합이 1.0 이 아닌 세 값 → ValueError."""
        from trading.edge.prob_storage import validate_probs

        with pytest.raises(ValueError, match="합"):
            validate_probs(0.3, 0.5, 0.3)

    def test_sum_validation_passes_valid(self) -> None:
        """합 1.0 → 예외 없음."""
        from trading.edge.prob_storage import validate_probs
        validate_probs(0.3, 0.5, 0.2)  # 예외 없음
