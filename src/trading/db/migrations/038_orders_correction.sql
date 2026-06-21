-- SPEC-TRADING-042 D1/D6: 교정 SELL lot 를 구분하는 correction 플래그.
--
-- 배경 (D1 유령 합성매수 드리프트):
--   `_synthetic_fill` 이 paper 모드에서 KIS 미확인 매수를 `status='filled'`
--   로 영속화함으로써 `orders` 순매수 집계(buy - sell)가 실제 KIS 잔고보다
--   부풀려진다(086790 13 filled vs 10 held 등). edge/roundtrips FIFO 는 이
--   유령 lot 을 정상 lot 으로 취급해 FIFO 원가 순서를 오염시킨다(D6).
--
-- 해결 (append-only):
--   과거 `status='filled'` 행을 절대 UPDATE/DELETE 하지 않는다 —
--   edge/scorecard 파생물 보호([HARD] SPEC-TRADING-042 데이터 정리 안전).
--   초과분을 닫기 위한 교정 SELL 행을 `correction=TRUE` 로 INSERT 한다.
--   `edge/roundtrips.build_roundtrips` 는 `correction=TRUE` 매도를
--   FIFO lot 을 pop 하되 RoundTrip 을 미생성하는 "원장 정리" 매도로 처리한다.
--
-- Idempotent: schema_migrations 가드. 안전하게 재실행 가능.
-- 적용:
--   docker exec trading-app trading migrate
--   (또는 `trading migrate` CLI)

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM schema_migrations WHERE version = '038_orders_correction'
    ) THEN
        ALTER TABLE orders ADD COLUMN correction BOOLEAN NOT NULL DEFAULT FALSE;

        INSERT INTO schema_migrations (version)
        VALUES ('038_orders_correction')
        ON CONFLICT DO NOTHING;

        INSERT INTO audit_log (event_type, actor, details)
        VALUES (
            'SCHEMA_MIGRATED', 'init',
            '{"migration":"038_orders_correction"}'::JSONB
        );
    END IF;
END $$;
