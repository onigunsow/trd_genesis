"""SPEC-TRADING-046 TDD: strategy/sizing/vol_target.py 단위 테스트.

M1 — SizingParams 단일원천 + vol-targeting 수식 검증
M2 — confidence non-increasing 가드 [HARD]

테스트는 구현보다 먼저 작성됩니다 (RED 단계).
"""

from __future__ import annotations

import pytest


# ---------------------------------------------------------------------------
# Helpers: 사이징 모듈이 없어도 테스트 구조 확인을 위한 임포트 지점
# ---------------------------------------------------------------------------

def _import_sizing():
    from trading.strategy.sizing.vol_target import compute_qty, SizingParams
    return compute_qty, SizingParams


# ---------------------------------------------------------------------------
# M1-A: SizingParams 구조 — config 단일원천 (REQ-046-C)
# ---------------------------------------------------------------------------

class TestSizingParams:
    """SizingParams 는 config 상수에서 기본값을 가져와야 한다."""

    def test_sizing_params_importable(self):
        """SizingParams 를 strategy.sizing.vol_target 에서 임포트 가능해야 한다."""
        _, SizingParams = _import_sizing()
        assert SizingParams is not None

    def test_sizing_params_defaults(self):
        """기본 SizingParams 는 운영자-결정 기본값을 갖는다."""
        _, SizingParams = _import_sizing()
        p = SizingParams()
        # vol_target_per_trade: 1-ATR 역풍이 자산의 1% 를 초과하지 않도록
        assert 0 < p.vol_target_per_trade <= 0.05
        # ATR lookback: 기존 14일 재사용 (plan.md)
        assert p.atr_lookback == 14
        # fallback 분율: 단건 상한(10%)보다 보수적
        assert 0 < p.fallback_fraction < 0.10
        # confidence damp: 기본 OFF [HARD] REQ-046-B2
        assert p.confidence_damp_enabled is False

    def test_sizing_params_fields_exist(self):
        """필수 필드가 SizingParams 에 존재해야 한다."""
        _, SizingParams = _import_sizing()
        p = SizingParams()
        assert hasattr(p, "vol_target_per_trade")
        assert hasattr(p, "atr_lookback")
        assert hasattr(p, "fallback_fraction")
        assert hasattr(p, "confidence_damp_enabled")

    def test_sizing_params_is_dataclass_or_typed(self):
        """SizingParams 는 타입이 명시된 구조체여야 한다 (dataclass 또는 pydantic)."""
        import dataclasses
        _, SizingParams = _import_sizing()
        # dataclass 이거나 pydantic BaseModel 이어야 한다
        is_dc = dataclasses.is_dataclass(SizingParams)
        try:
            from pydantic import BaseModel
            is_pydantic = issubclass(SizingParams, BaseModel)
        except ImportError:
            is_pydantic = False
        assert is_dc or is_pydantic, "SizingParams 는 dataclass 또는 pydantic 모델이어야 한다"


# ---------------------------------------------------------------------------
# M1-B: vol-targeting 수식 검증 (REQ-046-A1)
# AC-1: 저변동 종목 > 고변동 종목 notional
# ---------------------------------------------------------------------------

class TestVolTargetFormula:
    """변동성 타기팅: qty 는 ATR% 에 역비례해야 한다."""

    def _make_state(
        self,
        total_assets: int = 10_000_000,
        cash: int = 10_000_000,
        atr_pct: float | None = 2.0,
        ref_price: int = 50_000,
        holdings: list | None = None,
    ) -> dict:
        return {
            "total_assets": total_assets,
            "cash": cash,
            "atr_pct": atr_pct,
            "ref_price": ref_price,
            "holdings": holdings or [],
        }

    def test_low_vol_larger_than_high_vol(self):
        """AC-1: 저변동 종목이 고변동보다 크게 사이징된다."""
        compute_qty, SizingParams = _import_sizing()
        params = SizingParams()
        candidate = {"ticker": "005930", "side": "buy", "confidence": 0.7}

        state_low = self._make_state(atr_pct=1.0)   # 저변동
        state_high = self._make_state(atr_pct=4.0)  # 고변동

        result_low = compute_qty(candidate=candidate, portfolio_state=state_low, params=params)
        result_high = compute_qty(candidate=candidate, portfolio_state=state_high, params=params)

        qty_low = result_low["qty"]
        qty_high = result_high["qty"]

        # 저변동 종목은 더 많은 주를 살 수 있어야 한다
        assert qty_low > qty_high, (
            f"저변동(ATR=1%) qty={qty_low} > 고변동(ATR=4%) qty={qty_high} 이어야 함"
        )

    def test_deterministic_same_result_twice(self):
        """AC-1: 동일 입력으로 두 번 호출하면 같은 결과 (결정적)."""
        compute_qty, SizingParams = _import_sizing()
        params = SizingParams()
        candidate = {"ticker": "005930", "side": "buy", "confidence": 0.7}
        state = self._make_state(atr_pct=2.0)

        r1 = compute_qty(candidate=candidate, portfolio_state=state, params=params)
        r2 = compute_qty(candidate=candidate, portfolio_state=state, params=params)

        assert r1["qty"] == r2["qty"]
        assert r1["sizing_reason"] == r2["sizing_reason"]

    def test_vol_budget_scale(self):
        """더 높은 vol_target_per_trade 로 더 큰 포지션을 갖는다."""
        compute_qty, SizingParams = _import_sizing()
        state = self._make_state(atr_pct=2.0)
        candidate = {"ticker": "005930", "side": "buy"}

        p_small = SizingParams(vol_target_per_trade=0.005)
        p_large = SizingParams(vol_target_per_trade=0.02)

        r_small = compute_qty(candidate=candidate, portfolio_state=state, params=p_small)
        r_large = compute_qty(candidate=candidate, portfolio_state=state, params=p_large)

        assert r_large["qty"] >= r_small["qty"], (
            "vol_target 가 클수록 제안 qty 가 같거나 커야 한다"
        )

    def test_sizing_reason_normal(self):
        """정상 ATR 이 있을 때 sizing_reason = 'vol_target' 이어야 한다."""
        compute_qty, SizingParams = _import_sizing()
        params = SizingParams()
        candidate = {"ticker": "005930", "side": "buy"}
        state = self._make_state(atr_pct=2.0)

        result = compute_qty(candidate=candidate, portfolio_state=state, params=params)
        assert result["sizing_reason"] == "vol_target"

    def test_notional_scales_with_equity(self):
        """자산이 2배 이면 notional 도 (대략) 2배가 되어야 한다."""
        compute_qty, SizingParams = _import_sizing()
        params = SizingParams()
        candidate = {"ticker": "005930", "side": "buy"}

        state_1x = self._make_state(total_assets=10_000_000, cash=10_000_000, atr_pct=2.0)
        state_2x = self._make_state(total_assets=20_000_000, cash=20_000_000, atr_pct=2.0)

        r1 = compute_qty(candidate=candidate, portfolio_state=state_1x, params=params)
        r2 = compute_qty(candidate=candidate, portfolio_state=state_2x, params=params)

        # notional = qty * ref_price 가 거의 2배여야 한다 (±10% 허용)
        notional_1 = r1["qty"] * state_1x["ref_price"]
        notional_2 = r2["qty"] * state_2x["ref_price"]
        if notional_1 > 0:
            ratio = notional_2 / notional_1
            assert 1.5 <= ratio <= 2.5, f"2x 자산에서 notional 비율 {ratio:.2f} 이 [1.5, 2.5] 밖"


# ---------------------------------------------------------------------------
# M1-C: fallback — ATR 부재 (REQ-046-A3)
# ---------------------------------------------------------------------------

class TestVolUnavailableFallback:
    """ATR 이 없을 때 보수 고정 분율 fallback (REQ-046-A3)."""

    def test_vol_unavailable_fallback_reason(self):
        """atr_pct=None 이면 sizing_reason='vol_unavailable'."""
        compute_qty, SizingParams = _import_sizing()
        params = SizingParams()
        candidate = {"ticker": "005930", "side": "buy"}
        state = {
            "total_assets": 10_000_000,
            "cash": 10_000_000,
            "atr_pct": None,
            "ref_price": 50_000,
            "holdings": [],
        }
        result = compute_qty(candidate=candidate, portfolio_state=state, params=params)
        assert result["sizing_reason"] == "vol_unavailable"

    def test_vol_unavailable_fallback_qty_positive(self):
        """ATR 없어도 fallback qty > 0 (cash 충분할 때)."""
        compute_qty, SizingParams = _import_sizing()
        params = SizingParams()
        candidate = {"ticker": "005930", "side": "buy"}
        state = {
            "total_assets": 10_000_000,
            "cash": 10_000_000,
            "atr_pct": None,
            "ref_price": 50_000,
            "holdings": [],
        }
        result = compute_qty(candidate=candidate, portfolio_state=state, params=params)
        assert result["qty"] >= 0

    def test_vol_unavailable_fallback_conservative(self):
        """ATR 없을 때 fallback notional 이 단건 상한(10%)보다 보수적이어야 한다."""
        compute_qty, SizingParams = _import_sizing()
        params = SizingParams()
        candidate = {"ticker": "005930", "side": "buy"}
        state = {
            "total_assets": 10_000_000,
            "cash": 10_000_000,
            "atr_pct": None,
            "ref_price": 50_000,
            "holdings": [],
        }
        result = compute_qty(candidate=candidate, portfolio_state=state, params=params)
        fallback_notional = result["qty"] * state["ref_price"]
        single_order_cap = state["total_assets"] * 0.10  # RISK_SINGLE_ORDER_MAX
        assert fallback_notional <= single_order_cap, (
            f"fallback notional {fallback_notional:,} 이 단건 상한 {single_order_cap:,} 을 초과"
        )


# ---------------------------------------------------------------------------
# M1-D: below_min_lot — 1주 미만 반올림 (REQ-046-A4)
# ---------------------------------------------------------------------------

class TestBelowMinLot:
    """계산 qty 가 0 주로 반올림되면 qty=0, sizing_reason='below_min_lot' (REQ-046-A4)."""

    def test_below_min_lot_returns_zero(self):
        """극소 자산 / 고가 종목 / 낮은 vol budget → qty=0."""
        compute_qty, SizingParams = _import_sizing()
        params = SizingParams(vol_target_per_trade=0.001)  # 아주 낮은 budget
        candidate = {"ticker": "000660", "side": "buy"}
        state = {
            "total_assets": 100_000,    # 소액 자산
            "cash": 100_000,
            "atr_pct": 3.0,
            "ref_price": 200_000,       # 고가 종목
            "holdings": [],
        }
        result = compute_qty(candidate=candidate, portfolio_state=state, params=params)
        assert result["qty"] == 0
        assert result["sizing_reason"] == "below_min_lot"

    def test_below_min_lot_never_forces_one_share(self):
        """qty=0 에서 1주 강제 금지 (REQ-046-A4: never force ≥1 share)."""
        compute_qty, SizingParams = _import_sizing()
        params = SizingParams(vol_target_per_trade=0.00001)  # 극소
        candidate = {"ticker": "000660", "side": "buy"}
        state = {
            "total_assets": 1_000,      # 극소
            "cash": 1_000,
            "atr_pct": 1.0,
            "ref_price": 500_000,       # 삼성바이오로직스급 고가
            "holdings": [],
        }
        result = compute_qty(candidate=candidate, portfolio_state=state, params=params)
        # qty 가 0 이어야 함 — 1주를 강제해서는 안 됨
        assert result["qty"] == 0


# ---------------------------------------------------------------------------
# M2: confidence non-increasing 가드 [HARD] (REQ-046-B1/B2/B3)
# AC-2
# ---------------------------------------------------------------------------

class TestConfidenceNonIncreasing:
    """confidence 는 qty 를 키워서는 안 된다 [HARD]."""

    def _make_state(self) -> dict:
        return {
            "total_assets": 10_000_000,
            "cash": 10_000_000,
            "atr_pct": 2.0,
            "ref_price": 50_000,
            "holdings": [],
        }

    def test_confidence_non_increasing_default_off(self):
        """AC-2 (damp OFF): confidence=0.1/0.5/0.9 모두 동일 qty."""
        compute_qty, SizingParams = _import_sizing()
        params = SizingParams(confidence_damp_enabled=False)
        state = self._make_state()

        confidences = [0.1, 0.5, 0.9]
        qtys = []
        for c in confidences:
            candidate = {"ticker": "005930", "side": "buy", "confidence": c}
            result = compute_qty(candidate=candidate, portfolio_state=state, params=params)
            qtys.append(result["qty"])

        # damp OFF 기본: 모두 동일
        assert qtys[0] == qtys[1] == qtys[2], (
            f"damp OFF 에서 confidence 에 따라 qty 가 달라짐: {qtys}"
        )

    def test_confidence_never_increases_qty(self):
        """AC-2: confidence 증가 → qty 절대 증가 안 됨 [HARD]."""
        compute_qty, SizingParams = _import_sizing()
        params = SizingParams()  # default OFF
        state = self._make_state()

        low_conf = {"ticker": "005930", "side": "buy", "confidence": 0.1}
        high_conf = {"ticker": "005930", "side": "buy", "confidence": 0.9}

        r_low = compute_qty(candidate=low_conf, portfolio_state=state, params=params)
        r_high = compute_qty(candidate=high_conf, portfolio_state=state, params=params)

        # 절대 증가 없음
        assert r_high["qty"] <= r_low["qty"], (
            f"confidence 0.9 의 qty={r_high['qty']} > confidence 0.1 의 qty={r_low['qty']}: "
            "confidence 가 qty 를 키워서는 안 됨 [HARD]"
        )

    def test_confidence_none_same_as_default(self):
        """AC-2/REQ-046-B3: confidence=None 은 기본 경로와 동일."""
        compute_qty, SizingParams = _import_sizing()
        params = SizingParams()
        state = self._make_state()

        c_none = {"ticker": "005930", "side": "buy", "confidence": None}
        c_mid = {"ticker": "005930", "side": "buy", "confidence": 0.5}
        # confidence 가 없는 경우
        c_absent = {"ticker": "005930", "side": "buy"}

        r_none = compute_qty(candidate=c_none, portfolio_state=state, params=params)
        r_mid = compute_qty(candidate=c_mid, portfolio_state=state, params=params)
        r_absent = compute_qty(candidate=c_absent, portfolio_state=state, params=params)

        # damp OFF 이면 세 가지 모두 동일
        assert r_none["qty"] == r_mid["qty"], "confidence=None 은 기본 경로와 동일해야 한다"
        assert r_none["qty"] == r_absent["qty"], "confidence 미존재도 기본 경로와 동일해야 한다"

    def test_confidence_damp_when_enabled_is_downward_only(self):
        """damp=ON 일 때 낮은 confidence 가 같거나 더 낮은 qty 를 내야 한다."""
        compute_qty, SizingParams = _import_sizing()
        params = SizingParams(confidence_damp_enabled=True)
        state = self._make_state()

        high_conf = {"ticker": "005930", "side": "buy", "confidence": 1.0}
        low_conf = {"ticker": "005930", "side": "buy", "confidence": 0.1}

        r_high = compute_qty(candidate=high_conf, portfolio_state=state, params=params)
        r_low = compute_qty(candidate=low_conf, portfolio_state=state, params=params)

        # 낮은 confidence → 같거나 더 작은 qty (하향 전용)
        assert r_low["qty"] <= r_high["qty"], (
            f"damp=ON 에서 낮은 confidence 의 qty={r_low['qty']} > "
            f"높은 confidence 의 qty={r_high['qty']}"
        )

    def test_confidence_damp_enabled_does_not_increase(self):
        """damp=ON 에서도 confidence=1.0 은 damp 없는 경우와 동일 (상한 기준점)."""
        compute_qty, SizingParams = _import_sizing()
        state = self._make_state()

        p_off = SizingParams(confidence_damp_enabled=False)
        p_on = SizingParams(confidence_damp_enabled=True)

        c_full = {"ticker": "005930", "side": "buy", "confidence": 1.0}
        r_off = compute_qty(candidate=c_full, portfolio_state=state, params=p_off)
        r_on = compute_qty(candidate=c_full, portfolio_state=state, params=p_on)

        # confidence=1.0 에서 damp=ON 이 OFF 보다 커서는 안 됨
        assert r_on["qty"] <= r_off["qty"], (
            "damp=ON + confidence=1.0 이 damp=OFF 보다 커서는 안 됨"
        )


# ---------------------------------------------------------------------------
# M1-E: 반환 구조 검증
# ---------------------------------------------------------------------------

class TestComputeQtyReturnStructure:
    """compute_qty 가 올바른 키를 반환해야 한다."""

    def test_return_has_required_keys(self):
        """qty, sizing_reason, advisory_qty 키가 있어야 한다."""
        compute_qty, SizingParams = _import_sizing()
        params = SizingParams()
        candidate = {"ticker": "005930", "side": "buy", "qty": 3, "confidence": 0.7}
        state = {
            "total_assets": 10_000_000,
            "cash": 10_000_000,
            "atr_pct": 2.0,
            "ref_price": 50_000,
            "holdings": [],
        }
        result = compute_qty(candidate=candidate, portfolio_state=state, params=params)
        assert "qty" in result
        assert "sizing_reason" in result
        assert "advisory_qty" in result  # LLM 이 낸 qty (REQ-046-E3)

    def test_advisory_qty_preserved(self):
        """advisory_qty 는 LLM 이 낸 원본 qty 를 그대로 담아야 한다."""
        compute_qty, SizingParams = _import_sizing()
        params = SizingParams()
        llm_qty = 7
        candidate = {"ticker": "005930", "side": "buy", "qty": llm_qty, "confidence": 0.7}
        state = {
            "total_assets": 10_000_000,
            "cash": 10_000_000,
            "atr_pct": 2.0,
            "ref_price": 50_000,
            "holdings": [],
        }
        result = compute_qty(candidate=candidate, portfolio_state=state, params=params)
        assert result["advisory_qty"] == llm_qty

    def test_qty_is_non_negative_integer(self):
        """qty 는 음수가 아닌 정수여야 한다."""
        compute_qty, SizingParams = _import_sizing()
        params = SizingParams()
        candidate = {"ticker": "005930", "side": "buy"}
        state = {
            "total_assets": 10_000_000,
            "cash": 10_000_000,
            "atr_pct": 2.0,
            "ref_price": 50_000,
            "holdings": [],
        }
        result = compute_qty(candidate=candidate, portfolio_state=state, params=params)
        assert isinstance(result["qty"], int)
        assert result["qty"] >= 0

    def test_sell_returns_zero(self):
        """SELL 신호는 사이징 없이 qty=0, sizing_reason='sell_bypass' 를 반환해야 한다."""
        compute_qty, SizingParams = _import_sizing()
        params = SizingParams()
        candidate = {"ticker": "005930", "side": "sell", "qty": 5}
        state = {
            "total_assets": 10_000_000,
            "cash": 0,
            "atr_pct": 2.0,
            "ref_price": 50_000,
            "holdings": [],
        }
        result = compute_qty(candidate=candidate, portfolio_state=state, params=params)
        # SELL 은 SPEC-042 clamp_sell_to_confirmed 가 처리 — sizing 은 건드리지 않음
        assert result["sizing_reason"] == "sell_bypass"

    def test_zero_cash_returns_zero(self):
        """현금 0 이면 qty=0."""
        compute_qty, SizingParams = _import_sizing()
        params = SizingParams()
        candidate = {"ticker": "005930", "side": "buy"}
        state = {
            "total_assets": 10_000_000,
            "cash": 0,
            "atr_pct": 2.0,
            "ref_price": 50_000,
            "holdings": [],
        }
        result = compute_qty(candidate=candidate, portfolio_state=state, params=params)
        assert result["qty"] == 0
