-- Part 2: ML schema (predictions, API request log, monitoring metrics)
CREATE SCHEMA IF NOT EXISTS ml;

CREATE TABLE IF NOT EXISTS ml.predictions (
    id                 BIGSERIAL PRIMARY KEY,
    city               VARCHAR(100) NOT NULL,
    as_of              DATE NOT NULL,              -- last observed day used for features
    forecast_date      DATE NOT NULL,              -- day being predicted (as_of + 1)
    predicted_aqi      DOUBLE PRECISION,
    predicted_category VARCHAR(50),
    actual_aqi         INTEGER,                    -- backfilled once the day is in gold
    actual_category    VARCHAR(50),
    abs_error          DOUBLE PRECISION,
    model_version      VARCHAR(20) NOT NULL,
    source             VARCHAR(10) NOT NULL DEFAULT 'live',   -- live | backtest
    predicted_at       TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (city, forecast_date, model_version, source)
);
CREATE INDEX IF NOT EXISTS idx_ml_pred_city_date ON ml.predictions(city, forecast_date);

CREATE TABLE IF NOT EXISTS ml.api_requests (
    id          BIGSERIAL PRIMARY KEY,
    ts          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    endpoint    VARCHAR(50),
    city        VARCHAR(100),
    status_code INTEGER,
    latency_ms  DOUBLE PRECISION,
    error       TEXT
);
CREATE INDEX IF NOT EXISTS idx_ml_api_ts ON ml.api_requests(ts);

CREATE TABLE IF NOT EXISTS ml.monitoring_metrics (
    id        BIGSERIAL PRIMARY KEY,
    run_date  DATE NOT NULL,
    metric    VARCHAR(50) NOT NULL,   -- live_mae_14d | psi | ks_pvalue | missing_pct | latency_p95_ms | error_rate
    scope     VARCHAR(100) NOT NULL,  -- city, feature name, or 'all'
    value     DOUBLE PRECISION,
    threshold DOUBLE PRECISION,
    breached  BOOLEAN,
    UNIQUE (run_date, metric, scope)
);
