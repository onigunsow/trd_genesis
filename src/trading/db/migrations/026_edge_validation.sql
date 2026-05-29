-- Edge Validation — 일별 자산 스냅샷.
--
-- KIS inquire-balance 는 "오늘 잔고"만 제공한다(과거 차원 없음). 자산곡선/샤프/MDD 를
-- 시간가중(캘린더 기준)으로 산출하려면 매 거래일 마감 후 잔고를 영속화해야 한다 —
-- 한 번 지나간 날의 잔고는 복구할 수 없으므로 수집을 지금 시작한다.
--
-- realized_pnl_cum 은 inquire-balance 에 존재하지 않는다(balance() 는 미실현 평가손익
-- pnl_total 만 제공). 따라서 NULL 허용으로 두고, 라운드트립 분석(edge/roundtrips)이
-- 최신 행에 백필한다.
--
-- 멱등: CREATE TABLE IF NOT EXISTS + schema_migrations ON CONFLICT (024 하우스 스타일).

CREATE TABLE IF NOT EXISTS daily_equity_snapshot (
    trading_day      DATE         PRIMARY KEY,
    total_assets     BIGINT       NOT NULL,   -- KIS tot_evlu_amt (총자산평가금액)
    stock_eval       BIGINT       NOT NULL,   -- KIS scts_evlu_amt (주식평가금액)
    cash             BIGINT       NOT NULL,   -- KIS dnca_tot_amt (예수금 총액, cash_d2)
    unrealized_pnl   BIGINT       NOT NULL,   -- KIS evlu_pfls_smtl_amt (평가손익, 미실현)
    realized_pnl_cum BIGINT,                  -- 누적 실현손익 (balance() 미제공 → 라운드트립이 백필)
    created_at       TIMESTAMPTZ  NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS daily_equity_snapshot_day_idx
    ON daily_equity_snapshot (trading_day);

COMMENT ON TABLE daily_equity_snapshot IS
    'Edge Validation: 매 거래일 마감(15:40 KST) 후 KIS 잔고 스냅샷. 시간가중 자산곡선/샤프/MDD 의 원천.';
COMMENT ON COLUMN daily_equity_snapshot.realized_pnl_cum IS
    'balance() 미제공. edge/roundtrips 의 FIFO 매칭 누적 실현손익으로 백필(NULL 가능).';

INSERT INTO schema_migrations (version) VALUES ('026_edge_validation')
    ON CONFLICT DO NOTHING;

INSERT INTO audit_log (event_type, actor, details)
VALUES ('SCHEMA_MIGRATED', 'init', '{"migration":"026_edge_validation"}'::JSONB);
