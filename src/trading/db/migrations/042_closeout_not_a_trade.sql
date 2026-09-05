-- 8/7 모의계좌 전환 합성청산 4건을 실거래 집계에서 뺀다.
--
-- orders 182~185 는 request.origin='account_switch_closeout', response 에
-- "실제 체결이 아님" 이라고 적혀 있는데 correction=FALSE 라 build_roundtrips 를
-- 그대로 통과했다. 결과로 왕복 6건과 +36,600원이 성과에 가산되고 있었다.
-- 제외 시 60일 승률 38.3% -> 29.3%, 손익 -128,100 -> -164,700원.
--
-- correction=TRUE 의 의미는 roundtrips.build_roundtrips 가 정의한다:
--   FIFO lot 은 pop 하되 RoundTrip 미생성 / unmatched_sells 미기록 / 실현손익 미발생.
-- 원장 정리 전용 플래그이며, 이 4건이 정확히 그 성격이다.
--
-- fill_price/fill_qty 등 체결 사실은 건드리지 않는다(SPEC-042 D1: filled 행 불변).
-- 바꾸는 것은 분류 플래그 하나뿐이다.
--
-- 생성 코드 경로는 존재하지 않는다(origin 이 이 값 외에 0건) — 일회성 수동 삽입이었다.
-- 다음 계좌 전환 때도 같은 함정이 있으니, 합성청산을 넣는 쪽이 correction=TRUE 를
-- 함께 세팅해야 한다.
--
-- Idempotent: schema_migrations 가드 + origin 조건. 적용: docker exec trading-app trading migrate

DO $$
DECLARE
    touched INTEGER;
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM schema_migrations WHERE version = '042_closeout_not_a_trade'
    ) THEN
        UPDATE orders
           SET correction = TRUE
         WHERE request->>'origin' = 'account_switch_closeout'
           AND COALESCE(correction, FALSE) = FALSE;
        GET DIAGNOSTICS touched = ROW_COUNT;

        INSERT INTO schema_migrations (version)
        VALUES ('042_closeout_not_a_trade')
        ON CONFLICT DO NOTHING;

        INSERT INTO audit_log (event_type, actor, details)
        VALUES (
            'CLOSEOUT_MARKED_CORRECTION', 'init',
            jsonb_build_object(
                'migration', '042_closeout_not_a_trade',
                'rows', touched,
                'origin', 'account_switch_closeout'
            )
        );
    END IF;
END $$;
