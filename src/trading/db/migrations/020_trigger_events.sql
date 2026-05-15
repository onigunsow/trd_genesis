-- SPEC-TRADING-024 Stage 1 REQ-024-2/3/4: trigger_events table.
--
-- Append-only log of watcher-fired events (price threshold, volume anomaly,
-- blocked release). Used for postmortem analysis and Phase 3 threshold tuning.
--
-- cycle_run_id FK is nullable because Stage 1 fires standard intraday cycles
-- which do not (yet) link back per-event. Stage 2 multi-tier dispatch will
-- populate this column.

CREATE TABLE IF NOT EXISTS trigger_events (
    id              BIGSERIAL PRIMARY KEY,
    ticker          TEXT NOT NULL,
    trigger_type    TEXT NOT NULL CHECK (
        trigger_type IN ('price_threshold', 'volume_anomaly', 'blocked_release')
    ),
    fired_at        TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    metadata        JSONB NOT NULL DEFAULT '{}'::jsonb,
    cycle_run_id    BIGINT REFERENCES persona_runs(id) ON DELETE SET NULL
);

CREATE INDEX IF NOT EXISTS trigger_events_ticker_fired_idx
    ON trigger_events (ticker, fired_at DESC);
CREATE INDEX IF NOT EXISTS trigger_events_fired_at_idx
    ON trigger_events (fired_at DESC);

INSERT INTO schema_migrations (version) VALUES ('020_trigger_events')
    ON CONFLICT DO NOTHING;
