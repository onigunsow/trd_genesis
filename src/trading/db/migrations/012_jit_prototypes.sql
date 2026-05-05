-- Migration 012: JIT State Reconstruction Pipeline + Prototype-based Risk Management
-- SPEC-TRADING-011
--
-- Tables: snapshots, delta_events, market_prototypes, prototype_similarity_log
-- Feature flags: jit_pipeline_enabled, jit_websocket_enabled, jit_dart_polling_enabled,
--               jit_news_polling_enabled, prototype_risk_enabled

-- Snapshot tracking (base snapshots from cron builds)
CREATE TABLE IF NOT EXISTS snapshots (
    id BIGSERIAL PRIMARY KEY,
    snapshot_type TEXT NOT NULL,
    generated_at TIMESTAMPTZ NOT NULL,
    file_path TEXT NOT NULL,
    content_hash TEXT NOT NULL,
    delta_count INTEGER DEFAULT 0,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Delta Event Pipeline (intraday events: price, disclosure, news)
CREATE TABLE IF NOT EXISTS delta_events (
    id BIGSERIAL PRIMARY KEY,
    event_type TEXT NOT NULL,
    source TEXT NOT NULL,
    ticker TEXT,
    payload JSONB NOT NULL,
    event_ts TIMESTAMPTZ NOT NULL,
    ingested_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    snapshot_id BIGINT REFERENCES snapshots(id),
    merged BOOLEAN NOT NULL DEFAULT false
);

CREATE INDEX IF NOT EXISTS idx_delta_events_type_ts
    ON delta_events(event_type, event_ts DESC);
CREATE INDEX IF NOT EXISTS idx_delta_events_ticker_ts
    ON delta_events(ticker, event_ts DESC) WHERE ticker IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_delta_events_snapshot
    ON delta_events(snapshot_id) WHERE merged = false;

-- Market Prototype Library (historical scenario embeddings)
CREATE TABLE IF NOT EXISTS market_prototypes (
    id BIGSERIAL PRIMARY KEY,
    name TEXT NOT NULL UNIQUE,
    description TEXT NOT NULL,
    category TEXT NOT NULL,
    time_period_start DATE NOT NULL,
    time_period_end DATE NOT NULL,
    market_conditions JSONB NOT NULL,
    key_indicators JSONB NOT NULL,
    outcome JSONB NOT NULL,
    risk_recommendation JSONB NOT NULL,
    embedding vector(1024) NOT NULL,
    source TEXT NOT NULL DEFAULT 'manual',
    is_active BOOLEAN NOT NULL DEFAULT true,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_prototypes_category
    ON market_prototypes(category);
CREATE INDEX IF NOT EXISTS idx_prototypes_embedding
    ON market_prototypes USING ivfflat (embedding vector_cosine_ops) WITH (lists = 10);

-- Prototype Similarity Log (per-cycle computation results)
CREATE TABLE IF NOT EXISTS prototype_similarity_log (
    id BIGSERIAL PRIMARY KEY,
    cycle_kind TEXT NOT NULL,
    current_state_embedding vector(1024),
    top_matches JSONB NOT NULL,
    applied_ceiling_pct REAL,
    static_limit_pct REAL NOT NULL DEFAULT 80.0,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_proto_sim_log_ts
    ON prototype_similarity_log(created_at DESC);

-- Feature flags in system_state
ALTER TABLE system_state ADD COLUMN IF NOT EXISTS jit_pipeline_enabled BOOLEAN NOT NULL DEFAULT false;
ALTER TABLE system_state ADD COLUMN IF NOT EXISTS jit_websocket_enabled BOOLEAN NOT NULL DEFAULT false;
ALTER TABLE system_state ADD COLUMN IF NOT EXISTS jit_dart_polling_enabled BOOLEAN NOT NULL DEFAULT false;
ALTER TABLE system_state ADD COLUMN IF NOT EXISTS jit_news_polling_enabled BOOLEAN NOT NULL DEFAULT false;
ALTER TABLE system_state ADD COLUMN IF NOT EXISTS prototype_risk_enabled BOOLEAN NOT NULL DEFAULT false;
