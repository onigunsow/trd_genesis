-- SPEC-TRADING-029 Phase D: add filled_at timestamp to orders table.
--
-- KIS order lifecycle sync (REQ-029-2) transitions orders from 'submitted' to
-- 'filled' / 'partial' / 'cancelled' / 'rejected' and stamps the moment the
-- fill (or terminal cancel/reject) was observed. Queries filter on status
-- rather than filled_at, so no index is needed.
--
-- Idempotent: ADD COLUMN IF NOT EXISTS is safe to re-run.
-- Existing rows have filled_at IS NULL until they are next transitioned.

ALTER TABLE orders ADD COLUMN IF NOT EXISTS filled_at TIMESTAMPTZ;

COMMENT ON COLUMN orders.filled_at IS
    'SPEC-TRADING-029 REQ-029-2: timestamp of the most recent lifecycle transition '
    '(filled / partial / cancelled / rejected) observed via KIS inquire-daily-ccld.';

INSERT INTO schema_migrations (version) VALUES ('022_add_filled_at')
    ON CONFLICT DO NOTHING;

INSERT INTO audit_log (event_type, actor, details)
VALUES ('SCHEMA_MIGRATED', 'init', '{"migration":"022_add_filled_at"}'::JSONB);
