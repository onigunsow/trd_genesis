-- SPEC-TRADING-036 REQ-036-3: 후기 사이클 천장 방어.
--   late_cycle_events: 모든 트리거/해제 이벤트 로그.
--   system_state: 방어 활성 플래그 + 단계 + 진입 시각(24h 쿨다운용).
-- bull_mode 는 저장하지 않는다(REQ-036-2 g: 읽기 시점 파생).
-- 멱등: information_schema 가드. 재실행 안전 (023/024 하우스 스타일).

DO $$
BEGIN
    -- late_cycle_events 테이블
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.tables
        WHERE table_name = 'late_cycle_events'
    ) THEN
        CREATE TABLE late_cycle_events (
            id          BIGSERIAL PRIMARY KEY,
            event_type  TEXT NOT NULL
                        CHECK (event_type IN ('trigger','clear')),
            signal_name TEXT NOT NULL,        -- 'margin'|'deposits'|'vkospi'|'kospi_daily'
            value       NUMERIC,              -- 관측값 (unavailable 시 NULL)
            unit        TEXT,                 -- '조원'|''|'%' 등
            level       TEXT
                        CHECK (level IS NULL OR level IN
                               ('moderate','severe','top','immediate','flash')),
            created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
        );
    END IF;

    -- system_state.late_cycle_defense_active
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_name = 'system_state' AND column_name = 'late_cycle_defense_active'
    ) THEN
        ALTER TABLE system_state
            ADD COLUMN late_cycle_defense_active BOOLEAN NOT NULL DEFAULT false;
    END IF;

    -- system_state.late_cycle_level
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_name = 'system_state' AND column_name = 'late_cycle_level'
    ) THEN
        ALTER TABLE system_state
            ADD COLUMN late_cycle_level TEXT
                CHECK (late_cycle_level IS NULL OR late_cycle_level IN
                       ('moderate','severe','top','immediate','flash'));
    END IF;

    -- system_state.late_cycle_entered_at (24h 쿨다운 기준)
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_name = 'system_state' AND column_name = 'late_cycle_entered_at'
    ) THEN
        ALTER TABLE system_state
            ADD COLUMN late_cycle_entered_at TIMESTAMPTZ;
    END IF;
END $$;

COMMENT ON TABLE late_cycle_events IS
    'SPEC-TRADING-036 REQ-036-3(g): 후기 사이클 방어 트리거/해제 이벤트 로그.';
COMMENT ON COLUMN system_state.late_cycle_defense_active IS
    'SPEC-TRADING-036 REQ-036-3: true 면 방어 활성 → 불장 모드 자동 OFF(S-3).';
COMMENT ON COLUMN system_state.late_cycle_entered_at IS
    'SPEC-TRADING-036 REQ-036-3(f): 방어 진입 시각. 해소 후 24h 쿨다운 기준.';

INSERT INTO schema_migrations (version) VALUES ('025_late_cycle_defense')
    ON CONFLICT DO NOTHING;

INSERT INTO audit_log (event_type, actor, details)
VALUES ('SCHEMA_MIGRATED', 'init', '{"migration":"025_late_cycle_defense"}'::JSONB);
