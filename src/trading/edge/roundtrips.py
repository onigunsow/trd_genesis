"""Phase 1 — orders(paper) FIFO 원가 매칭 → 라운드트립.

페이퍼 체결(orders, mode='paper', status filled/partial)을 종목별 시간순으로 정렬해
매수→매도를 **FIFO** 로 매칭한다. 매도 1건이 여러 매수 로트에 걸치면 매칭 청크마다
라운드트립을 만든다(가장 정직한 FIFO 귀속 단위). 재고를 초과하는 매도분은 조용히 버리지 않고
``unmatched_sells`` 로 기록한다.

진입 confidence / 위험 verdict 는 **매수(진입) 시점** 의사결정에서 가져온다
(orders.persona_decision_id → persona_decisions.confidence, risk_reviews.verdict).

핵심은 순수 함수 ``build_roundtrips(rows)`` 라 DB 없이 단위 테스트할 수 있다.
``load_fill_rows`` 만 DB 를 만진다.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any, Iterable

from trading.db.session import connection


@dataclass
class RoundTrip:
    """FIFO 로 매칭된 매수→매도 1쌍(청크). 가격은 주당 단가, 수수료는 청크 안분분."""

    ticker: str
    entry_date: date
    exit_date: date
    qty: int
    entry_price: float          # 매수 주당 체결가
    exit_price: float           # 매도 주당 체결가
    entry_fee: float            # 진입 수수료(매수 fee 의 청크 안분분)
    exit_fee: float             # 청산 수수료(매도 fee 의 청크 안분분)
    confidence: float | None    # 진입 의사결정 confidence (없으면 None)
    verdict: str | None         # 진입 위험 게이트 verdict APPROVE/HOLD/REJECT (없으면 None)

    @property
    def cost_basis(self) -> float:
        """진입 총원가(체결대금 + 진입 수수료) — 수익률 분모."""
        return self.entry_price * self.qty + self.entry_fee

    @property
    def proceeds(self) -> float:
        """청산 순수취액(체결대금 − 청산 수수료)."""
        return self.exit_price * self.qty - self.exit_fee

    @property
    def fees(self) -> float:
        return self.entry_fee + self.exit_fee

    @property
    def gross_pnl(self) -> float:
        """수수료 제외 손익."""
        return (self.exit_price - self.entry_price) * self.qty

    @property
    def net_pnl(self) -> float:
        """수수료 차감 후 실현 순손익."""
        return self.gross_pnl - self.fees

    @property
    def return_pct(self) -> float:
        """순손익 / 진입 총원가 (%)."""
        cb = self.cost_basis
        return (self.net_pnl / cb * 100.0) if cb else 0.0

    @property
    def holding_days(self) -> int:
        return (self.exit_date - self.entry_date).days

    @property
    def is_win(self) -> bool:
        return self.net_pnl > 0


@dataclass
class UnmatchedSell:
    """매수 재고를 초과한 매도분(조용히 버리지 않고 보고)."""

    ticker: str
    sell_date: date
    qty: int
    price: float


@dataclass
class _BuyLot:
    qty: int
    unit_price: float
    fee_per_share: float
    entry_date: date
    confidence: float | None
    verdict: str | None


@dataclass
class RoundTripResult:
    roundtrips: list[RoundTrip] = field(default_factory=list)
    unmatched_sells: list[UnmatchedSell] = field(default_factory=list)
    open_qty: dict[str, int] = field(default_factory=dict)  # 종목별 미청산 매수 잔량(정보용)


# ---------------------------------------------------------------------------
# 순수 매칭 (DB 없음 — 단위 테스트 대상)
# ---------------------------------------------------------------------------


def _row_date(row: dict[str, Any]) -> date:
    """체결 시각 → date. filled_at 우선, 없으면 ts."""
    val = row.get("filled_at") or row.get("ts")
    if isinstance(val, datetime):
        return val.date()
    if isinstance(val, date):
        return val
    # 문자열 폴백(테스트 편의).
    return date.fromisoformat(str(val)[:10])


def _sort_key(row: dict[str, Any]) -> tuple:
    val = row.get("filled_at") or row.get("ts")
    return (str(row.get("ticker", "")), str(val), int(row.get("id", 0) or 0))


def build_roundtrips(rows: Iterable[dict[str, Any]]) -> RoundTripResult:
    """체결 행들을 FIFO 매칭. 각 행 dict 필요 키:

    ``ticker, side('buy'|'sell'), fill_qty, fill_price, fee`` (+ 정렬용 ts/filled_at/id).
    매수 행은 ``confidence``/``verdict`` 도 사용(없으면 None).
    """
    rows = sorted(rows, key=_sort_key)
    lots: dict[str, deque[_BuyLot]] = {}
    result = RoundTripResult()

    for row in rows:
        ticker = str(row["ticker"])
        side = str(row["side"])
        qty = int(row.get("fill_qty") or 0)
        price = float(row.get("fill_price") or 0)
        fee = float(row.get("fee") or 0)
        if qty <= 0:
            continue

        if side == "buy":
            lots.setdefault(ticker, deque()).append(
                _BuyLot(
                    qty=qty,
                    unit_price=price,
                    fee_per_share=(fee / qty) if qty else 0.0,
                    entry_date=_row_date(row),
                    confidence=(
                        float(row["confidence"])
                        if row.get("confidence") is not None
                        else None
                    ),
                    verdict=row.get("verdict"),
                )
            )
        elif side == "sell":
            sell_date = _row_date(row)
            sell_fee_per_share = (fee / qty) if qty else 0.0
            remaining = qty
            book = lots.get(ticker)
            while remaining > 0 and book:
                lot = book[0]
                matched = min(remaining, lot.qty)
                result.roundtrips.append(
                    RoundTrip(
                        ticker=ticker,
                        entry_date=lot.entry_date,
                        exit_date=sell_date,
                        qty=matched,
                        entry_price=lot.unit_price,
                        exit_price=price,
                        entry_fee=lot.fee_per_share * matched,
                        exit_fee=sell_fee_per_share * matched,
                        confidence=lot.confidence,
                        verdict=lot.verdict,
                    )
                )
                lot.qty -= matched
                remaining -= matched
                if lot.qty == 0:
                    book.popleft()
            if remaining > 0:
                # 매수 재고 초과 매도 — 버리지 않고 기록.
                result.unmatched_sells.append(
                    UnmatchedSell(
                        ticker=ticker, sell_date=sell_date, qty=remaining, price=price
                    )
                )

    # 미청산 매수 잔량(여전히 보유 중) 집계.
    for ticker, book in lots.items():
        open_qty = sum(lot.qty for lot in book)
        if open_qty > 0:
            result.open_qty[ticker] = open_qty

    return result


# ---------------------------------------------------------------------------
# DB 로드
# ---------------------------------------------------------------------------

_FILL_SQL = """
    SELECT o.id, o.ts, o.filled_at, o.side, o.ticker,
           o.fill_qty, o.fill_price, o.fee,
           pd.confidence,
           (SELECT rr.verdict FROM risk_reviews rr
             WHERE rr.decision_id = pd.id
             ORDER BY rr.ts DESC LIMIT 1) AS verdict
      FROM orders o
      LEFT JOIN persona_decisions pd ON pd.id = o.persona_decision_id
     WHERE o.mode = 'paper'
       AND o.status IN ('filled', 'partial')
       AND o.fill_qty IS NOT NULL AND o.fill_qty > 0
       AND o.fill_price IS NOT NULL
       {since_clause}
     ORDER BY o.ticker, COALESCE(o.filled_at, o.ts), o.id
"""


def load_fill_rows(days: int | None = None) -> list[dict[str, Any]]:
    """페이퍼 체결 행을 DB 에서 로드(confidence/verdict 조인 포함)."""
    since_clause = ""
    params: list[Any] = []
    if days is not None:
        since_clause = "AND o.ts >= NOW() - (%s || ' days')::INTERVAL"
        params.append(str(int(days)))
    sql = _FILL_SQL.format(since_clause=since_clause)
    with connection() as conn, conn.cursor() as cur:
        cur.execute(sql, params)
        return [dict(r) for r in cur.fetchall()]


def compute_roundtrips(days: int | None = None) -> RoundTripResult:
    """DB 로드 + FIFO 매칭 (편의 래퍼)."""
    return build_roundtrips(load_fill_rows(days))
