-- SPEC-TRADING-054 (follow-up): ticker_metadata.name 컬럼 추가
-- KRX 독립 KIS name resolver 가 채우는 한국어 종목명 캐시 컬럼.
-- 멱등성 보장 (IF NOT EXISTS 활용).

ALTER TABLE ticker_metadata
    ADD COLUMN IF NOT EXISTS name TEXT NOT NULL DEFAULT '';

-- schema_migrations 버전 등록
INSERT INTO schema_migrations (version)
VALUES ('037_ticker_metadata_name')
ON CONFLICT (version) DO NOTHING;
