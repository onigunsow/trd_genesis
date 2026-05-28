-- SPEC-TRADING-031: halt-cycle briefing cooldown — persist the throttle clock.
--
-- While halt_state=true, every trading cycle's halt gate previously emitted a
-- duplicate "매매 정지" Telegram briefing (~28/day under SPEC-024's */15 cron).
-- REQ-031-1 throttles that to once per cooldown (default 6h) and REQ-031-2 fires
-- immediately on the first cycle of a halt episode. The decision needs a
-- persisted last-notified timestamp so the cooldown survives a container restart
-- (REQ-031-1c) — calendar-day reset is explicitly NOT used.
--
-- halt_notified_at IS NULL means "no cycle-gate briefing sent this episode yet"
-- (first cycle notifies immediately). circuit_breaker.reset() (=/resume) sets it
-- back to NULL so the next episode's first cycle notifies immediately (REQ-031-3).
--
-- Idempotent: information_schema guard (matches 013_event_car_atr.sql house
-- style). Safe to re-run.

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_name = 'system_state' AND column_name = 'halt_notified_at'
    ) THEN
        ALTER TABLE system_state ADD COLUMN halt_notified_at TIMESTAMPTZ;
    END IF;
END $$;

COMMENT ON COLUMN system_state.halt_notified_at IS
    'SPEC-TRADING-031 REQ-031-1/2/3: timestamp of the most recent halt-cycle '
    '"매매 정지" briefing. NULL = no briefing sent this halt episode yet (first '
    'cycle notifies immediately). reset()/resume clears it back to NULL.';

INSERT INTO schema_migrations (version) VALUES ('023_halt_notify_cooldown')
    ON CONFLICT DO NOTHING;

INSERT INTO audit_log (event_type, actor, details)
VALUES ('SCHEMA_MIGRATED', 'init', '{"migration":"023_halt_notify_cooldown"}'::JSONB);
