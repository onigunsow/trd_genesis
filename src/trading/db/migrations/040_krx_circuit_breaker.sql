-- mig 040: KRX 서킷 브레이커 상태 컬럼 추가
--
-- system_state 싱글톤에 KRX pykrx 서킷 브레이커 상태를 영속화한다.
-- 스케줄러 재시작·다중 프로세스 간 서킷 상태를 공유하기 위해 DB에 저장.
--
-- 새 컬럼:
--   krx_circuit_state            — 'CLOSED' | 'OPEN' | 'HALF_OPEN' (기본 CLOSED)
--   krx_circuit_open_until       — OPEN 상태 해제 예정 시각 (nullable)
--   krx_circuit_cooldown_level   — 지수 백오프 단계 (0=15m, 1=1h, 2=6h, 3+=24h)
--   krx_circuit_consecutive_failures — 현재 연속 실패 카운터
--
-- 적용: docker exec trading-app trading migrate
--       (또는 psql $DATABASE_URL < 040_krx_circuit_breaker.sql)
-- 롤백: 아래 주석된 DROP 문 실행

ALTER TABLE system_state
    ADD COLUMN IF NOT EXISTS krx_circuit_state TEXT NOT NULL DEFAULT 'CLOSED',
    ADD COLUMN IF NOT EXISTS krx_circuit_open_until TIMESTAMPTZ,
    ADD COLUMN IF NOT EXISTS krx_circuit_cooldown_level INTEGER NOT NULL DEFAULT 0,
    ADD COLUMN IF NOT EXISTS krx_circuit_consecutive_failures INTEGER NOT NULL DEFAULT 0;

-- 롤백 (필요 시):
-- ALTER TABLE system_state
--     DROP COLUMN IF EXISTS krx_circuit_state,
--     DROP COLUMN IF EXISTS krx_circuit_open_until,
--     DROP COLUMN IF EXISTS krx_circuit_cooldown_level,
--     DROP COLUMN IF EXISTS krx_circuit_consecutive_failures;
