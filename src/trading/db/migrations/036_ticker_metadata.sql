-- SPEC-TRADING-054 M1.5 (ADR-002)
-- 종목→업종 마스터 테이블: 별도 로더 스크립트가 KRX/pykrx 에서 적재.
-- 대시보드는 읽기전용 접근. 매핑 없는 종목은 "미분류" 폴백(REQ-054-G1).

CREATE TABLE IF NOT EXISTS ticker_metadata (
    ticker      TEXT        NOT NULL PRIMARY KEY,  -- 종목코드
    sector      TEXT        NOT NULL DEFAULT '미분류', -- 업종 (예: '전기전자', '화학')
    industry    TEXT        NOT NULL DEFAULT '',    -- 세부 업종
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- dashboard_ro 역할 읽기 권한 부여
GRANT SELECT ON ticker_metadata TO dashboard_ro;
