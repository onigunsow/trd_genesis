-- M5 migration — observability + retrospectives + portfolio adjustments.

CREATE TABLE IF NOT EXISTS retrospectives (
    id              BIGSERIAL    PRIMARY KEY,
    ts              TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    week_start      DATE         NOT NULL UNIQUE,
    week_end        DATE         NOT NULL,
    persona_run_id  BIGINT       NOT NULL REFERENCES persona_runs(id) ON DELETE CASCADE,
    summary         TEXT         NOT NULL DEFAULT '',
    improvements    JSONB        NOT NULL DEFAULT '[]'::JSONB
);

CREATE INDEX IF NOT EXISTS retrospectives_ts_idx ON retrospectives (ts DESC);

CREATE TABLE IF NOT EXISTS portfolio_adjustments (
    id              BIGSERIAL    PRIMARY KEY,
    ts              TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    persona_run_id  BIGINT       NOT NULL REFERENCES persona_runs(id) ON DELETE CASCADE,
    decision_id     BIGINT       NOT NULL REFERENCES persona_decisions(id) ON DELETE CASCADE,
    qty_original    INTEGER      NOT NULL,
    qty_adjusted    INTEGER      NOT NULL,
    rationale       TEXT         NOT NULL DEFAULT '',
    raw             JSONB        NOT NULL DEFAULT '{}'::JSONB
);

CREATE INDEX IF NOT EXISTS portfolio_adjustments_ts_idx ON portfolio_adjustments (ts DESC);

INSERT INTO schema_migrations (version) VALUES ('005_m5_observability') ON CONFLICT DO NOTHING;
INSERT INTO audit_log (event_type, actor, details)
VALUES ('SCHEMA_MIGRATED', 'init', '{"migration":"005_m5_observability"}'::JSONB);
