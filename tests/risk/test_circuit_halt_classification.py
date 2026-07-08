"""SPEC-TRADING-062 그룹 A — 회로차단 breach 분류 (REQ-062-A3).

`check_pre_order`가 반환하는 breach는 두 성격으로 갈린다:
- per-signal 자문 차단(그 주문만 거부하면 충분, 계좌 위험 없음): avg_down, repeat_buy,
  per_ticker, total_invested, single_order, daily_count
- 계좌 전체 위험(halt 정당): daily_loss

`requires_circuit_halt`는 breach 문자열의 ':' 앞 접두 토큰만으로 판별하는 순수 함수이며,
시장 종속 하드코딩이 없어야 한다(US 시장 재사용 대비).

@MX:SPEC: SPEC-TRADING-062
"""

from __future__ import annotations

from trading.risk.limits import ACCOUNT_HALT_BREACH_TOKENS, requires_circuit_halt


class TestAccountHaltBreachTokens:
    """계좌-halt 토큰 집합은 daily_loss만 포함해야 한다."""

    def test_daily_loss_is_account_halt_token(self):
        assert "daily_loss" in ACCOUNT_HALT_BREACH_TOKENS

    def test_advisory_tokens_are_not_account_halt_tokens(self):
        for token in ("avg_down", "repeat_buy", "per_ticker", "total_invested"):
            assert token not in ACCOUNT_HALT_BREACH_TOKENS


class TestRequiresCircuitHalt:
    """requires_circuit_halt(breaches) — prefix-token 기반 순수 판별 함수."""

    def test_empty_breaches_does_not_require_halt(self):
        assert requires_circuit_halt([]) is False

    def test_avg_down_only_does_not_require_halt(self):
        breaches = ["avg_down: 086790 단기과열·손실(-1.20%) 물타기 매수 거부"]
        assert requires_circuit_halt(breaches) is False

    def test_repeat_buy_only_does_not_require_halt(self):
        breaches = ["repeat_buy: 086790 단기과열 당일 매수 1회 초과 차단"]
        assert requires_circuit_halt(breaches) is False

    def test_per_ticker_only_does_not_require_halt(self):
        assert requires_circuit_halt(["per_ticker: 005930 예상 보유 초과"]) is False

    def test_total_invested_only_does_not_require_halt(self):
        assert requires_circuit_halt(["total_invested: 투자 후 비중 초과"]) is False

    def test_daily_loss_only_requires_halt(self):
        assert requires_circuit_halt(["daily_loss: 오늘 손익 -3.00% ≤ 한도 -2.50%"]) is True

    def test_mixed_breaches_with_daily_loss_requires_halt(self):
        breaches = [
            "avg_down: 086790 단기과열·손실(-1.20%) 물타기 매수 거부",
            "daily_loss: 오늘 손익 -3.00% ≤ 한도 -2.50%",
        ]
        assert requires_circuit_halt(breaches) is True

    def test_mixed_advisory_breaches_without_daily_loss_does_not_require_halt(self):
        breaches = [
            "avg_down: 086790 단기과열·손실(-1.20%) 물타기 매수 거부",
            "repeat_buy: 086790 단기과열 당일 매수 1회 초과 차단",
        ]
        assert requires_circuit_halt(breaches) is False
