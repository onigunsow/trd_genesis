-- M2 migration — KIS orders, positions.
-- Implements REQ-KIS-02-3 (DB schema v1) and REQ-KIS-02-4 (every order persisted).

CREATE TABLE IF NOT EXISTS orders (
    id              BIGSERIAL    PRIMARY KEY,
    ts              TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    mode            TEXT         NOT NULL CHECK (mode IN ('paper','live')),
    side            TEXT         NOT NULL CHECK (side IN ('buy','sell')),
    ticker          TEXT         NOT NULL,
    qty             INTEGER      NOT NULL CHECK (qty > 0),
    order_type      TEXT         NOT NULL CHECK (order_type IN ('market','limit')),
    limit_price     INTEGER,                              -- NULL for market orders
    request         JSONB        NOT NULL DEFAULT '{}'::JSONB,
    response        JSONB        NOT NULL DEFAULT '{}'::JSONB,
    kis_order_no    TEXT,                                 -- KRX order number from KIS response
    status          TEXT         NOT NULL CHECK (status IN
                                  ('submitted','filled','partial','rejected','cancelled','error')),
    rejected_reason TEXT,
    fill_price      INTEGER,
    fill_qty        INTEGER,
    fee             INTEGER      NOT NULL DEFAULT 0,
    persona_decision_id BIGINT  -- ref to persona_decisions.id (nullable; populated in M4)
);

CREATE INDEX IF NOT EXISTS orders_ts_idx ON orders (ts DESC);
CREATE INDEX IF NOT EXISTS orders_ticker_idx ON orders (ticker, ts DESC);
CREATE INDEX IF NOT EXISTS orders_status_idx ON orders (status);
CREATE INDEX IF NOT EXISTS orders_mode_idx ON orders (mode, ts DESC);

CREATE TABLE IF NOT EXISTS positions (
    id              BIGSERIAL    PRIMARY KEY,
    ticker          TEXT         NOT NULL UNIQUE,
    qty             INTEGER      NOT NULL CHECK (qty >= 0),
    avg_cost        INTEGER      NOT NULL DEFAULT 0,      -- weighted average cost in KRW
    last_updated    TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    last_order_id   BIGINT       REFERENCES orders(id) ON DELETE SET NULL
);

CREATE INDEX IF NOT EXISTS positions_ticker_idx ON positions (ticker);

COMMENT ON TABLE orders IS
    'Every KIS order request and response is persisted here (REQ-KIS-02-4). Includes paper and live.';
COMMENT ON TABLE positions IS
    'Aggregated holdings derived from orders. Updated on each fill.';
COMMENT ON COLUMN orders.persona_decision_id IS
    'Foreign key to persona_decisions added in M4. Nullable for manual orders during M2 verification.';

-- Migration tracking — record this migration was applied.
CREATE TABLE IF NOT EXISTS schema_migrations (
    version     TEXT         PRIMARY KEY,
    applied_at  TIMESTAMPTZ  NOT NULL DEFAULT NOW()
);

INSERT INTO schema_migrations (version) VALUES ('001_system_state') ON CONFLICT DO NOTHING;
INSERT INTO schema_migrations (version) VALUES ('002_kis_orders') ON CONFLICT DO NOTHING;

INSERT INTO audit_log (event_type, actor, details)
VALUES ('SCHEMA_MIGRATED', 'init', '{"migration":"002_kis_orders"}'::JSONB);
