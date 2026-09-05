-- 실행 중인 코드·프롬프트 버전을 시간축에 기록한다.
--
-- 배경(2026-09-05): 60일 성과 창이 서로 다른 코드 체제를 한 덩어리로 섞는 바람에
-- 하루에 세 번, 이미 고쳐진 결함을 현재 결함으로 진단했다.
--   - 장전 매도 3/3 폐기  -> 8/29 910435c 에서 이미 수정됨
--   - rotate 가 청산 다수인데 손익 0 -> 8/17 이후 0건, 이미 죽은 규칙
--   - 원장 누적 음수 -> parity=True, 고칠 것 없음
-- 사람이 배포 이력을 대조해서 겨우 걸러냈다. 주간 자동 에이전트에는 그 제동이 없다.
--
-- 왜 컬럼이 아니라 별도 테이블인가: 성과는 orders 에서, 결정은 persona_decisions 에서,
-- 청산은 audit_log 에서 나온다. 각 테이블에 버전 컬럼을 붙이면 쓰기 지점이 흩어진다.
-- 대신 "언제부터 어떤 버전이 돌았는가" 를 한 곳에 남기고, 어떤 시계열이든 ts 로
-- 구간 조인해 체제를 가른다.
--
-- code_hash   : src/trading/**/*.py 전체 md5 앞 12자 (bind-mount 라 이미지 커밋은
--               실제 실행 코드와 다를 수 있어 소스를 직접 해시한다)
-- prompt_hash : personas/prompts/*.jinja md5 앞 12자 (041 과 같은 값)
--
-- Idempotent: schema_migrations 가드. 적용: docker exec trading-app trading migrate

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM schema_migrations WHERE version = '043_runtime_version'
    ) THEN
        CREATE TABLE IF NOT EXISTS runtime_version (
            id          SERIAL PRIMARY KEY,
            first_seen  TIMESTAMPTZ NOT NULL DEFAULT now(),
            code_hash   TEXT,
            prompt_hash TEXT
        );

        -- 같은 (code, prompt) 조합은 한 행만 — 재시작마다 쌓이지 않게.
        CREATE UNIQUE INDEX IF NOT EXISTS idx_runtime_version_pair
            ON runtime_version (COALESCE(code_hash, ''), COALESCE(prompt_hash, ''));

        CREATE INDEX IF NOT EXISTS idx_runtime_version_seen
            ON runtime_version (first_seen);

        INSERT INTO schema_migrations (version)
        VALUES ('043_runtime_version')
        ON CONFLICT DO NOTHING;

        INSERT INTO audit_log (event_type, actor, details)
        VALUES ('SCHEMA_MIGRATED', 'init',
                '{"migration":"043_runtime_version"}'::JSONB);
    END IF;
END $$;
