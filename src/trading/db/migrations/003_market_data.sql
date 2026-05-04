-- M3 migration — market data caches.
-- Implements REQ-DATA-03-2 (cache OHLCV + macro + disclosures).

CREATE TABLE IF NOT EXISTS ohlcv (
    source       TEXT          NOT NULL,                  -- pykrx | yfinance
    symbol       TEXT          NOT NULL,                  -- 005930 / ^GSPC / etc.
    ts           DATE          NOT NULL,                  -- trading date (KST or US)
    open         NUMERIC(18,4) NOT NULL,
    high         NUMERIC(18,4) NOT NULL,
    low          NUMERIC(18,4) NOT NULL,
    close        NUMERIC(18,4) NOT NULL,
    volume       BIGINT        NOT NULL DEFAULT 0,
    adj_close    NUMERIC(18,4),                           -- yfinance only; null for pykrx
    fetched_at   TIMESTAMPTZ   NOT NULL DEFAULT NOW(),
    PRIMARY KEY (source, symbol, ts)
);

CREATE INDEX IF NOT EXISTS ohlcv_symbol_ts_idx ON ohlcv (symbol, ts DESC);
CREATE INDEX IF NOT EXISTS ohlcv_source_idx ON ohlcv (source);

CREATE TABLE IF NOT EXISTS macro_indicators (
    source       TEXT          NOT NULL,                  -- fred | ecos
    series_id    TEXT          NOT NULL,                  -- e.g. DFF, BOK_KEYRATE
    ts           DATE          NOT NULL,
    value        NUMERIC(20,8) NOT NULL,
    units        TEXT,
    fetched_at   TIMESTAMPTZ   NOT NULL DEFAULT NOW(),
    PRIMARY KEY (source, series_id, ts)
);

CREATE INDEX IF NOT EXISTS macro_series_idx ON macro_indicators (series_id, ts DESC);

CREATE TABLE IF NOT EXISTS disclosures (
    rcept_no     TEXT          PRIMARY KEY,               -- DART receipt number (unique)
    corp_code    TEXT          NOT NULL,
    corp_name    TEXT          NOT NULL,
    stock_code   TEXT,                                     -- 6-digit, may be null for non-listed
    report_nm    TEXT          NOT NULL,
    rcept_dt     DATE          NOT NULL,
    flr_nm       TEXT,
    rm           TEXT,
    fetched_at   TIMESTAMPTZ   NOT NULL DEFAULT NOW(),
    raw          JSONB         NOT NULL DEFAULT '{}'::JSONB
);

CREATE INDEX IF NOT EXISTS disclosures_stock_dt_idx ON disclosures (stock_code, rcept_dt DESC);
CREATE INDEX IF NOT EXISTS disclosures_dt_idx ON disclosures (rcept_dt DESC);

CREATE TABLE IF NOT EXISTS benchmark_runs (
    id           BIGSERIAL    PRIMARY KEY,
    ts           TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    strategy     TEXT         NOT NULL,                   -- sma_cross | dual_momentum
    universe     TEXT         NOT NULL,                   -- e.g. KOSPI200, watchlist
    start_date   DATE         NOT NULL,
    end_date     DATE         NOT NULL,
    params       JSONB        NOT NULL DEFAULT '{}'::JSONB,
    cagr         NUMERIC(10,6),
    mdd          NUMERIC(10,6),
    sharpe       NUMERIC(10,4),
    trades       INTEGER,
    final_equity NUMERIC(20,2),
    summary      JSONB        NOT NULL DEFAULT '{}'::JSONB
);

CREATE INDEX IF NOT EXISTS benchmark_runs_strategy_idx ON benchmark_runs (strategy, ts DESC);

INSERT INTO schema_migrations (version) VALUES ('003_market_data') ON CONFLICT DO NOTHING;
INSERT INTO audit_log (event_type, actor, details)
VALUES ('SCHEMA_MIGRATED', 'init', '{"migration":"003_market_data"}'::JSONB);

COMMENT ON TABLE ohlcv IS
    'OHLCV cache. Idempotent upsert by (source, symbol, ts). Backfill from 2019-01-01.';
COMMENT ON TABLE benchmark_runs IS
    'Persisted backtest results — used in M5 to compare persona system vs rule-based benchmark.';
