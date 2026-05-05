-- Migration 013: Event-CAR Historical Database + ATR Cache + Feature Flags
-- SPEC-TRADING-012: Algorithm & Strategy Upgrade

-- Event-CAR Historical Database
CREATE TABLE IF NOT EXISTS event_car_history (
    id BIGSERIAL PRIMARY KEY,
    ticker TEXT NOT NULL,
    event_type TEXT NOT NULL,
    event_subtype TEXT,
    event_date DATE NOT NULL,
    event_magnitude REAL,
    car_1d REAL,
    car_5d REAL,
    car_10d REAL,
    benchmark_return_1d REAL,
    benchmark_return_5d REAL,
    benchmark_return_10d REAL,
    volume_ratio REAL,
    computed_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE(ticker, event_type, event_date)
);
CREATE INDEX IF NOT EXISTS idx_event_car_type ON event_car_history(event_type, event_subtype);
CREATE INDEX IF NOT EXISTS idx_event_car_ticker ON event_car_history(ticker, event_date DESC);
CREATE INDEX IF NOT EXISTS idx_event_car_date ON event_car_history(event_date DESC);

-- CAR Statistics Cache
CREATE TABLE IF NOT EXISTS event_car_stats (
    id BIGSERIAL PRIMARY KEY,
    event_type TEXT NOT NULL,
    event_subtype TEXT,
    sector TEXT,
    sample_count INTEGER NOT NULL,
    mean_car_1d REAL NOT NULL,
    mean_car_5d REAL NOT NULL,
    mean_car_10d REAL NOT NULL,
    std_car_1d REAL NOT NULL,
    std_car_5d REAL NOT NULL,
    std_car_10d REAL NOT NULL,
    median_abs_car_5d REAL NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE(event_type, event_subtype, sector)
);

-- Event Filter Log
CREATE TABLE IF NOT EXISTS event_filter_log (
    id BIGSERIAL PRIMARY KEY,
    ticker TEXT NOT NULL,
    event_type TEXT NOT NULL,
    event_subtype TEXT,
    event_magnitude REAL,
    predicted_car_5d REAL,
    confidence REAL NOT NULL,
    sample_count INTEGER NOT NULL,
    threshold REAL NOT NULL,
    decision TEXT NOT NULL,
    reason TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_event_filter_log_ts ON event_filter_log(created_at DESC);
CREATE INDEX IF NOT EXISTS idx_event_filter_log_ticker ON event_filter_log(ticker, created_at DESC);

-- ATR Cache
CREATE TABLE IF NOT EXISTS atr_cache (
    id BIGSERIAL PRIMARY KEY,
    ticker TEXT NOT NULL,
    date DATE NOT NULL,
    atr_14 REAL NOT NULL,
    atr_pct REAL NOT NULL,
    close_price REAL NOT NULL,
    volatility_regime TEXT NOT NULL,
    computed_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE(ticker, date)
);
CREATE INDEX IF NOT EXISTS idx_atr_cache_ticker_date ON atr_cache(ticker, date DESC);

-- Feature flags in system_state (idempotent)
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_name = 'system_state' AND column_name = 'car_filter_enabled'
    ) THEN
        ALTER TABLE system_state ADD COLUMN car_filter_enabled BOOLEAN NOT NULL DEFAULT false;
    END IF;
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_name = 'system_state' AND column_name = 'dynamic_thresholds_enabled'
    ) THEN
        ALTER TABLE system_state ADD COLUMN dynamic_thresholds_enabled BOOLEAN NOT NULL DEFAULT false;
    END IF;
END $$;
