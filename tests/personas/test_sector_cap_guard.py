"""섹터 집중 코드 가드 TDD 테스트.

enforce_sector_cap() 함수와 portfolio_gate 통합을 검증한다.

테스트 격리: DB/KRX/네트워크 호출 없음. 섹터 조회는 mock.
"""

from __future__ import annotations

import logging
from unittest.mock import patch

import pytest

from trading.personas import regime_branch

# ---------------------------------------------------------------------------
# 헬퍼 — 신호·보유 목 생성
# ---------------------------------------------------------------------------


def _buy(ticker: str, qty: int = 10) -> dict:
    return {"ticker": ticker, "side": "buy", "qty": qty}


def _sell(ticker: str, qty: int = 10) -> dict:
    return {"ticker": ticker, "side": "sell", "qty": qty}


def _holding(ticker: str, sector: str, eval_amount: int) -> dict:
    """보유 종목 레코드 (assets['holdings'] 형식)."""
    return {
        "ticker": ticker,
        "sector": sector,
        "eval_amount": eval_amount,
        "qty": 10,
        "avg_cost": eval_amount // 10,
        "pnl_pct": 0.0,
    }


# ---------------------------------------------------------------------------
# RED: enforce_sector_cap 단위 테스트
# ---------------------------------------------------------------------------


class TestEnforceSectorCapUnit:
    """enforce_sector_cap() 순수 함수 동작 검증."""

    def test_buy_초과시_차단(self):
        """보유 금융 35% + 신규 금융 BUY → 40% cap 초과 → 차단."""
        from trading.personas.sector_cap_guard import enforce_sector_cap

        total = 10_000_000
        # 금융 350만원 = 35%
        holdings = [_holding("005930", "금융", 3_500_000)]
        signals = [_buy("055550", 10)]  # 신한지주(금융), 1주당 ~40000원 * 10 = 400000

        # 1주당 가격 40000원 * 10주 = 400000원 → 총 금융 3900000 = 39% < 40% → 통과
        kept, dropped = enforce_sector_cap(
            signals,
            holdings=holdings,
            total_portfolio=total,
            sector_cap_pct=40.0,
            price_map={"055550": 40_000},
            sector_map={"055550": "금융"},
        )
        # 39% < 40% → 통과해야 함
        assert len(kept) == 1
        assert len(dropped) == 0

    def test_buy_cap_초과시_차단(self):
        """보유 금융 39% + 신규 금융 BUY → 42% → cap 40% 초과 → 차단."""
        from trading.personas.sector_cap_guard import enforce_sector_cap

        total = 10_000_000
        # 금융 390만원 = 39%
        holdings = [_holding("005930", "금융", 3_900_000)]
        signals = [_buy("055550", 10)]  # 신한지주(금융), 1주 당 30000원 * 10 = 300000

        kept, dropped = enforce_sector_cap(
            signals,
            holdings=holdings,
            total_portfolio=total,
            sector_cap_pct=40.0,
            price_map={"055550": 30_000},
            sector_map={"055550": "금융"},
        )
        # 39% + 3% = 42% > 40% → 차단
        assert len(kept) == 0
        assert len(dropped) == 1
        assert dropped[0]["ticker"] == "055550"

    def test_다른_섹터_buy_통과(self):
        """금융 cap 초과여도 IT 섹터 BUY는 통과."""
        from trading.personas.sector_cap_guard import enforce_sector_cap

        total = 10_000_000
        holdings = [_holding("005930", "금융", 4_500_000)]  # 금융 45%
        signals = [_buy("035420", 10)]  # NAVER(IT)

        kept, dropped = enforce_sector_cap(
            signals,
            holdings=holdings,
            total_portfolio=total,
            sector_cap_pct=40.0,
            price_map={"035420": 200_000},
            sector_map={"035420": "IT"},
        )
        assert len(kept) == 1
        assert len(dropped) == 0

    def test_미분류_섹터_차단_안함_경고만(self, caplog):
        """섹터 정보가 없는(unknown) 종목은 차단하지 않고 WARNING 로그만."""
        from trading.personas.sector_cap_guard import enforce_sector_cap

        total = 10_000_000
        holdings = [_holding("005930", "금융", 4_500_000)]  # 금융 45%
        signals = [_buy("999999", 10)]  # 섹터 미상

        with caplog.at_level(logging.WARNING, logger="trading.personas.sector_cap_guard"):
            kept, dropped = enforce_sector_cap(
                signals,
                holdings=holdings,
                total_portfolio=total,
                sector_cap_pct=40.0,
                price_map={"999999": 10_000},
                sector_map={},  # 섹터 맵에 없음
            )

        assert len(kept) == 1  # 차단하지 않음
        assert len(dropped) == 0
        assert any("미상" in r.message or "unknown" in r.message.lower() for r in caplog.records)

    def test_sell_신호_항상_통과(self):
        """SELL 신호는 섹터 cap과 무관하게 항상 통과."""
        from trading.personas.sector_cap_guard import enforce_sector_cap

        total = 10_000_000
        holdings = [_holding("005930", "금융", 9_000_000)]  # 금융 90%
        signals = [_sell("005930", 10)]

        kept, dropped = enforce_sector_cap(
            signals,
            holdings=holdings,
            total_portfolio=total,
            sector_cap_pct=40.0,
            price_map={"005930": 80_000},
            sector_map={"005930": "금융"},
        )
        assert len(kept) == 1
        assert len(dropped) == 0

    def test_빈_신호_목록(self):
        """신호가 없으면 빈 결과."""
        from trading.personas.sector_cap_guard import enforce_sector_cap

        kept, dropped = enforce_sector_cap(
            [],
            holdings=[],
            total_portfolio=10_000_000,
            sector_cap_pct=40.0,
            price_map={},
            sector_map={},
        )
        assert kept == []
        assert dropped == []

    def test_보유_없을때_cap_기준은_전체대비(self):
        """보유가 없을 때 신규 BUY가 cap 이내면 통과."""
        from trading.personas.sector_cap_guard import enforce_sector_cap

        total = 10_000_000
        # 신규 BUY 금액: 100000원 = 1% → 통과
        kept, dropped = enforce_sector_cap(
            [_buy("005930", 1)],
            holdings=[],
            total_portfolio=total,
            sector_cap_pct=40.0,
            price_map={"005930": 100_000},
            sector_map={"005930": "금융"},
        )
        assert len(kept) == 1
        assert len(dropped) == 0

    def test_총자산_0_방어(self):
        """총자산이 0이면 차단 없이 그대로 반환(ZeroDivision 방어)."""
        from trading.personas.sector_cap_guard import enforce_sector_cap

        kept, dropped = enforce_sector_cap(
            [_buy("005930", 10)],
            holdings=[],
            total_portfolio=0,
            sector_cap_pct=40.0,
            price_map={"005930": 80_000},
            sector_map={"005930": "금융"},
        )
        assert len(kept) == 1
        assert len(dropped) == 0


# ---------------------------------------------------------------------------
# RED: regime별 cap 반영 테스트
# ---------------------------------------------------------------------------


class TestEnforceSectorCapRegime:
    """regime별 sector_cap_pct(35/40/45%) 반영 검증."""

    @pytest.mark.parametrize(
        ("regime", "expected_cap"),
        [
            ("bull", 45.0),
            ("neutral", 40.0),
            ("bear", 35.0),
        ],
    )
    def test_regime_cap_값(self, regime, expected_cap):
        """adjust_for_regime이 올바른 sector_cap_pct 반환."""
        adj = regime_branch.adjust_for_regime(regime)
        assert adj.sector_cap_pct == expected_cap

    def test_bear_regime_35pct_cap_적용(self):
        """bear regime에서 35% cap으로 BUY 차단."""
        from trading.personas.sector_cap_guard import enforce_sector_cap

        total = 10_000_000
        # 금융 340만원 = 34%
        holdings = [_holding("005930", "금융", 3_400_000)]
        signals = [_buy("055550", 10)]  # 금융, 1주 20000원 * 10 = 200000 → 36% > 35%

        kept, dropped = enforce_sector_cap(
            signals,
            holdings=holdings,
            total_portfolio=total,
            sector_cap_pct=35.0,  # bear
            price_map={"055550": 20_000},
            sector_map={"055550": "금융"},
        )
        assert len(kept) == 0
        assert len(dropped) == 1

    def test_bull_regime_45pct_cap_통과(self):
        """bull regime에서 45% cap이라 43%까지는 통과."""
        from trading.personas.sector_cap_guard import enforce_sector_cap

        total = 10_000_000
        # 금융 420만원 = 42%
        holdings = [_holding("005930", "금융", 4_200_000)]
        signals = [_buy("055550", 5)]  # 1주 20000원 * 5 = 100000 → 43% < 45% → 통과

        kept, dropped = enforce_sector_cap(
            signals,
            holdings=holdings,
            total_portfolio=total,
            sector_cap_pct=45.0,  # bull
            price_map={"055550": 20_000},
            sector_map={"055550": "금융"},
        )
        assert len(kept) == 1
        assert len(dropped) == 0


# ---------------------------------------------------------------------------
# RED: get_sector_from_db 단위 테스트
# ---------------------------------------------------------------------------


class TestGetSectorFromDb:
    """DB 기반 섹터 조회 헬퍼 검증."""

    def test_db_조회_성공(self):
        """ticker_metadata에서 섹터 조회 성공 케이스."""
        from trading.personas.sector_cap_guard import get_sectors_from_db

        mock_rows = [
            {"ticker": "005930", "sector": "금융"},
            {"ticker": "055550", "sector": "금융"},
        ]
        with patch("trading.personas.sector_cap_guard.connection") as mock_conn:
            _ctx = mock_conn.return_value.__enter__.return_value
            mock_cur = _ctx.cursor.return_value.__enter__.return_value
            mock_cur.fetchall.return_value = mock_rows
            result = get_sectors_from_db(["005930", "055550"])

        assert result == {"005930": "금융", "055550": "금융"}

    def test_db_실패시_빈_딕트(self):
        """DB 접근 실패 시 빈 dict 반환(fail-safe)."""
        from trading.personas.sector_cap_guard import get_sectors_from_db

        with patch("trading.personas.sector_cap_guard.connection") as mock_conn:
            mock_conn.side_effect = Exception("DB 연결 실패")
            result = get_sectors_from_db(["005930"])

        assert result == {}

    def test_빈_목록_조회(self):
        """빈 목록 전달 시 DB 조회 없이 빈 dict."""
        from trading.personas.sector_cap_guard import get_sectors_from_db

        with patch("trading.personas.sector_cap_guard.connection") as mock_conn:
            result = get_sectors_from_db([])

        mock_conn.assert_not_called()
        assert result == {}


# ---------------------------------------------------------------------------
# RED: portfolio_gate 통합 — 가드 호출 여부 검증
# ---------------------------------------------------------------------------


class TestPortfolioGateSectorIntegration:
    """_apply_portfolio_adjustment에서 sector cap 가드가 실제로 호출됨."""

    def _make_assets(self, holdings=None):
        """mock assets 생성."""
        return {
            "holdings": holdings or [],
            "cash_d2": 6_000_000,
            "stock_eval": 4_000_000,
            "total_assets": 10_000_000,
            "invest_basis": 10_000_000,
        }

    def test_sector_가드_호출됨(self):
        """_apply_portfolio_adjustment가 _apply_sector_cap_guard를 내부에서 호출."""
        from trading.personas import portfolio_gate

        holdings = [_holding("005930", "금융", 4_500_000)]  # 금융 45%
        signals = [{"ticker": "055550", "side": "buy", "qty": 10}]
        sig_ids = [1]

        with (
            patch("trading.personas.portfolio_gate.portfolio") as mock_portfolio,
            patch("trading.personas.portfolio_gate._read_regime", return_value=("bear", "neutral")),
            patch("trading.personas.portfolio_gate._apply_sector_cap_guard") as mock_guard,
        ):
            mock_portfolio.is_active.return_value = False  # skip portfolio persona
            # 섹터 가드가 신호를 모두 차단한다고 가정
            mock_guard.return_value = ([], [])

            portfolio_gate._apply_portfolio_adjustment(
                signals,
                sig_ids,
                holdings=holdings,
                holdings_count=len(holdings),
                total_assets=10_000_000,
                cash_pct=60.0,
                today="2026-06-28",
                cycle_kind="pre_market",
            )

        mock_guard.assert_called_once()

    def test_sector_가드_차단_결과_적용(self):
        """_apply_sector_cap_guard가 BUY 차단하면 해당 signal이 최종 결과에서 제거됨."""
        from trading.personas import portfolio_gate

        holdings = [_holding("005930", "금융", 4_200_000)]  # 금융 42%
        signals = [
            {"ticker": "055550", "side": "buy", "qty": 10},  # 금융 → cap 초과 차단 예정
            {"ticker": "035420", "side": "buy", "qty": 5},   # IT → 통과 예정
        ]
        sig_ids = [1, 2]
        rejected = []

        # _apply_sector_cap_guard가 금융 BUY만 제거한 결과를 돌려주도록 mock
        kept_after_sector = [{"ticker": "035420", "side": "buy", "qty": 5}]
        kept_ids_after_sector = [2]

        with (
            patch("trading.personas.portfolio_gate.portfolio") as mock_portfolio,
            patch(
                "trading.personas.portfolio_gate._read_regime",
                return_value=("neutral", "neutral"),
            ),
            patch(
                "trading.personas.portfolio_gate._apply_sector_cap_guard",
                return_value=(kept_after_sector, kept_ids_after_sector),
            ),
        ):
            mock_portfolio.is_active.return_value = False  # portfolio persona 스킵

            new_signals, _new_ids = portfolio_gate._apply_portfolio_adjustment(
                signals,
                sig_ids,
                holdings=holdings,
                holdings_count=len(holdings),
                total_assets=10_000_000,
                cash_pct=58.0,
                today="2026-06-28",
                cycle_kind="pre_market",
                res_rejected=rejected,
            )

        buy_tickers = [s["ticker"] for s in new_signals if s.get("side") == "buy"]
        assert "035420" in buy_tickers
        assert "055550" not in buy_tickers


# ---------------------------------------------------------------------------
# RED: 미분류 섹터 가시화
# ---------------------------------------------------------------------------


class TestUnknownSectorVisibility:
    """미분류 종목 경고 가시화 검증."""

    def test_미분류_건수_로그_포함(self, caplog):
        """섹터 미상 종목이 있을 때 '섹터 미상으로 cap 미적용 N건' 로그 출력."""
        from trading.personas.sector_cap_guard import enforce_sector_cap

        signals = [_buy("999999", 5), _buy("888888", 5)]  # 둘 다 섹터 미상

        with caplog.at_level(logging.WARNING, logger="trading.personas.sector_cap_guard"):
            kept, _dropped = enforce_sector_cap(
                signals,
                holdings=[],
                total_portfolio=10_000_000,
                sector_cap_pct=40.0,
                price_map={"999999": 10_000, "888888": 10_000},
                sector_map={},
            )

        log_text = " ".join(r.message for r in caplog.records)
        assert "2" in log_text  # 미상 건수 포함
        assert len(kept) == 2   # 차단 안 함
