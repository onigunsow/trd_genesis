-- SPEC-TRADING-054 M1.5 (ADR-004)
-- 종목별 평가 스냅샷 테이블: reconcile(KIS inquire-balance)이 매일 upsert.
-- 대시보드는 이 테이블을 읽기전용으로만 접근한다(REQ-054-A7).
-- PK = (trading_day, ticker) — 동일 날짜 동일 종목 upsert 시 최신값 덮어씌움.

CREATE TABLE IF NOT EXISTS position_eval_snapshot (
    trading_day   DATE    NOT NULL,             -- 기준 거래일 (Asia/Seoul)
    ticker        TEXT    NOT NULL,             -- 종목코드 (e.g. '005930')
    qty           INTEGER NOT NULL DEFAULT 0,   -- 보유 수량
    avg_cost      NUMERIC NOT NULL DEFAULT 0,   -- 평균매입단가 (원)
    eval_price    NUMERIC NOT NULL DEFAULT 0,   -- 현재가 (prpr)
    eval_amount   NUMERIC NOT NULL DEFAULT 0,   -- 평가금액 (evlu_amt)
    unrealized_pnl NUMERIC NOT NULL DEFAULT 0, -- 평가손익 (evlu_pfls_amt)
    pnl_pct       NUMERIC NOT NULL DEFAULT 0,  -- 평가손익률 % (evlu_pfls_rt) — MINOR-2
    updated_at    TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    PRIMARY KEY (trading_day, ticker)
);

-- 최신 trading_day 조회 성능 (포트폴리오 엔드포인트에서 자주 사용)
CREATE INDEX IF NOT EXISTS idx_position_eval_snapshot_day
    ON position_eval_snapshot (trading_day DESC);

-- dashboard_ro 역할 읽기 권한 부여
GRANT SELECT ON position_eval_snapshot TO dashboard_ro;
