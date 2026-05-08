-- SPEC-015 REQ-FALLBACK-06-1: Feature flag for CLI persona mode.
-- Defaults to false (safe rollout, opt-in activation after testing).

ALTER TABLE system_state
    ADD COLUMN IF NOT EXISTS cli_personas_enabled BOOLEAN NOT NULL DEFAULT false;

COMMENT ON COLUMN system_state.cli_personas_enabled IS
    'SPEC-015: When true, persona calls route through Claude CLI instead of Anthropic API';
