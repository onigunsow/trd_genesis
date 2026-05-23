"""KIS market data — current price, daily candles."""

from __future__ import annotations

from typing import Any

from trading.kis.client import KisClient, KisError


def current_price(client: KisClient, ticker: str) -> dict[str, Any]:
    """Fetch current price for a domestic stock.

    KIS endpoint: GET /uapi/domestic-stock/v1/quotations/inquire-price
    tr_id: FHKST01010100 (paper/live identical for quotation endpoints).
    """
    resp = client.get(
        "/uapi/domestic-stock/v1/quotations/inquire-price",
        tr_id="FHKST01010100",
        params={"FID_COND_MRKT_DIV_CODE": "J", "FID_INPUT_ISCD": ticker},
    )
    if resp.rt_cd != "0":
        raise KisError(resp)
    output = resp.output if isinstance(resp.output, dict) else (resp.output[0] if resp.output else {})

    # KIS iscd_stat_cls_code (종목 상태 분류)
    # 00=정상 / 51=관리 / 52=투자위험 / 53=투자경고 / 54=거래정지 / 55=단기과열
    stat_cls = output.get("iscd_stat_cls_code", "00")
    upper_limit = int(output.get("stck_mxpr", "0") or 0)   # 상한가
    lower_limit = int(output.get("stck_llam", "0") or 0)   # 하한가
    price = int(output.get("stck_prpr", "0") or 0)

    # 상하한가 도달/근접 비율 (한국 KOSPI 30% 변동폭 기준)
    near_upper = upper_limit > 0 and price >= upper_limit * 0.99
    near_lower = lower_limit > 0 and price <= lower_limit * 1.01

    return {
        "ticker": ticker,
        "price": price,
        "open": int(output.get("stck_oprc", "0") or 0),
        "high": int(output.get("stck_hgpr", "0") or 0),
        "low": int(output.get("stck_lwpr", "0") or 0),
        "prev_close": int(output.get("stck_sdpr", "0") or 0),
        "volume": int(output.get("acml_vol", "0") or 0),
        "change_pct": float(output.get("prdy_ctrt", "0") or 0),
        # M5 정밀화 — REQ-KIS-02-12 매매 사전 차단용
        "stat_cls": stat_cls,                       # 00 외엔 모두 위험
        "upper_limit": upper_limit,
        "lower_limit": lower_limit,
        "near_upper_limit": near_upper,
        "near_lower_limit": near_lower,
        "is_normal": stat_cls == "00",
        "raw": output,
    }


# 종목 상태 코드 의미 (REQ-KIS-02-12)
STAT_CLS_LABELS = {
    "00": "정상",
    "51": "관리종목",
    "52": "투자위험",
    "53": "투자경고",
    "54": "거래정지",
    "55": "단기과열",
}


def stat_cls_label(code: str) -> str:
    return STAT_CLS_LABELS.get(code, f"알수없음({code})")


# SPEC-TRADING-026: stat_cls risk tiers.
# 단기과열(55) is tradeable (via single-price auction) and is treated as a
# soft / cautioned state — de-weighted at the screener, size-reduced and
# limit-only at execution — rather than a hard block. The genuine danger
# states (관리 51 / 투자위험 52 / 투자경고 53 / 거래정지 54) and any unknown
# non-normal code remain a hard block.
OVERHEAT_STAT_CLS = "55"  # 단기과열


def is_overheated(stat_cls: str) -> bool:
    """True for 단기과열(55) — tradeable but cautioned (single-price auction)."""
    return stat_cls == OVERHEAT_STAT_CLS


def is_hard_block(stat_cls: str) -> bool:
    """True when the stat_cls must hard-block trading.

    Conservative default: anything that is neither normal(00) nor
    overheated(55) hard-blocks, including unknown / missing codes.
    """
    return stat_cls not in ("00", OVERHEAT_STAT_CLS)
