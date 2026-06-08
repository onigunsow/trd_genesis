-- SPEC-TRADING-042 Module B REQ-042-B1: extend the orders.status CHECK enum with
-- 'expired' so the order-state resolver can deterministically converge an order
-- that is stuck in 'submitted' beyond its bounded window when its fill cannot be
-- confirmed (and it was not actively KIS-cancelled).
--
-- RC-2 (2026-06-01..06-08): 5 SELL orders accepted by KIS (rt_cd=0 → 'submitted')
-- whose synthetic/fill step threw were left in 'submitted' forever — no resolver,
-- no timeout. The resolver maps a stuck order to one of three terminal states:
--   filled    — KIS / balance reconcile confirms the execution.
--   cancelled — an active KIS cancel succeeded (reserved; the live cancel TR is
--               not yet verified, mirroring the fill-inquiry seam).
--   expired   — the bounded window elapsed and the fill could NOT be confirmed
--               (and we did not fabricate a fill — REQ-042-B3). The order-state
--               ledger converges; the genuine exit intent is re-evaluated next
--               cycle from KIS truth (Module A intraday reconcile + phantom clamp).
--
-- 'submitted','filled','partial','rejected','cancelled','error' were already
-- allowed (verified 2026-06-08); only 'expired' is new. Postgres CHECK constraints
-- cannot be "extended" in place, so we DROP and re-ADD the full enum (the new set
-- is a strict superset, so every existing row continues to satisfy it).
--
-- Reversible: the down-migration below (commented) restores the pre-031 enum. It
-- is safe only once no row holds status='expired'; the resolver is the only writer
-- of that value, so an operator rolling back must first re-resolve those rows.
--
-- Idempotent: guarded by a NOT-already-applied check on schema_migrations so a
-- re-run is a no-op (026/028/029 house style). Safe to re-run.
--
-- OPERATOR STEP — this migration is NOT auto-applied to the live DB. Apply with:
--   docker exec -i trading-postgres psql -U trading -d trading \
--     < src/trading/db/migrations/031_orders_status_expired.sql
-- (or `trading migrate`, which runs all pending migrations in order).

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM schema_migrations WHERE version = '031_orders_status_expired'
    ) THEN
        ALTER TABLE orders DROP CONSTRAINT IF EXISTS orders_status_check;
        ALTER TABLE orders
            ADD CONSTRAINT orders_status_check
            CHECK (status = ANY (ARRAY[
                'submitted'::text,
                'filled'::text,
                'partial'::text,
                'rejected'::text,
                'cancelled'::text,
                'expired'::text,
                'error'::text
            ]));

        INSERT INTO schema_migrations (version)
        VALUES ('031_orders_status_expired')
        ON CONFLICT DO NOTHING;

        INSERT INTO audit_log (event_type, actor, details)
        VALUES (
            'SCHEMA_MIGRATED', 'init',
            '{"migration":"031_orders_status_expired"}'::JSONB
        );
    END IF;
END $$;

-- ── Down-migration (manual; only after re-resolving any status='expired' rows) ──
-- ALTER TABLE orders DROP CONSTRAINT IF EXISTS orders_status_check;
-- ALTER TABLE orders
--     ADD CONSTRAINT orders_status_check
--     CHECK (status = ANY (ARRAY[
--         'submitted'::text,'filled'::text,'partial'::text,
--         'rejected'::text,'cancelled'::text,'error'::text
--     ]));
-- DELETE FROM schema_migrations WHERE version = '031_orders_status_expired';
