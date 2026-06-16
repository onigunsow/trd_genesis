-- SPEC-TRADING-052: CLI 인증 끊김 비용 누수 방지 — degraded 감지·조기경고·비용0 강제(옵트인)
--
-- 2026-06-16 사고: 호스트 claude CLI 인증 만료 → exit=0 빈출력 일관 실패
-- → 설계된 폴백/직접 API 경로가 유료 Anthropic API로 자동 우회
-- → 크레딧 177건 소진(08:15~16:00). 감지·경고·비용차단 3중 공백 해소.
--
-- ADR-002 (OQ-2 RESOLVED): degraded-latch와 throttle 클럭을 분리 컬럼으로 추가.
-- - cli_degraded: latch 상태(REQ-052-A5 단조 latch/clear)
-- - cli_degraded_since: healthy→degraded 전이 시각(관측·집계용)
-- - cli_consecutive_failures: 영속 연속 실패 횟수(in-process _cli_failure_count와 독립, REQ-052-A5)
-- - cli_degraded_notified_at: 별도 throttle 클럭(SPEC-031 halt_notified_at 동형)
-- - strict_cost_zero_mode: ADR-001 옵트인 플래그(기본 OFF)
--
-- Idempotent: information_schema 가드(mig 023 house style). 재실행 안전.

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_name = 'system_state' AND column_name = 'cli_degraded'
    ) THEN
        ALTER TABLE system_state ADD COLUMN cli_degraded BOOLEAN DEFAULT FALSE;
    END IF;
END $$;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_name = 'system_state' AND column_name = 'cli_degraded_since'
    ) THEN
        ALTER TABLE system_state ADD COLUMN cli_degraded_since TIMESTAMPTZ;
    END IF;
END $$;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_name = 'system_state' AND column_name = 'cli_consecutive_failures'
    ) THEN
        ALTER TABLE system_state ADD COLUMN cli_consecutive_failures INTEGER DEFAULT 0;
    END IF;
END $$;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_name = 'system_state' AND column_name = 'cli_degraded_notified_at'
    ) THEN
        ALTER TABLE system_state ADD COLUMN cli_degraded_notified_at TIMESTAMPTZ;
    END IF;
END $$;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_name = 'system_state' AND column_name = 'strict_cost_zero_mode'
    ) THEN
        ALTER TABLE system_state ADD COLUMN strict_cost_zero_mode BOOLEAN DEFAULT FALSE;
    END IF;
END $$;

COMMENT ON COLUMN system_state.cli_degraded IS
    'SPEC-TRADING-052 REQ-052-A1/A5: CLI degraded latch. True = 연속 실패 임계 도달.'
    ' 오직 CLI 성공/하트비트 신선(REQ-052-A2)에서만 False로 해제.'
    ' in-process _cli_failure_count와 독립(ADR-005) — flap 방지.';

COMMENT ON COLUMN system_state.cli_degraded_since IS
    'SPEC-TRADING-052 REQ-052-A1: healthy→degraded 전이 시각(관측·집계용).';

COMMENT ON COLUMN system_state.cli_consecutive_failures IS
    'SPEC-TRADING-052 REQ-052-A1/A5: 영속 연속 실패 횟수.'
    ' in-process _cli_failure_count와 독립 — 다중 워커/재시작 간 공유용.';

COMMENT ON COLUMN system_state.cli_degraded_notified_at IS
    'SPEC-TRADING-052 REQ-052-B2/ADR-003: 조기경고 throttle 클럭.'
    ' SPEC-031 halt_notified_at 동형. NULL = 이 에피소드 첫 발동 즉시 알림 대상.'
    ' degraded 해제(REQ-052-A2) 시 NULL로 리셋.';

COMMENT ON COLUMN system_state.strict_cost_zero_mode IS
    'SPEC-TRADING-052 REQ-052-C1/ADR-001: 비용0 강제 옵트인 플래그.'
    ' 기본 OFF — SPEC-016 폴백 허용된 예외 보존.'
    ' ON이면 유료 폴백/직접호출 차단·defer.';

INSERT INTO schema_migrations (version) VALUES ('034_cli_degraded_guard')
    ON CONFLICT DO NOTHING;

INSERT INTO audit_log (event_type, actor, details)
VALUES ('SCHEMA_MIGRATED', 'init', '{"migration":"034_cli_degraded_guard"}'::JSONB);
