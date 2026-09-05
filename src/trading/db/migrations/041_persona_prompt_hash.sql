-- 프롬프트 세트 버전을 persona_runs 에 못 박는다.
--
-- 배경: decision.jinja 를 고칠 때마다 성과 시계열이 조용히 다른 전략의 것과 섞였다.
--   2026-08-27 stat_cls 정정 → 08-28 수급표 주입 → 09-02 자기검열 수정 을 지금
--   같은 PF 로 재고 있다. Agentic Trading Lab 의 AgentVersion(불변 prompt_hash)
--   설계를 최소 형태로 가져온다 — 전략을 바꾼다 = 새 해시가 찍힌다.
--
-- prompt_hash: personas/prompts/*.jinja 전체의 md5 앞 12자. NULL = 도입 이전 행.
--
-- Idempotent: schema_migrations 가드. 039 패턴 미러.
-- 적용: docker exec trading-app trading migrate

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM schema_migrations WHERE version = '041_persona_prompt_hash'
    ) THEN
        ALTER TABLE persona_runs
            ADD COLUMN IF NOT EXISTS prompt_hash TEXT;

        CREATE INDEX IF NOT EXISTS idx_persona_runs_prompt_hash
            ON persona_runs (prompt_hash);

        INSERT INTO schema_migrations (version)
        VALUES ('041_persona_prompt_hash')
        ON CONFLICT DO NOTHING;

        INSERT INTO audit_log (event_type, actor, details)
        VALUES (
            'SCHEMA_MIGRATED', 'init',
            '{"migration":"041_persona_prompt_hash"}'::JSONB
        );
    END IF;
END $$;
