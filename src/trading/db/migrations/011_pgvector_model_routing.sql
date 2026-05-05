-- SPEC-TRADING-010 — pgvector + Model Routing + Semantic Retrieval
-- REQ-PGVEC-02-1: Enable pgvector extension
-- REQ-PGVEC-02-2: context_embeddings table
-- REQ-ROUTER-01-3: model_routing JSONB in system_state
-- REQ-MIGR-05-7: Feature flags (semantic_retrieval_enabled, shadow_test_active)

-- UP -----------------------------------------------------------------------

-- Enable pgvector extension (REQ-PGVEC-02-1)
CREATE EXTENSION IF NOT EXISTS vector;

-- Context embeddings table (REQ-PGVEC-02-2)
CREATE TABLE IF NOT EXISTS context_embeddings (
    id BIGSERIAL PRIMARY KEY,
    source_file TEXT NOT NULL,
    chunk_index INTEGER NOT NULL,
    chunk_text TEXT NOT NULL,
    chunk_tokens INTEGER NOT NULL,
    embedding vector(1024) NOT NULL,
    metadata JSONB,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE(source_file, chunk_index)
);

CREATE INDEX IF NOT EXISTS idx_context_embeddings_source
    ON context_embeddings(source_file);
CREATE INDEX IF NOT EXISTS idx_context_embeddings_vector
    ON context_embeddings USING ivfflat (embedding vector_cosine_ops) WITH (lists = 20);

-- Model routing configuration in system_state (REQ-ROUTER-01-3)
ALTER TABLE system_state ADD COLUMN IF NOT EXISTS model_routing JSONB NOT NULL DEFAULT '{
  "macro": {"model": "claude-opus-4-7", "haiku_eligible": false},
  "micro": {"model": "claude-haiku-4-5", "haiku_eligible": true, "haiku_enabled": true},
  "decision": {"model": "claude-sonnet-4-6", "haiku_eligible": false},
  "risk": {"model": "claude-sonnet-4-6", "haiku_eligible": false},
  "portfolio": {"model": "claude-sonnet-4-6", "haiku_eligible": false},
  "retrospective": {"model": "claude-sonnet-4-6", "haiku_eligible": false},
  "daily_report": {"model": "claude-haiku-4-5", "haiku_eligible": true, "haiku_enabled": true},
  "macro_news": {"model": "claude-haiku-4-5", "haiku_eligible": true, "haiku_enabled": true}
}'::jsonb;

-- Feature flags (REQ-MIGR-05-7)
ALTER TABLE system_state ADD COLUMN IF NOT EXISTS semantic_retrieval_enabled BOOLEAN NOT NULL DEFAULT false;
ALTER TABLE system_state ADD COLUMN IF NOT EXISTS shadow_test_active BOOLEAN NOT NULL DEFAULT false;

-- Shadow test results table (REQ-MIGR-05-2)
CREATE TABLE IF NOT EXISTS shadow_test_results (
    id BIGSERIAL PRIMARY KEY,
    persona TEXT NOT NULL,
    primary_model TEXT NOT NULL,
    shadow_model TEXT NOT NULL,
    primary_run_id BIGINT REFERENCES persona_runs(id),
    shadow_run_id BIGINT REFERENCES persona_runs(id),
    overlap_score REAL,
    quality_assessment JSONB,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_shadow_test_results_persona
    ON shadow_test_results(persona, created_at DESC);

-- Migration bookkeeping
INSERT INTO schema_migrations (version) VALUES ('011_pgvector_model_routing') ON CONFLICT DO NOTHING;
INSERT INTO audit_log (event_type, actor, details)
VALUES ('SCHEMA_MIGRATED', 'init', '{"migration":"011_pgvector_model_routing"}'::JSONB);

COMMENT ON TABLE context_embeddings IS
    'SPEC-010 REQ-PGVEC-02-2: Semantic chunks with vector embeddings for context retrieval.';
COMMENT ON TABLE shadow_test_results IS
    'SPEC-010 REQ-MIGR-05-2: Dual-model comparison results for quality gate.';
COMMENT ON COLUMN system_state.model_routing IS
    'SPEC-010 REQ-ROUTER-01-3: Per-persona model routing configuration.';
COMMENT ON COLUMN system_state.semantic_retrieval_enabled IS
    'SPEC-010 REQ-MIGR-05-7: Feature flag for semantic context retrieval.';
COMMENT ON COLUMN system_state.shadow_test_active IS
    'SPEC-010 REQ-MIGR-05-7: Feature flag for dual-model shadow testing.';

-- DOWN (rollback) ----------------------------------------------------------
-- To reverse this migration, execute:
--
-- DROP TABLE IF EXISTS shadow_test_results;
-- DROP TABLE IF EXISTS context_embeddings;
-- ALTER TABLE system_state DROP COLUMN IF EXISTS model_routing;
-- ALTER TABLE system_state DROP COLUMN IF EXISTS semantic_retrieval_enabled;
-- ALTER TABLE system_state DROP COLUMN IF EXISTS shadow_test_active;
-- DROP EXTENSION IF EXISTS vector;
-- DELETE FROM schema_migrations WHERE version = '011_pgvector_model_routing';
