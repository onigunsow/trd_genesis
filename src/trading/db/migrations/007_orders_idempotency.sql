-- M5+ 보강 — orders 멱등성 (REQ-KIS-02-13).
-- kis_order_no는 KIS가 발급하는 KRX 주문번호 — 같은 번호로 두 번 INSERT 차단.
-- NULL은 PostgreSQL UNIQUE에서 제외되므로 거부된 주문(kis_order_no IS NULL)은 영향 없음.

-- 빈 문자열을 NULL로 정규화 (기존 데이터 정리)
UPDATE orders SET kis_order_no = NULL WHERE kis_order_no = '';

-- 부분 UNIQUE 인덱스: kis_order_no가 NOT NULL인 경우에만 unique
CREATE UNIQUE INDEX IF NOT EXISTS orders_kis_order_no_unique_idx
    ON orders (kis_order_no)
    WHERE kis_order_no IS NOT NULL;

INSERT INTO schema_migrations (version) VALUES ('007_orders_idempotency') ON CONFLICT DO NOTHING;
INSERT INTO audit_log (event_type, actor, details)
VALUES ('SCHEMA_MIGRATED', 'init', '{"migration":"007_orders_idempotency"}'::JSONB);

COMMENT ON INDEX orders_kis_order_no_unique_idx IS
    'REQ-KIS-02-13 멱등성: 같은 KIS 주문번호로 두 번 INSERT 차단. NULL(거부된 주문)은 다수 허용.';
