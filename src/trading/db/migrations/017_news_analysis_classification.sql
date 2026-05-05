-- SPEC-TRADING-014: Add classification column and allow impact_score=0 for noise filtering.
-- classification: macro_market_moving, sector_specific, company_specific, noise
-- impact_score 0: articles with zero investment relevance (PR/CSR/HR/awards)

-- Add classification column
ALTER TABLE news_analysis
    ADD COLUMN IF NOT EXISTS classification VARCHAR(30) NOT NULL DEFAULT 'company_specific';

-- Drop old constraint and add new one allowing 0
ALTER TABLE news_analysis
    DROP CONSTRAINT IF EXISTS news_analysis_impact_score_check;
ALTER TABLE news_analysis
    ADD CONSTRAINT news_analysis_impact_score_check CHECK (impact_score BETWEEN 0 AND 5);

-- Index for classification-based queries
CREATE INDEX IF NOT EXISTS idx_news_analysis_classification
    ON news_analysis (classification, impact_score DESC);
