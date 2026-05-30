-- SPEC-TRADING-038 REQ-038-2: persist the once-per-day position-action guard
-- (e.g. take-profit) so it survives a container restart and cannot trigger a
-- double half-sell.
--
-- The position_watchdog previously kept this guard in an in-memory dict, which
-- reset on restart: a 14:00 take-profit could be repeated at 14:30 after a
-- restart. A DB-backed marker keyed by (trading_day, ticker, action) makes the
-- guard restart-safe; the UNIQUE constraint enforces the double-action ban at
-- the DB level (INSERT ... ON CONFLICT DO NOTHING).
--
-- Idempotent: CREATE TABLE IF NOT EXISTS + schema_migrations ON CONFLICT
-- (026 house style). The marker is naturally daily — a new trading_day key is a
-- fresh row, so the next day resets without any cleanup job.

CREATE TABLE IF NOT EXISTS position_action_markers (
    id           BIGSERIAL    PRIMARY KEY,
    trading_day  DATE         NOT NULL,             -- KST 거래일
    ticker       TEXT         NOT NULL,
    action       TEXT         NOT NULL,             -- 예: 'take_profit'
    created_at   TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    CONSTRAINT position_action_markers_uniq UNIQUE (trading_day, ticker, action)
);

CREATE INDEX IF NOT EXISTS position_action_markers_lookup_idx
    ON position_action_markers (trading_day, ticker, action);

COMMENT ON TABLE position_action_markers IS
    'SPEC-TRADING-038 REQ-038-2: 포지션 액션 1일 1회 가드(예 익절)의 재시작 내성 영속 마커.';

INSERT INTO schema_migrations (version) VALUES ('028_position_action_markers')
    ON CONFLICT DO NOTHING;

INSERT INTO audit_log (event_type, actor, details)
VALUES ('SCHEMA_MIGRATED', 'init', '{"migration":"028_position_action_markers"}'::JSONB);
