"""SPEC-TRADING-034 — shared portfolio-adjustment gate.

Inserts the (previously dormant) portfolio persona between decision and
risk/execute in all three trading cycles. Applies the persona's binding,
buy-only sizing adjustments (``qty_adjusted``) and drops rejected buys, then
hands the adjusted signal/sig_id lists back to the cycle's execute loop.

Design invariants:
- Buy-only: sell (and hold) signals are never reduced or dropped (SPEC-033).
- Binding: ``qty_adjusted`` overwrites the buy ``qty``; ``qty_adjusted <= 0``
  (or a ``rejected`` ticker) drops the buy and its sig_id.
- Skip: when ``holdings_count`` < 5 (portfolio.is_active False) the persona is
  not called at all (Sonnet cost 0) and the input is returned unchanged.
- Fail-safe: any persona failure (exception, ``response_json is None``, missing
  required keys) falls back to the UNADJUSTED input so the cycle continues;
  the operator is notified via Telegram (notify failures are swallowed).
- Transparency: a non-trivial adjustment/rejection emits one Telegram briefing
  and one audit_log entry.

The pure apply-mapping logic (``_apply_mapping``) is isolated for unit testing.

@MX:SPEC: SPEC-TRADING-034
"""

from __future__ import annotations

import logging
from typing import Any

from trading.alerts import telegram as tg
from trading.db.session import audit
from trading.personas import portfolio

LOG = logging.getLogger(__name__)

_REQUIRED_KEYS = ("adjusted_signals", "rejected")


def _apply_mapping(
    buys: list[tuple[dict[str, Any], int]],
    *,
    adjusted: dict[str, dict[str, Any]],
    rejected: set[str],
) -> tuple[list[tuple[dict[str, Any], int]], list[int]]:
    """Pure apply-mapping over buy (signal, sig_id) pairs.

    Returns ``(kept_buys, dropped_sig_ids)`` where ``kept_buys`` preserves input
    order and ``dropped_sig_ids`` collects the sig_ids removed by a rejection or
    a ``qty_adjusted <= 0``. Tickers absent from both ``adjusted`` and
    ``rejected`` are left unchanged (REQ-034-2a/7 boundary).
    """
    kept: list[tuple[dict[str, Any], int]] = []
    dropped: list[int] = []
    for sig, sid in buys:
        ticker = sig.get("ticker")
        # Rejection takes priority over any adjustment (REQ-034-3).
        if ticker in rejected:
            dropped.append(sid)
            continue
        if ticker in adjusted:
            entry = adjusted[ticker]
            if "qty_adjusted" in entry and entry["qty_adjusted"] is not None:
                try:
                    qty = int(entry["qty_adjusted"])
                except (TypeError, ValueError):
                    # Defensive (A-4): non-numeric qty_adjusted -> no-op.
                    kept.append((sig, sid))
                    continue
                if qty <= 0:
                    # REQ-034-2: qty_adjusted == 0 (or negative) drops the buy.
                    dropped.append(sid)
                    continue
                sig["qty"] = qty
        kept.append((sig, sid))
    return kept, dropped


def _has_required_keys(pj: dict[str, Any] | None) -> bool:
    return isinstance(pj, dict) and any(k in pj for k in _REQUIRED_KEYS)


# @MX:ANCHOR: SPEC-TRADING-034 — fan_in 3 (pre_market/intraday/event cycles all
# call this gate between decision and risk/execute). Buy-only + sig_id alignment
# are the load-bearing invariants; changing the return contract or dropping the
# fail-safe would regress all three cycles.
# @MX:REASON: This is the single insertion point for portfolio sizing discipline
# in live trading; a fault here either blocks exits (if it touched sells) or
# halts the cycle (if it raised) — both forbidden by SPEC-034.
def _apply_portfolio_adjustment(
    signals: list[dict[str, Any]],
    sig_ids: list[int],
    *,
    holdings: list[dict[str, Any]],
    holdings_count: int,
    total_assets: int,
    cash_pct: float,
    today: str,
    cycle_kind: str,
    res_rejected: list[int] | None = None,
) -> tuple[list[dict[str, Any]], list[int]]:
    """Apply binding, buy-only portfolio adjustments to ``signals``/``sig_ids``.

    Args:
        signals: Decision signals (mixed buy/sell/hold), position-aligned with
            ``sig_ids``.
        sig_ids: persona_decisions row ids, one per signal (decision.run order).
        holdings/holdings_count/total_assets/cash_pct: portfolio context derived
            from the in-scope ``assets`` (= balance()).
        today/cycle_kind: prompt + audit context.
        res_rejected: when provided, dropped buy sig_ids are appended here so the
            caller's ``CycleResult.rejected`` records portfolio rejections.

    Returns:
        ``(new_signals, new_sig_ids)`` — adjusted buys + untouched non-buys with
        signal<->sig_id alignment preserved. On skip/failure, the unchanged
        input lists are returned.
    """
    # REQ-034-5: holdings < 5 -> skip entirely (no persona call, cost 0).
    if not portfolio.is_active(holdings_count):
        return signals, sig_ids

    # Split buy vs non-buy; non-buy preserved untouched (REQ-034-4).
    pairs = list(zip(signals, sig_ids, strict=False))
    buys = [(s, sid) for s, sid in pairs if s.get("side") == "buy"]
    others = [(s, sid) for s, sid in pairs if s.get("side") != "buy"]

    # No buys to adjust -> pass through unchanged.
    if not buys:
        return signals, sig_ids

    # REQ-034-1/9: run the portfolio persona on buy signals only.
    try:
        pres = portfolio.run(
            {
                "today": today,
                "decision_signals": [b[0] for b in buys],
                "holdings": holdings,
                "holdings_count": holdings_count,
                "total_assets": total_assets,
                "cash_pct": cash_pct,
            },
            cycle_kind,
        )
        pj = pres.response_json
    except Exception as exc:
        # REQ-034-6: fail-safe — never block the cycle. Absorbs the
        # ANTHROPIC_API_KEY RuntimeError and any CLI/JSON failure.
        LOG.warning("portfolio gate failed (%s) — falling back to unadjusted", exc)
        _notify_failure(cycle_kind, exc)
        return signals, sig_ids

    if not _has_required_keys(pj):
        LOG.warning(
            "portfolio gate: missing/invalid response_json — falling back to unadjusted"
        )
        _notify_failure(cycle_kind, None)
        return signals, sig_ids

    adjusted = {
        a["ticker"]: a
        for a in (pj.get("adjusted_signals") or [])
        if isinstance(a, dict) and "ticker" in a
    }
    rejected = {
        r["ticker"]
        for r in (pj.get("rejected") or [])
        if isinstance(r, dict) and "ticker" in r
    }

    # Capture original quantities BEFORE _apply_mapping mutates the buy dicts
    # in place (it sets s["qty"] = qty_adjusted), so the transparency report can
    # tell a real reduction from a no-op.
    orig_qty = {sid: s.get("qty") for s, sid in buys}

    kept_buys, dropped_sids = _apply_mapping(buys, adjusted=adjusted, rejected=rejected)

    # Reassemble: kept buys first (input order), then untouched non-buys. Each
    # signal stays paired with its original sig_id (REQ-034-4a alignment).
    new_pairs = kept_buys + others
    new_signals = [p[0] for p in new_pairs]
    new_sig_ids = [p[1] for p in new_pairs]

    # Record dropped buys in the cycle result (REQ-034-3).
    if res_rejected is not None:
        res_rejected.extend(dropped_sids)

    # Build transparency payloads only for tickers that actually changed.
    adjusted_report = [
        {
            "ticker": s.get("ticker"),
            "qty_original": orig_qty.get(sid),
            "qty_adjusted": s.get("qty"),
        }
        for s, sid in kept_buys
        if s.get("ticker") in adjusted and s.get("qty") != orig_qty.get(sid)
    ]
    dropped_set = set(dropped_sids)
    rejected_report = [
        {"ticker": s.get("ticker")}
        for s, sid in buys
        if sid in dropped_set
    ]

    # REQ-034-7: non-trivial adjustment/rejection -> telegram + audit.
    if adjusted_report or rejected_report:
        _emit_transparency(cycle_kind, adjusted_report, rejected_report)

    return new_signals, new_sig_ids


def _notify_failure(cycle_kind: str, exc: BaseException | None) -> None:
    """REQ-034-6: notify the operator of a portfolio failure; swallow notify
    errors so the gate never raises."""
    try:
        err = exc if exc is not None else RuntimeError("invalid portfolio response")
        tg.system_error(
            "Portfolio persona", err, context=f"cycle={cycle_kind} (unadjusted fallback)"
        )
    except Exception:
        LOG.warning("portfolio gate: telegram notify failed (swallowed)", exc_info=True)


def _emit_transparency(
    cycle_kind: str,
    adjusted_report: list[dict[str, Any]],
    rejected_report: list[dict[str, Any]],
) -> None:
    """REQ-034-7: one telegram briefing + one audit entry per non-trivial run."""
    lines: list[str] = []
    for a in adjusted_report:
        lines.append(
            f"{a['ticker']}: {a['qty_original']}주 → {a['qty_adjusted']}주"
        )
    for r in rejected_report:
        lines.append(f"{r['ticker']}: 거부(드롭)")
    message = f"[{cycle_kind}] " + "; ".join(lines)
    try:
        tg.system_briefing("포트폴리오 조정", message)
    except Exception:
        LOG.warning("portfolio gate: briefing send failed (swallowed)", exc_info=True)
    audit(
        "PORTFOLIO_ADJUSTMENT",
        actor="portfolio_gate",
        details={
            "cycle": cycle_kind,
            "adjusted": adjusted_report,
            "rejected": rejected_report,
        },
    )
