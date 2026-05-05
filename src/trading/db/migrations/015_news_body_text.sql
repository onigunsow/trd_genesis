-- SPEC-TRADING-013: Add body_text column for full article content extraction.
-- Allows personas to analyze full article content rather than headlines only.

ALTER TABLE news_articles ADD COLUMN IF NOT EXISTS body_text TEXT;
