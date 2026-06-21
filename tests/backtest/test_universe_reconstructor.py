"""SPEC-TRADING-057 M1 — as-of-date 유니버스 재구성기 단위 테스트.

REQ-057-M1-6  : 생존편향 PRECONDITION GATE 검증
REQ-057-M1-6a : as-of-date 멤버십 재구성 (상폐 포함)
REQ-057-M1-6b : 재구성 불가 시 achievable=False 다운그레이드 플래그

설계 원칙:
- 모든 테스트는 픽스처 주입(provider callable)으로 실행 — 네트워크/pykrx 불필요.
- pykrx는 테스트 컬렉션 시점에 import되지 않는다 (KRX 로그인 사이드이펙트 방지).
"""
from __future__ import annotations

from datetime import date

import pytest


# ── 픽스처 헬퍼 ────────────────────────────────────────────────────────────

_DELISTED_2018 = "000030"  # 2018년 당시 상장, 현재 상폐
_ACTIVE_2018   = "005380"  # 2018년 당시 상장, 현재도 상장

# 2018-01-02 기준 as-of-date 픽스처 멤버십 (상폐 종목 포함)
_FIXTURE_MEMBERS_2018 = [_DELISTED_2018, _ACTIVE_2018, "000660", "035420"]

# 오늘 기준 생존 종목만 (상폐 제외)
_FIXTURE_MEMBERS_TODAY = [_ACTIVE_2018, "000660", "035420"]


def _membership_provider_ok(rebalance_date: date) -> list[str]:
    """2018-01-02 → as-of-date(상폐 포함), 그 외 → 오늘 멤버."""
    if rebalance_date == date(2018, 1, 2):
        return list(_FIXTURE_MEMBERS_2018)
    return list(_FIXTURE_MEMBERS_TODAY)


def _membership_provider_fail(rebalance_date: date) -> list[str]:
    """항상 KRX 네트워크 오류를 시뮬레이션."""
    raise ConnectionError("KRX 세션 없음 (픽스처 시뮬레이션)")


def _membership_provider_empty(rebalance_date: date) -> list[str]:
    """빈 멤버십 반환 — as-of-date 지원 불가 케이스."""
    return []


# ── TC-1: 정상 재구성 — 상폐 종목 포함 반환 ──────────────────────────────

class TestReconstructNormal:
    """REQ-057-M1-6a: as-of-date 재구성 정상 경로."""

    def test_returns_injected_tickers_including_delisted(self):
        """provider가 반환한 종목 목록(상폐 포함)을 그대로 돌려준다."""
        from trading.backtest.universe_reconstructor import reconstruct_universe

        result = reconstruct_universe(
            date(2018, 1, 2),
            membership_provider=_membership_provider_ok,
        )

        assert _DELISTED_2018 in result.tickers, "상폐 종목 000030이 포함돼야 한다"
        assert _ACTIVE_2018 in result.tickers
        assert len(result.tickers) == len(_FIXTURE_MEMBERS_2018)

    def test_achievable_true_when_provider_works(self):
        """provider 성공 시 achievable=True."""
        from trading.backtest.universe_reconstructor import reconstruct_universe

        result = reconstruct_universe(
            date(2018, 1, 2),
            membership_provider=_membership_provider_ok,
        )

        assert result.achievable is True

    def test_rebalance_date_preserved(self):
        """결과에 요청한 rebalance_date가 보존된다."""
        from trading.backtest.universe_reconstructor import reconstruct_universe

        d = date(2018, 1, 2)
        result = reconstruct_universe(d, membership_provider=_membership_provider_ok)

        assert result.rebalance_date == d

    def test_tickers_sorted_for_determinism(self):
        """동일 입력 → 바이트 동일 출력: 종목 목록이 정렬되어 있어야 한다."""
        from trading.backtest.universe_reconstructor import reconstruct_universe

        r1 = reconstruct_universe(
            date(2018, 1, 2),
            membership_provider=_membership_provider_ok,
        )
        r2 = reconstruct_universe(
            date(2018, 1, 2),
            membership_provider=_membership_provider_ok,
        )

        assert r1.tickers == r2.tickers, "동일 입력은 동일 출력이어야 한다"
        assert r1.tickers == sorted(r1.tickers), "종목 코드가 정렬되어야 한다"

    def test_as_of_date_differs_from_today(self):
        """2018-01-02 재구성 결과가 오늘 결과와 다르다 (상폐 종목 포함 증거)."""
        from trading.backtest.universe_reconstructor import reconstruct_universe

        r_2018 = reconstruct_universe(
            date(2018, 1, 2),
            membership_provider=_membership_provider_ok,
        )
        r_today = reconstruct_universe(
            date.today(),
            membership_provider=_membership_provider_ok,
        )

        assert set(r_2018.tickers) != set(r_today.tickers), (
            "2018 as-of-date 유니버스는 오늘과 달라야 한다 (상폐 종목 차이)"
        )


# ── TC-2: M1-6b 다운그레이드 — provider 실패 ─────────────────────────────

class TestReconstructDowngrade:
    """REQ-057-M1-6b: provider 실패 → achievable=False 다운그레이드."""

    def test_achievable_false_when_provider_raises(self):
        """provider가 예외를 던지면 achievable=False."""
        from trading.backtest.universe_reconstructor import reconstruct_universe

        result = reconstruct_universe(
            date(2018, 1, 2),
            membership_provider=_membership_provider_fail,
        )

        assert result.achievable is False

    def test_tickers_empty_on_provider_failure(self):
        """provider 실패 시 tickers는 빈 리스트."""
        from trading.backtest.universe_reconstructor import reconstruct_universe

        result = reconstruct_universe(
            date(2018, 1, 2),
            membership_provider=_membership_provider_fail,
        )

        assert result.tickers == []

    def test_achievable_false_when_provider_returns_empty(self):
        """provider가 빈 목록을 반환하면 achievable=False (as-of-date 지원 불가 판단)."""
        from trading.backtest.universe_reconstructor import reconstruct_universe

        result = reconstruct_universe(
            date(2018, 1, 2),
            membership_provider=_membership_provider_empty,
        )

        assert result.achievable is False

    def test_downgrade_error_message_present(self):
        """achievable=False 시 downgrade_reason이 채워진다."""
        from trading.backtest.universe_reconstructor import reconstruct_universe

        result = reconstruct_universe(
            date(2018, 1, 2),
            membership_provider=_membership_provider_fail,
        )

        assert result.downgrade_reason, "다운그레이드 사유 메시지가 있어야 한다"

    def test_downgrade_flag_is_boolean(self):
        """achievable 필드는 bool이어야 한다 (int/None 아님)."""
        from trading.backtest.universe_reconstructor import reconstruct_universe

        result = reconstruct_universe(
            date(2018, 1, 2),
            membership_provider=_membership_provider_fail,
        )

        assert isinstance(result.achievable, bool)


# ── TC-3: 결정성 ─────────────────────────────────────────────────────────

class TestDeterminism:
    """REQ-057-M1-2: 동일 입력 → 바이트 동일 출력."""

    def test_repeated_calls_identical_tickers(self):
        """같은 날짜, 같은 provider → 매번 동일한 종목 목록."""
        from trading.backtest.universe_reconstructor import reconstruct_universe

        results = [
            reconstruct_universe(
                date(2020, 6, 1),
                membership_provider=_membership_provider_ok,
            )
            for _ in range(3)
        ]

        for r in results[1:]:
            assert r.tickers == results[0].tickers


# ── TC-4: pykrx import 격리 확인 ─────────────────────────────────────────

class TestPykrxIsolation:
    """단위 테스트 컨텍스트에서 pykrx가 import되지 않는지 간접 확인.

    provider를 주입하면 모듈 내부 default provider (pykrx lazy import)를
    호출하지 않으므로, 이 테스트 클래스 전체가 네트워크 없이 통과해야 한다.
    """

    def test_module_importable_without_pykrx_network(self):
        """universe_reconstructor는 pykrx 네트워크 없이 import 가능해야 한다."""
        # 이미 위 테스트들이 import에 성공했으면 이 테스트도 통과한다.
        import trading.backtest.universe_reconstructor  # noqa: F401

    def test_injected_provider_does_not_touch_pykrx(self):
        """픽스처 provider 주입 시 실제 pykrx.stock 호출이 없어야 한다."""
        from trading.backtest.universe_reconstructor import reconstruct_universe

        call_log: list[str] = []

        def spy_provider(d: date) -> list[str]:
            call_log.append(f"called:{d.isoformat()}")
            return ["005380", "000660"]

        result = reconstruct_universe(
            date(2021, 3, 15),
            membership_provider=spy_provider,
        )

        assert len(call_log) == 1, "spy provider가 정확히 1회 호출돼야 한다"
        assert result.achievable is True
