-- SPEC-TRADING-039 REQ-039-1/3: mark paper-mode synthetic fills so the
-- SPEC-029 balance-reconcile FIFO never double-counts an order that was already
-- filled synthetically at submission time.
--
-- Paper SELL orders previously stayed in status='submitted' forever because the
-- only fill-sync path (fills._transition_orders_fifo) is BUY-only and KIS paper
-- does not report same-day sell fills. SPEC-039 fills paper orders synthetically
-- at submit time; this column lets reconcile exclude those rows (it adds
-- `AND synthetic = false` to its WHERE clauses) so a synthetically-filled order
-- is never re-attributed against KIS balance holdings.
--
-- Live orders never set synthetic=true (paper-only hard gate, REQ-039-2), so the
-- column is FALSE for every live row and reconcile behaviour for live is
-- byte-for-byte unchanged.
--
-- Idempotent: ADD COLUMN IF NOT EXISTS + schema_migrations ON CONFLICT
-- (026/028 house style). Safe to re-run.

ALTER TABLE orders
    ADD COLUMN IF NOT EXISTS synthetic BOOLEAN NOT NULL DEFAULT FALSE;

COMMENT ON COLUMN orders.synthetic IS
    'SPEC-TRADING-039: TRUE when this order was filled by the paper-only synthetic-fill layer at submission time (never set for live). reconcile_from_balance excludes synthetic rows from FIFO attribution to avoid double-counting.';

INSERT INTO schema_migrations (version) VALUES ('029_orders_synthetic_marker')
    ON CONFLICT DO NOTHING;

INSERT INTO audit_log (event_type, actor, details)
VALUES ('SCHEMA_MIGRATED', 'init', '{"migration":"029_orders_synthetic_marker"}'::JSONB);
