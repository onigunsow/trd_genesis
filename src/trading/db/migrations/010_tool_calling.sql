-- SPEC-TRADING-009 Phase A — Tool Calling Infrastructure
-- REQ-REFL-03-6: reflection_rounds table
-- REQ-TOOL-01-7: tool_call_log table
-- REQ-PTOOL-02-7: persona_runs additional columns
-- REQ-COMPAT-04-1/2: system_state feature flags

-- UP -----------------------------------------------------------------------

-- Reflection rounds table (REQ-REFL-03-6)
CREATE TABLE IF NOT EXISTS reflection_rounds (
    id BIGSERIAL PRIMARY KEY,
    cycle_kind TEXT NOT NULL,
    original_decision_id BIGINT REFERENCES persona_decisions(id),
    round_number SMALLINT NOT NULL CHECK (round_number IN (1, 2)),
    risk_persona_run_id BIGINT NOT NULL REFERENCES persona_runs(id),
    risk_rationale TEXT,
    revised_decision_run_id BIGINT REFERENCES persona_runs(id),
    revised_risk_run_id BIGINT REFERENCES persona_runs(id),
    final_verdict TEXT NOT NULL CHECK (final_verdict IN ('APPROVE', 'REJECT', 'WITHDRAWN')),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- persona_runs: tool-calling token accounting (REQ-PTOOL-02-7)
ALTER TABLE persona_runs
    ADD COLUMN IF NOT EXISTS tool_calls_count INTEGER NOT NULL DEFAULT 0,
    ADD COLUMN IF NOT EXISTS tool_input_tokens INTEGER NOT NULL DEFAULT 0,
    ADD COLUMN IF NOT EXISTS tool_output_tokens INTEGER NOT NULL DEFAULT 0;

-- system_state: feature flags (REQ-COMPAT-04-1, REQ-COMPAT-04-2)
ALTER TABLE system_state
    ADD COLUMN IF NOT EXISTS tool_calling_enabled BOOLEAN NOT NULL DEFAULT false,
    ADD COLUMN IF NOT EXISTS reflection_loop_enabled BOOLEAN NOT NULL DEFAULT false;

-- Tool call audit log (REQ-TOOL-01-7)
CREATE TABLE IF NOT EXISTS tool_call_log (
    id BIGSERIAL PRIMARY KEY,
    persona_run_id BIGINT REFERENCES persona_runs(id),
    tool_name TEXT NOT NULL,
    input_hash TEXT NOT NULL,
    execution_ms INTEGER NOT NULL,
    success BOOLEAN NOT NULL,
    result_bytes INTEGER,
    error TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_tool_call_log_run
    ON tool_call_log(persona_run_id);
CREATE INDEX IF NOT EXISTS idx_tool_call_log_name
    ON tool_call_log(tool_name, created_at DESC);

-- Migration bookkeeping
INSERT INTO schema_migrations (version) VALUES ('010_tool_calling') ON CONFLICT DO NOTHING;
INSERT INTO audit_log (event_type, actor, details)
VALUES ('SCHEMA_MIGRATED', 'init', '{"migration":"010_tool_calling"}'::JSONB);

COMMENT ON TABLE reflection_rounds IS
    'SPEC-009 REQ-REFL-03-6: Risk REJECT reflection loop persistence.';
COMMENT ON TABLE tool_call_log IS
    'SPEC-009 REQ-TOOL-01-7: Audit trail for every tool invocation.';
COMMENT ON COLUMN persona_runs.tool_calls_count IS
    'SPEC-009 REQ-PTOOL-02-7: Number of tool calls in this persona invocation.';
COMMENT ON COLUMN persona_runs.tool_input_tokens IS
    'SPEC-009 REQ-PTOOL-02-7: Total input tokens from tool result messages.';
COMMENT ON COLUMN persona_runs.tool_output_tokens IS
    'SPEC-009 REQ-PTOOL-02-7: Total output tokens consumed by tool-use responses.';
COMMENT ON COLUMN system_state.tool_calling_enabled IS
    'SPEC-009 REQ-COMPAT-04-1: Feature flag for tool-calling active retrieval.';
COMMENT ON COLUMN system_state.reflection_loop_enabled IS
    'SPEC-009 REQ-COMPAT-04-2: Feature flag for Risk REJECT reflection loop.';

-- DOWN (rollback) ----------------------------------------------------------
-- To reverse this migration, execute:
--
-- DROP TABLE IF EXISTS tool_call_log;
-- DROP TABLE IF EXISTS reflection_rounds;
-- ALTER TABLE persona_runs DROP COLUMN IF EXISTS tool_calls_count;
-- ALTER TABLE persona_runs DROP COLUMN IF EXISTS tool_input_tokens;
-- ALTER TABLE persona_runs DROP COLUMN IF EXISTS tool_output_tokens;
-- ALTER TABLE system_state DROP COLUMN IF EXISTS tool_calling_enabled;
-- ALTER TABLE system_state DROP COLUMN IF EXISTS reflection_loop_enabled;
-- DELETE FROM schema_migrations WHERE version = '010_tool_calling';
