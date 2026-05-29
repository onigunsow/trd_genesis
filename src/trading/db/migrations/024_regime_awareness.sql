-- SPEC-TRADING-035 REQ-035-1/REQ-035-2(f): regime awareness 캐싱 + 감사 컬럼.
--
-- Macro 페르소나가 이미 emit 하는 regime/risk_appetite (persona_runs.response_json 에만 존재)를
-- system_state(단일 행 id=1)로 승격하여 Decision/Risk/Portfolio 가 텍스트 파싱 없이 조회한다
-- (SPEC-016 Q-5: 신규 테이블 대신 단일 행 확장). persona_runs.regime_at_decision 은 의사결정
-- 시점의 regime 스냅샷(감사 추적, REQ-035-2(f)).
--
-- 멱등: information_schema.columns 가드. 재실행 안전 (023_halt_notify_cooldown.sql 하우스 스타일).

DO $$
BEGIN
    -- system_state.current_regime
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_name = 'system_state' AND column_name = 'current_regime'
    ) THEN
        ALTER TABLE system_state
            ADD COLUMN current_regime TEXT NOT NULL DEFAULT 'neutral'
                CHECK (current_regime IN ('bull','neutral','bear'));
    END IF;

    -- system_state.current_risk_appetite
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_name = 'system_state' AND column_name = 'current_risk_appetite'
    ) THEN
        ALTER TABLE system_state
            ADD COLUMN current_risk_appetite TEXT NOT NULL DEFAULT 'neutral'
                CHECK (current_risk_appetite IN ('risk-on','neutral','risk-off'));
    END IF;

    -- system_state.regime_updated_at
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_name = 'system_state' AND column_name = 'regime_updated_at'
    ) THEN
        ALTER TABLE system_state
            ADD COLUMN regime_updated_at TIMESTAMPTZ NOT NULL DEFAULT now();
    END IF;

    -- system_state.regime_source_run_id (FK -> persona_runs.id)
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_name = 'system_state' AND column_name = 'regime_source_run_id'
    ) THEN
        ALTER TABLE system_state
            ADD COLUMN regime_source_run_id BIGINT
                REFERENCES persona_runs(id);
    END IF;

    -- persona_runs.regime_at_decision (감사 스냅샷)
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_name = 'persona_runs' AND column_name = 'regime_at_decision'
    ) THEN
        ALTER TABLE persona_runs
            ADD COLUMN regime_at_decision TEXT
                CHECK (regime_at_decision IS NULL
                       OR regime_at_decision IN ('bull','neutral','bear'));
    END IF;
END $$;

COMMENT ON COLUMN system_state.current_regime IS
    'SPEC-TRADING-035 REQ-035-1: Macro 페르소나가 산출한 시장 체제 캐시. '
    'TTL 7일 초과 시 읽기 시점에 neutral 로 안전 폴백(저장값은 보존).';
COMMENT ON COLUMN system_state.regime_source_run_id IS
    'SPEC-TRADING-035 REQ-035-1(b): current_regime 을 갱신한 persona_runs.id.';
COMMENT ON COLUMN persona_runs.regime_at_decision IS
    'SPEC-TRADING-035 REQ-035-2(f): 해당 Decision/Risk 실행 시점의 current_regime 스냅샷(감사).';

INSERT INTO schema_migrations (version) VALUES ('024_regime_awareness')
    ON CONFLICT DO NOTHING;

INSERT INTO audit_log (event_type, actor, details)
VALUES ('SCHEMA_MIGRATED', 'init', '{"migration":"024_regime_awareness"}'::JSONB);
