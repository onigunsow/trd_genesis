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
    stat_cls = output.get("iscd_stat_cls_code", PLAIN_STAT_CLS)
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
        "stat_cls": stat_cls,                       # NORMAL_STAT_CLS 외엔 주의
        "upper_limit": upper_limit,
        "lower_limit": lower_limit,
        "near_upper_limit": near_upper,
        "near_lower_limit": near_lower,
        # 2026-08-27: 종전엔 00 만 정상이었는데 실측상 사실상 모든 종목이
        # 55(신용가능) 라 매매가 전부 차단될 수 있었다. NORMAL_STAT_CLS 기준.
        "is_normal": stat_cls in NORMAL_STAT_CLS,
        "raw": output,
    }


# 종목 상태 코드 의미 (REQ-KIS-02-12)
#
# 2026-08-27 정정: 종전 표가 틀렸다. 54 를 거래정지로, 55 를 단기과열로 적어놨는데
# KIS iscd_stat_cls_code 공식 표는 54=투자주의 · 55=신용가능 · 58=거래정지 · 59=단기과열이다.
# 실측이 먼저 이상을 드러냈다 — 삼성전자·현대모비스·하나금융 등 17/17 종목이 전부 55 였다.
# 단기과열은 KRX 가 소수에만 붙이는 희소 지정이라 표본 100퍼센트가 될 수 없다.
# 55 는 신용거래 가능이라는 뜻이고, 유동성 있는 상장주라면 대부분 해당한다.
STAT_CLS_LABELS = {
    "00": "정상",
    "51": "관리종목",
    "52": "투자위험",
    "53": "투자경고",
    "54": "투자주의",
    "55": "신용가능",
    "57": "증거금100%",
    "58": "거래정지",
    "59": "단기과열",
}


def stat_cls_label(code: str) -> str:
    return STAT_CLS_LABELS.get(code, f"알수없음({code})")


# SPEC-TRADING-026: stat_cls risk tiers.
# 단기과열(59) is tradeable (via single-price auction) and is treated as a
# soft / cautioned state — de-weighted at the screener, size-reduced and
# limit-only at execution — rather than a hard block. The genuine danger
# states and any unknown non-normal code remain a hard block.
#
# NORMAL 에 55 를 넣는 것이 이 정정의 핵심이다. 실측상 사실상 모든 종목이 55 이므로
# 55 를 빼면 허용 목록이 비어 전 종목 매매가 정지된다. 57(증거금100%)은 종전에도
# 차단이었고 이번 변경으로 넓히지 않는다 — 라벨 정정과 위험 자세 변경을 한 커밋에
# 섞지 않는다.
PLAIN_STAT_CLS = "00"  # 그외(일반)
CREDIT_OK_STAT_CLS = "55"  # 신용가능
NORMAL_STAT_CLS = frozenset({PLAIN_STAT_CLS, CREDIT_OK_STAT_CLS})
OVERHEAT_STAT_CLS = "59"  # 단기과열


def is_overheated(stat_cls: str) -> bool:
    """True for 단기과열(59) — tradeable but cautioned (single-price auction)."""
    return stat_cls == OVERHEAT_STAT_CLS


def is_hard_block(stat_cls: str) -> bool:
    """True when the stat_cls must hard-block trading.

    Conservative default: anything that is neither normal(00/55) nor
    overheated(59) hard-blocks, including unknown / missing codes.
    """
    return stat_cls not in NORMAL_STAT_CLS and stat_cls != OVERHEAT_STAT_CLS
