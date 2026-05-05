-- SPEC-TRADING-014: News Intelligence Analysis Pipeline
-- Tables for article analysis results, story clusters, and trend data.

-- news_analysis: per-article LLM analysis results (Haiku 4.5)
CREATE TABLE IF NOT EXISTS news_analysis (
    id BIGSERIAL PRIMARY KEY,
    article_id BIGINT NOT NULL REFERENCES news_articles(id) ON DELETE CASCADE,
    summary_2line TEXT NOT NULL,
    impact_score SMALLINT NOT NULL CHECK (impact_score BETWEEN 1 AND 5),
    keywords TEXT[] NOT NULL DEFAULT '{}',
    sentiment VARCHAR(10) NOT NULL CHECK (sentiment IN ('positive', 'neutral', 'negative')),
    analyzed_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    model_used VARCHAR(50) NOT NULL DEFAULT 'claude-haiku-4-5',
    token_input INTEGER,
    token_output INTEGER,
    cost_krw REAL,
    UNIQUE (article_id)
);

CREATE INDEX IF NOT EXISTS idx_news_analysis_impact ON news_analysis (impact_score DESC);
CREATE INDEX IF NOT EXISTS idx_news_analysis_analyzed ON news_analysis (analyzed_at DESC);
CREATE INDEX IF NOT EXISTS idx_news_analysis_sentiment ON news_analysis (sentiment);

-- story_clusters: grouped articles covering same event
CREATE TABLE IF NOT EXISTS story_clusters (
    id BIGSERIAL PRIMARY KEY,
    representative_title TEXT NOT NULL,
    article_ids BIGINT[] NOT NULL,
    source_count INTEGER NOT NULL,
    impact_max SMALLINT NOT NULL,
    sector VARCHAR(50) NOT NULL,
    keywords TEXT[] NOT NULL DEFAULT '{}',
    sentiment_dominant VARCHAR(10) NOT NULL,
    first_published TIMESTAMPTZ NOT NULL,
    last_updated TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    cluster_date DATE NOT NULL DEFAULT CURRENT_DATE,
    portfolio_relevant BOOLEAN NOT NULL DEFAULT FALSE,
    relevance_tickers TEXT[] DEFAULT '{}'
);

CREATE INDEX IF NOT EXISTS idx_story_clusters_date ON story_clusters (cluster_date DESC);
CREATE INDEX IF NOT EXISTS idx_story_clusters_impact ON story_clusters (impact_max DESC);
CREATE INDEX IF NOT EXISTS idx_story_clusters_sector ON story_clusters (sector);

-- news_trends: daily/weekly keyword + sentiment aggregation
CREATE TABLE IF NOT EXISTS news_trends (
    id BIGSERIAL PRIMARY KEY,
    trend_date DATE NOT NULL,
    trend_type VARCHAR(10) NOT NULL CHECK (trend_type IN ('daily', 'weekly')),
    sector VARCHAR(50),
    keyword VARCHAR(100) NOT NULL,
    mention_count INTEGER NOT NULL DEFAULT 0,
    sentiment_positive INTEGER NOT NULL DEFAULT 0,
    sentiment_neutral INTEGER NOT NULL DEFAULT 0,
    sentiment_negative INTEGER NOT NULL DEFAULT 0,
    sentiment_avg REAL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (trend_date, trend_type, sector, keyword)
);

CREATE INDEX IF NOT EXISTS idx_news_trends_date_type ON news_trends (trend_date DESC, trend_type);
CREATE INDEX IF NOT EXISTS idx_news_trends_keyword ON news_trends (keyword, trend_date DESC);
