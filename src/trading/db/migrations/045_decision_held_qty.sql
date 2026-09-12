-- 결정 시점의 보유 수량을 persona_decisions 에 남긴다.
--
-- 배경(2026-09-12): 진입 품질 반사실이 데이터 부재로 불가능했다. 매수 결정의
-- 91.4%가 분할 추가매수인데(스냅샷 존재일 기준 buy 723건 중 661건), 신규 진입과
-- 추가매수를 가르려면 "그 결정 시점에 이미 보유 중이었나" 가 필요하다.
-- 유일한 대체 소스인 position_eval_smapshot 은 2026-06-20 부터 59일치뿐이고,
-- 그 이전을 체결기록으로 재구성하면 2026-05 오류율이 66.9% 다. 최엄격 정의로
-- 걸러내면 신규 진입 표본이 43건·7종목·11거래일(유효 n=6)까지 줄어 판정 불가였다.
--
-- 앞으로의 결정에는 기록이 남는다. 과거는 복구 불가.
--
-- held_qty: 결정 기록 시점 positions.qty (KIS reconcile 진실원). 미보유는 0,
--           조회 실패는 NULL — "0주 보유" 와 "모름" 을 구분한다.
--
-- Idempotent: schema_migrations 가드. 적용: docker exec trading-app trading migrate

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM schema_migrations WHERE version = '045_decision_held_qty'
    ) THEN
        ALTER TABLE persona_decisions
            ADD COLUMN IF NOT EXISTS held_qty INTEGER;

        INSERT INTO schema_migrations (version)
        VALUES ('045_decision_held_qty')
        ON CONFLICT DO NOTHING;

        INSERT INTO audit_log (event_type, actor, details)
        VALUES ('SCHEMA_MIGRATED', 'init',
                '{"migration":"045_decision_held_qty"}'::JSONB);
    END IF;
END $$;
