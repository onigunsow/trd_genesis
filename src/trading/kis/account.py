"""KIS account — balance, holdings, buyable amount."""

from __future__ import annotations

from typing import Any

from trading.kis.client import KisClient, KisError


def balance(client: KisClient) -> dict[str, Any]:
    """Fetch domestic stock balance summary + per-ticker holdings.

    KIS endpoint: GET /uapi/domestic-stock/v1/trading/inquire-balance
    tr_id: VTTC8434R (paper) / TTTC8434R (live)
    """
    resp = client.get(
        "/uapi/domestic-stock/v1/trading/inquire-balance",
        tr_id=client.tr_id(paper_id="VTTC8434R", live_id="TTTC8434R"),
        params={
            "CANO": client.account_prefix,
            "ACNT_PRDT_CD": client.account_suffix,
            "AFHR_FLPR_YN": "N",
            "OFL_YN": "",
            "INQR_DVSN": "01",   # 01 = 대출일별, 02 = 종목별
            "UNPR_DVSN": "01",
            "FUND_STTL_ICLD_YN": "N",
            "FNCG_AMT_AUTO_RDPT_YN": "N",
            "PRCS_DVSN": "00",
            "CTX_AREA_FK100": "",
            "CTX_AREA_NK100": "",
        },
    )
    if resp.rt_cd != "0":
        raise KisError(resp)

    raw = resp.raw
    # KIS returns output1 (per-ticker) + output2 (account summary).
    output1 = raw.get("output1", []) or []
    output2 = raw.get("output2", []) or [{}]
    summary = output2[0] if output2 else {}

    holdings = [
        {
            "ticker": h.get("pdno", ""),
            "name": h.get("prdt_name", ""),
            "qty": int(h.get("hldg_qty", "0") or 0),
            "avg_cost": int(float(h.get("pchs_avg_pric", "0") or 0)),
            "current_price": int(h.get("prpr", "0") or 0),
            "eval_amount": int(h.get("evlu_amt", "0") or 0),
            "pnl_amount": int(h.get("evlu_pfls_amt", "0") or 0),
            "pnl_pct": float(h.get("evlu_pfls_rt", "0") or 0),
        }
        for h in output1
        if int(h.get("hldg_qty", "0") or 0) > 0
    ]

    # KIS의 nrcvb_buy_amt(미수동결금액) / cma_evlu_amt(CMA평가금액) 등도 포함.
    # 매수가능금액 정밀 산출: nxdy_excc_amt(D+2 정산예정금액) - 미체결 매수 묶임.
    cash_d2 = int(summary.get("dnca_tot_amt", "0") or 0)
    nxdy_buyable = int(summary.get("nxdy_excc_amt", "0") or 0)
    nrcvb_buy_amt = int(summary.get("nrcvb_buy_amt", "0") or 0)  # 미체결 매수금 (장중)
    # Effective buyable: D+2 정산금 - 미체결 매수 차감
    buyable_effective = max(0, nxdy_buyable - nrcvb_buy_amt)

    return {
        "cash_d2": cash_d2,                                          # 예수금 총액
        "buyable": nxdy_buyable,                                     # KIS의 명목 매수가능
        "buyable_effective": buyable_effective,                      # 미체결 매수 차감 후 실제 가용 (REQ-KIS-02-11)
        "nrcvb_buy_amt": nrcvb_buy_amt,                              # 현재 묶여있는 미체결 매수금
        "total_assets": int(summary.get("tot_evlu_amt", "0") or 0),  # 총자산평가금액
        "stock_eval": int(summary.get("scts_evlu_amt", "0") or 0),   # 주식평가금액
        "pnl_total": int(summary.get("evlu_pfls_smtl_amt", "0") or 0),
        "holdings": holdings,
        "raw": raw,
    }
