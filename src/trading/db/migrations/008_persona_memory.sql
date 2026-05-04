-- SPEC-TRADING-007 P1 — Persona Memory Schema
-- Implements REQ-MEM-02-1, 02-2, 02-3, 02-4, REQ-MEM-05-2 (memory_proposals on retrospectives)

-- ── macro_memory ─────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS macro_memory (
    id              BIGSERIAL    PRIMARY KEY,
    scope           TEXT         NOT NULL CHECK (scope IN ('global','korea','usa','china','etc')),
    scope_id        TEXT,                                                -- optional fine-grained (예: 'fed', 'bok')
    kind            TEXT         NOT NULL CHECK (kind IN ('geopolitical','economic','policy','regime','event')),
    summary         TEXT         NOT NULL,
    importance      SMALLINT     NOT NULL DEFAULT 3 CHECK (importance BETWEEN 1 AND 5),
    source_refs     JSONB        NOT NULL,                               -- REQ-MEM-02-3 의무
    valid_from      DATE         NOT NULL DEFAULT CURRENT_DATE,
    valid_until     DATE,                                                -- NULL = 무기한 (importance ≥4 권장)
    status          TEXT         NOT NULL DEFAULT 'active'
                                  CHECK (status IN ('active','archived','superseded')),
    supersedes_id   BIGINT       REFERENCES macro_memory(id) ON DELETE SET NULL,
    created_at      TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    last_accessed_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    -- REQ-MEM-02-3: source_refs는 비어있으면 안 됨 (반드시 persona_run_id 포함)
    CONSTRAINT macro_memory_source_refs_not_empty CHECK (jsonb_typeof(source_refs) = 'object' AND source_refs ? 'persona_run_id')
);

CREATE INDEX IF NOT EXISTS macro_memory_active_idx
    ON macro_memory (status, importance DESC, updated_at DESC) WHERE status = 'active';
CREATE INDEX IF NOT EXISTS macro_memory_scope_idx ON macro_memory (scope, kind, status);
CREATE INDEX IF NOT EXISTS macro_memory_supersedes_idx ON macro_memory (supersedes_id) WHERE supersedes_id IS NOT NULL;

-- ── micro_memory ─────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS micro_memory (
    id              BIGSERIAL    PRIMARY KEY,
    scope           TEXT         NOT NULL CHECK (scope IN ('sector','ticker')),
    scope_id        TEXT         NOT NULL,                               -- '반도체' or '005930'
    kind            TEXT         NOT NULL CHECK (kind IN ('earnings','disclosure','thematic','flow_pattern','regulatory')),
    summary         TEXT         NOT NULL,
    importance      SMALLINT     NOT NULL DEFAULT 3 CHECK (importance BETWEEN 1 AND 5),
    source_refs     JSONB        NOT NULL,
    valid_from      DATE         NOT NULL DEFAULT CURRENT_DATE,
    valid_until     DATE,
    status          TEXT         NOT NULL DEFAULT 'active'
                                  CHECK (status IN ('active','archived','superseded')),
    supersedes_id   BIGINT       REFERENCES micro_memory(id) ON DELETE SET NULL,
    created_at      TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    last_accessed_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    CONSTRAINT micro_memory_source_refs_not_empty CHECK (jsonb_typeof(source_refs) = 'object' AND source_refs ? 'persona_run_id')
);

CREATE INDEX IF NOT EXISTS micro_memory_active_idx
    ON micro_memory (status, importance DESC, updated_at DESC) WHERE status = 'active';
CREATE INDEX IF NOT EXISTS micro_memory_scope_idx ON micro_memory (scope, scope_id, kind, status);
CREATE INDEX IF NOT EXISTS micro_memory_supersedes_idx ON micro_memory (supersedes_id) WHERE supersedes_id IS NOT NULL;

-- ── retrospectives.memory_proposals (REQ-MEM-05-2) ───────────────────────────
ALTER TABLE retrospectives
    ADD COLUMN IF NOT EXISTS memory_proposals JSONB NOT NULL DEFAULT '[]'::JSONB;

-- ── auto-archive trigger (REQ-MEM-02-4) ──────────────────────────────────────
-- valid_until 도달 시 status='archived'를 INSERT/UPDATE 트리거가 처리하지 않고,
-- 매일 새벽 cron(별도 함수)로 sweep. 단순함 우선.
CREATE OR REPLACE FUNCTION archive_expired_memory()
RETURNS INTEGER AS $$
DECLARE
    total INTEGER := 0;
    delta INTEGER;
BEGIN
    -- valid_until 도달 → archive (양 테이블)
    UPDATE macro_memory SET status='archived', updated_at=NOW()
        WHERE status='active' AND valid_until IS NOT NULL AND valid_until < CURRENT_DATE;
    GET DIAGNOSTICS delta = ROW_COUNT; total := total + delta;

    UPDATE micro_memory SET status='archived', updated_at=NOW()
        WHERE status='active' AND valid_until IS NOT NULL AND valid_until < CURRENT_DATE;
    GET DIAGNOSTICS delta = ROW_COUNT; total := total + delta;

    -- last_accessed 30일 + importance < 4 → archive
    UPDATE macro_memory SET status='archived', updated_at=NOW()
        WHERE status='active' AND importance < 4
          AND last_accessed_at < NOW() - INTERVAL '30 days';
    GET DIAGNOSTICS delta = ROW_COUNT; total := total + delta;

    UPDATE micro_memory SET status='archived', updated_at=NOW()
        WHERE status='active' AND importance < 4
          AND last_accessed_at < NOW() - INTERVAL '30 days';
    GET DIAGNOSTICS delta = ROW_COUNT; total := total + delta;

    -- last_accessed 60일 + importance ≥ 4 → archive
    UPDATE macro_memory SET status='archived', updated_at=NOW()
        WHERE status='active' AND importance >= 4
          AND last_accessed_at < NOW() - INTERVAL '60 days';
    GET DIAGNOSTICS delta = ROW_COUNT; total := total + delta;

    UPDATE micro_memory SET status='archived', updated_at=NOW()
        WHERE status='active' AND importance >= 4
          AND last_accessed_at < NOW() - INTERVAL '60 days';
    GET DIAGNOSTICS delta = ROW_COUNT; total := total + delta;

    RETURN total;
END;
$$ LANGUAGE plpgsql;

INSERT INTO schema_migrations (version) VALUES ('008_persona_memory') ON CONFLICT DO NOTHING;
INSERT INTO audit_log (event_type, actor, details)
VALUES ('SCHEMA_MIGRATED', 'init', '{"migration":"008_persona_memory"}'::JSONB);

COMMENT ON TABLE macro_memory IS
    'SPEC-007 Macro persona dynamic memory. 페르소나가 자가 관리. source_refs.persona_run_id 의무.';
COMMENT ON TABLE micro_memory IS
    'SPEC-007 Micro persona dynamic memory. scope=sector|ticker, scope_id=섹터명 또는 종목코드.';
COMMENT ON FUNCTION archive_expired_memory IS
    'REQ-MEM-02-4 retention sweep. cron으로 매일 새벽 호출 권장.';
