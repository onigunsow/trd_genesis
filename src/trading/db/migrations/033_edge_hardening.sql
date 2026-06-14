-- SPEC-TRADING-048 마이그레이션 033: edge hardening 스키마.
--
-- 변경 사항:
--   1. persona_decisions 에 prob_bull/prob_base/prob_bear nullable 컬럼 추가.
--      (REQ-048-M3-4: 시나리오 확률 스키마-only, 프롬프트 변경은 후속 SPEC)
--   2. cool_down_events 테이블 신설 — COOL_DOWN 증거태그 누적 추적.
--      (REQ-048-M3-5: COOL_DOWN 리스크 상태)
--   3. system_state 에 cool_down 컬럼 추가.
--
-- 멱등 보장: IF NOT EXISTS / DO $$...END 패턴.
-- 롤백: 033_edge_hardening_rollback.sql 참조.

-- 1. persona_decisions: 시나리오 확률 컬럼 (nullable, 합 검증은 애플리케이션 레이어)
ALTER TABLE persona_decisions
    ADD COLUMN IF NOT EXISTS prob_bull  NUMERIC(6,4),
    ADD COLUMN IF NOT EXISTS prob_base  NUMERIC(6,4),
    ADD COLUMN IF NOT EXISTS prob_bear  NUMERIC(6,4);

-- 2. cool_down_events 테이블 (COOL_DOWN 증거태그 누적 + 해제 마커)
CREATE TABLE IF NOT EXISTS cool_down_events (
    id          SERIAL PRIMARY KEY,
    ts          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    event_type  TEXT        NOT NULL,  -- 'violation' | 'drawdown' | 'triggered' | 'cleared'
    reason      TEXT,
    details     JSONB,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- 3. system_state: COOL_DOWN 활성 여부 컬럼
ALTER TABLE system_state
    ADD COLUMN IF NOT EXISTS cool_down_active BOOLEAN NOT NULL DEFAULT FALSE;

-- 인덱스: 최근 이벤트 조회용
CREATE INDEX IF NOT EXISTS idx_cool_down_events_ts ON cool_down_events(ts DESC);
