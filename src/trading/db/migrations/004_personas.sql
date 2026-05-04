-- M4 migration — persona system tables.
-- REQ-PERSONA-04-2 (every persona invocation persisted with token cost),
-- REQ-PERSONA-04-3 (Decision signals + Risk reviews).

CREATE TABLE IF NOT EXISTS persona_runs (
    id              BIGSERIAL    PRIMARY KEY,
    ts              TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    persona_name    TEXT         NOT NULL,                -- macro|micro|decision|risk|portfolio|retrospective
    model           TEXT         NOT NULL,                -- e.g. claude-sonnet-4-6, claude-opus-4-7
    cycle_kind      TEXT         NOT NULL,                -- pre_market|intraday|event|weekly|manual
    trigger_context JSONB        NOT NULL DEFAULT '{}'::JSONB,
    prompt          TEXT         NOT NULL,
    response        TEXT         NOT NULL,
    response_json   JSONB,                                 -- parsed structured output if any
    input_tokens    INTEGER      NOT NULL DEFAULT 0,
    output_tokens   INTEGER      NOT NULL DEFAULT 0,
    cost_krw        NUMERIC(10,2) NOT NULL DEFAULT 0,
    latency_ms      INTEGER      NOT NULL DEFAULT 0,
    error           TEXT
);

CREATE INDEX IF NOT EXISTS persona_runs_persona_ts_idx ON persona_runs (persona_name, ts DESC);
CREATE INDEX IF NOT EXISTS persona_runs_cycle_idx ON persona_runs (cycle_kind, ts DESC);
CREATE INDEX IF NOT EXISTS persona_runs_ts_idx ON persona_runs (ts DESC);

CREATE TABLE IF NOT EXISTS persona_decisions (
    id              BIGSERIAL    PRIMARY KEY,
    ts              TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    persona_run_id  BIGINT       NOT NULL REFERENCES persona_runs(id) ON DELETE CASCADE,
    macro_run_id    BIGINT       REFERENCES persona_runs(id),
    micro_run_id    BIGINT       REFERENCES persona_runs(id),
    cycle_kind      TEXT         NOT NULL,
    ticker          TEXT         NOT NULL,
    side            TEXT         NOT NULL CHECK (side IN ('buy','sell','hold')),
    qty             INTEGER      NOT NULL DEFAULT 0,
    rationale       TEXT         NOT NULL,
    confidence      NUMERIC(4,2),                          -- 0.00 ~ 1.00
    raw             JSONB        NOT NULL DEFAULT '{}'::JSONB
);

CREATE INDEX IF NOT EXISTS persona_decisions_ts_idx ON persona_decisions (ts DESC);
CREATE INDEX IF NOT EXISTS persona_decisions_ticker_idx ON persona_decisions (ticker, ts DESC);

CREATE TABLE IF NOT EXISTS risk_reviews (
    id                  BIGSERIAL    PRIMARY KEY,
    ts                  TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    persona_run_id      BIGINT       NOT NULL REFERENCES persona_runs(id) ON DELETE CASCADE,
    decision_id         BIGINT       NOT NULL REFERENCES persona_decisions(id) ON DELETE CASCADE,
    verdict             TEXT         NOT NULL CHECK (verdict IN ('APPROVE','HOLD','REJECT')),
    rationale           TEXT         NOT NULL,
    code_rules_passed   BOOLEAN      NOT NULL DEFAULT FALSE,
    raw                 JSONB        NOT NULL DEFAULT '{}'::JSONB
);

CREATE INDEX IF NOT EXISTS risk_reviews_ts_idx ON risk_reviews (ts DESC);
CREATE INDEX IF NOT EXISTS risk_reviews_verdict_idx ON risk_reviews (verdict);

-- Daily reports (M5 will populate; create the table now to keep schema linear)
CREATE TABLE IF NOT EXISTS daily_reports (
    id              BIGSERIAL    PRIMARY KEY,
    ts              TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    trading_day     DATE         NOT NULL UNIQUE,
    summary         TEXT         NOT NULL,
    details         JSONB        NOT NULL DEFAULT '{}'::JSONB
);

INSERT INTO schema_migrations (version) VALUES ('004_personas') ON CONFLICT DO NOTHING;
INSERT INTO audit_log (event_type, actor, details)
VALUES ('SCHEMA_MIGRATED', 'init', '{"migration":"004_personas"}'::JSONB);
