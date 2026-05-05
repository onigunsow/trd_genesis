-- SPEC-TRADING-013: News crawling infrastructure
-- Creates news_articles storage and news_source_health tracking tables.

-- ─── news_articles ───────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS news_articles (
    id BIGSERIAL PRIMARY KEY,
    title TEXT NOT NULL,
    url TEXT NOT NULL,
    summary TEXT,
    source_name VARCHAR(100) NOT NULL,
    sector VARCHAR(50) NOT NULL,
    language VARCHAR(5) NOT NULL,
    published_at TIMESTAMPTZ NOT NULL,
    crawled_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    content_hash VARCHAR(64) NOT NULL,
    date_inferred BOOLEAN NOT NULL DEFAULT FALSE,
    CONSTRAINT uq_content_hash UNIQUE (content_hash)
);

CREATE INDEX IF NOT EXISTS idx_news_sector_published
    ON news_articles (sector, published_at DESC);

CREATE INDEX IF NOT EXISTS idx_news_language
    ON news_articles (language);

CREATE INDEX IF NOT EXISTS idx_news_crawled
    ON news_articles (crawled_at DESC);

-- ─── news_source_health ──────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS news_source_health (
    source_name VARCHAR(100) PRIMARY KEY,
    enabled BOOLEAN NOT NULL DEFAULT TRUE,
    consecutive_failures INTEGER NOT NULL DEFAULT 0,
    last_success TIMESTAMPTZ,
    last_failure TIMESTAMPTZ,
    last_error TEXT,
    total_fetches INTEGER NOT NULL DEFAULT 0,
    total_failures INTEGER NOT NULL DEFAULT 0
);

-- ─── Feature flag in system_state ────────────────────────────────────────────
-- Add news_crawling_v2_enabled column if not exists
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_name = 'system_state'
        AND column_name = 'news_crawling_v2_enabled'
    ) THEN
        ALTER TABLE system_state
            ADD COLUMN news_crawling_v2_enabled BOOLEAN NOT NULL DEFAULT TRUE;
    END IF;
END $$;
