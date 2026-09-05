-- runtime_version 에 last_seen 을 붙여 같은 버전의 재등장을 잃지 않게 한다.
--
-- 043 은 (code_hash, prompt_hash) 유니크 + first_seen 하나뿐이라, 롤백하거나
-- 이전 버전을 재배포하면 두 번째 구간이 표에서 사라진다. lead(first_seen) 으로
-- 구간을 만들면 그 이후 성과가 전부 다음 버전 것으로 조용히 귀속된다 —
-- 이 장치가 막으려던 오진단이 더 조용한 형태로 재발한다.
--
-- last_seen 이 있으면 구간이 [first_seen, last_seen] 으로 잡히고, 구간이 겹치면
-- 겹침이 눈에 보인다. 정확한 귀속이 필요하면 시간 조인이 아니라 행에 도장을
-- 찍어야 한다(041 이 persona_runs.prompt_hash 로 한 방식) — 시간 조인은 쓰기
-- 지점을 한 곳에 모으는 대신 정확도를 내준 선택이고, 그 대가가 이것이다.
--
-- Idempotent: schema_migrations 가드. 적용: docker exec trading-app trading migrate

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM schema_migrations WHERE version = '044_runtime_version_last_seen'
    ) THEN
        ALTER TABLE runtime_version
            ADD COLUMN IF NOT EXISTS last_seen TIMESTAMPTZ;

        -- 기존 행은 관측 시각이 하나뿐이므로 first_seen 으로 채운다.
        UPDATE runtime_version SET last_seen = first_seen WHERE last_seen IS NULL;

        INSERT INTO schema_migrations (version)
        VALUES ('044_runtime_version_last_seen')
        ON CONFLICT DO NOTHING;

        INSERT INTO audit_log (event_type, actor, details)
        VALUES ('SCHEMA_MIGRATED', 'init',
                '{"migration":"044_runtime_version_last_seen"}'::JSONB);
    END IF;
END $$;
