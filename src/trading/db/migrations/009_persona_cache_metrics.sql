-- SPEC-TRADING-008 Phase A — Prompt cache metrics on persona_runs.
-- REQ-CACHE-01-3: cache_read_input_tokens, cache_creation_input_tokens 저장.

ALTER TABLE persona_runs
    ADD COLUMN IF NOT EXISTS cache_read_tokens INTEGER NOT NULL DEFAULT 0,
    ADD COLUMN IF NOT EXISTS cache_creation_tokens INTEGER NOT NULL DEFAULT 0;

CREATE INDEX IF NOT EXISTS persona_runs_cache_idx
    ON persona_runs (ts DESC) WHERE cache_read_tokens > 0;

INSERT INTO schema_migrations (version) VALUES ('009_persona_cache_metrics') ON CONFLICT DO NOTHING;
INSERT INTO audit_log (event_type, actor, details)
VALUES ('SCHEMA_MIGRATED', 'init', '{"migration":"009_persona_cache_metrics"}'::JSONB);

COMMENT ON COLUMN persona_runs.cache_read_tokens IS
    'SPEC-008 REQ-CACHE-01-3: Anthropic prompt cache hit 시 재사용된 input 토큰 수. 90% 할인 적용.';
COMMENT ON COLUMN persona_runs.cache_creation_tokens IS
    'SPEC-008 REQ-CACHE-01-3: Anthropic prompt cache 첫 생성 시 쓰여진 input 토큰 수. 25% 추가 비용.';
