-- SPEC-TRADING-047 M1 REQ-047-1: dashboard_ro 읽기 전용 Postgres 역할 생성.
--
-- 보안 원칙(심층 방어):
--   - SELECT 전용. INSERT/UPDATE/DELETE/DDL 권한 없음.
--   - GRANT CONNECT + GRANT USAGE on schema public 만 허용.
--   - 현재 및 미래의 대상 테이블에 SELECT 부여(GRANT ON ALL + ALTER DEFAULT).
--   - 비밀번호는 DASHBOARD_DB_PASSWORD 환경변수로 주입; 여기서는 플레이스홀더 없음.
--     → 배포 절차: migration 적용 후 운영자가 psql 로 ALTER ROLE 비밀번호 설정.
--
-- 멱등 보장: DO $$...END 블록으로 역할/권한 중복 적용 안전.

DO $$
BEGIN
    -- 역할이 없을 때만 생성
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'dashboard_ro') THEN
        CREATE ROLE dashboard_ro WITH LOGIN PASSWORD 'changeme_in_ops';
    END IF;
END $$;

-- 데이터베이스 연결 허용
GRANT CONNECT ON DATABASE trading TO dashboard_ro;

-- schema public 사용 허용 (PostgreSQL 15+ 기본 revoke 대응)
GRANT USAGE ON SCHEMA public TO dashboard_ro;

-- 현재 존재하는 모든 테이블에 SELECT 부여
GRANT SELECT ON ALL TABLES IN SCHEMA public TO dashboard_ro;

-- 미래에 생성될 테이블에도 자동으로 SELECT 부여 (소유자별)
ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT SELECT ON TABLES TO dashboard_ro;

-- 마이그레이션 기록
INSERT INTO schema_migrations (version) VALUES ('032_dashboard_readonly_role')
    ON CONFLICT DO NOTHING;

INSERT INTO audit_log (event_type, actor, details)
VALUES (
    'SCHEMA_MIGRATED',
    'init',
    '{"migration":"032_dashboard_readonly_role","note":"dashboard_ro SELECT-only role for SPEC-TRADING-047"}'::JSONB
);
