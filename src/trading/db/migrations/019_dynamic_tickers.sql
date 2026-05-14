-- SPEC-TRADING-023 REQ-023-2: dynamic_tickers registry.
--
-- Stores tickers auto-expanded into the data universe by micro persona
-- recommendations of universe-out symbols. SPEC-019 daily refresh cron picks
-- these up automatically via get_data_universe().
--
-- Capacity is bounded at 100 rows (REQ-023-5 (e); configurable via env
-- DYNAMIC_UNIVERSE_CAP). When the cap is reached, the row with the oldest
-- first_seen_at is evicted (FIFO).

CREATE TABLE IF NOT EXISTS dynamic_tickers (
    ticker          TEXT PRIMARY KEY,
    first_seen_at   TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    last_used_at    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    source          TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_dynamic_tickers_first_seen
    ON dynamic_tickers (first_seen_at);

INSERT INTO schema_migrations (version) VALUES ('019_dynamic_tickers')
    ON CONFLICT DO NOTHING;
