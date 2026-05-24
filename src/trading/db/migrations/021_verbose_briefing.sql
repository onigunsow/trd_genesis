-- SPEC-TRADING-027: per-cycle decision-chain briefing detail toggle.
-- Default false = concise (one consolidated "사이클 요약" per cycle).
-- /detail sets true = also emit per-persona detail briefings.

ALTER TABLE system_state
    ADD COLUMN IF NOT EXISTS verbose_briefing BOOLEAN NOT NULL DEFAULT false;

COMMENT ON COLUMN system_state.verbose_briefing IS
    'SPEC-027: when true, emit per-persona detail briefings in addition to the consolidated cycle-chain summary';

INSERT INTO schema_migrations (version) VALUES ('021_verbose_briefing')
    ON CONFLICT DO NOTHING;
