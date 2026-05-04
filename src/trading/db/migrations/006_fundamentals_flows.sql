-- M5 정밀화 — 종목 펀더멘탈 + 외국인/기관 매매.
-- 마이크로 페르소나가 진짜 분석을 할 수 있도록 input 풍부화.

CREATE TABLE IF NOT EXISTS fundamentals (
    ticker          TEXT          NOT NULL,
    ts              DATE          NOT NULL,
    market_cap      BIGINT,                                -- 시가총액 원
    per             NUMERIC(12,4),
    pbr             NUMERIC(12,4),
    eps             NUMERIC(18,4),
    bps             NUMERIC(18,4),
    div_yield       NUMERIC(8,4),                           -- 배당수익률 %
    dps             NUMERIC(18,4),
    fetched_at      TIMESTAMPTZ   NOT NULL DEFAULT NOW(),
    PRIMARY KEY (ticker, ts)
);

CREATE INDEX IF NOT EXISTS fundamentals_ticker_ts_idx ON fundamentals (ticker, ts DESC);

CREATE TABLE IF NOT EXISTS flows (
    ticker          TEXT          NOT NULL,
    ts              DATE          NOT NULL,
    foreign_net     BIGINT        NOT NULL DEFAULT 0,      -- 외국인 순매수 금액 (원)
    institution_net BIGINT        NOT NULL DEFAULT 0,      -- 기관 순매수 금액
    individual_net  BIGINT        NOT NULL DEFAULT 0,      -- 개인 순매수 금액
    fetched_at      TIMESTAMPTZ   NOT NULL DEFAULT NOW(),
    PRIMARY KEY (ticker, ts)
);

CREATE INDEX IF NOT EXISTS flows_ticker_ts_idx ON flows (ticker, ts DESC);

INSERT INTO schema_migrations (version) VALUES ('006_fundamentals_flows') ON CONFLICT DO NOTHING;
INSERT INTO audit_log (event_type, actor, details)
VALUES ('SCHEMA_MIGRATED', 'init', '{"migration":"006_fundamentals_flows"}'::JSONB);
