-- SPEC-TRADING-055 M1: resolver 하트비트 + 이상 알림 throttle 컬럼 추가.
--
-- system_state 에 두 TIMESTAMPTZ 컬럼을 추가한다.
--   last_resolver_run          — resolver cron 이 정상 발화한 마지막 시각(UTC).
--   resolver_anomaly_notified_at — 이상 알림 throttle 기준 시각(UTC).
--
-- Idempotent: schema_migrations 가드.  038_orders_correction 패턴 미러.
-- 적용:
--   docker exec trading-app trading migrate
--   (또는 `trading migrate` CLI)
--
-- 배포 순서 권고(D7): migrate 먼저 → redeploy.
--   migrate 전 redeploy 시 _wrap 가 write 예외를 삼켜 무음 no-op 가 되므로
--   resolver_fresh=False 오탐 방지를 위해 migrate 우선 권장.

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM schema_migrations WHERE version = '039_resolver_heartbeat'
    ) THEN
        ALTER TABLE system_state
            ADD COLUMN IF NOT EXISTS last_resolver_run TIMESTAMPTZ;

        ALTER TABLE system_state
            ADD COLUMN IF NOT EXISTS resolver_anomaly_notified_at TIMESTAMPTZ;

        INSERT INTO schema_migrations (version)
        VALUES ('039_resolver_heartbeat')
        ON CONFLICT DO NOTHING;

        INSERT INTO audit_log (event_type, actor, details)
        VALUES (
            'SCHEMA_MIGRATED', 'init',
            '{"migration":"039_resolver_heartbeat"}'::JSONB
        );
    END IF;
END $$;
