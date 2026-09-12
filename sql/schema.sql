-- Runs against the aqi_db database (see postgres-init/01-init.sh)

CREATE SCHEMA IF NOT EXISTS staging;
CREATE SCHEMA IF NOT EXISTS clean;
CREATE SCHEMA IF NOT EXISTS mart;

CREATE TABLE IF NOT EXISTS staging.ingestion_log (
    log_id SERIAL PRIMARY KEY,
    source TEXT NOT NULL,
    extraction_date TIMESTAMPTZ NOT NULL DEFAULT now(),
    status TEXT NOT NULL,
    row_count INTEGER,
    error_message TEXT
);

CREATE TABLE IF NOT EXISTS staging.air_quality_observations (
    staging_id BIGSERIAL PRIMARY KEY,
    source TEXT NOT NULL,
    station_id TEXT NOT NULL,
    station_name TEXT,
    city TEXT,
    latitude DOUBLE PRECISION,
    longitude DOUBLE PRECISION,
    pollutant TEXT NOT NULL,
    value DOUBLE PRECISION,
    unit TEXT,
    observed_at TIMESTAMPTZ NOT NULL,
    ingested_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS staging.weather_observations (
    staging_id BIGSERIAL PRIMARY KEY,
    source TEXT NOT NULL,
    station_id TEXT NOT NULL,
    latitude DOUBLE PRECISION,
    longitude DOUBLE PRECISION,
    temperature_c DOUBLE PRECISION,
    humidity_pct DOUBLE PRECISION,
    wind_speed_ms DOUBLE PRECISION,
    precipitation_mm DOUBLE PRECISION,
    observed_at TIMESTAMPTZ NOT NULL,
    ingested_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS clean.rejected_records (
    rejected_id BIGSERIAL PRIMARY KEY,
    source_table TEXT NOT NULL,
    raw_payload JSONB,
    reason TEXT NOT NULL,
    rejected_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS clean.air_quality (
    id BIGSERIAL PRIMARY KEY,
    station_id TEXT NOT NULL,
    station_name TEXT,
    city TEXT,
    latitude DOUBLE PRECISION,
    longitude DOUBLE PRECISION,
    pollutant TEXT NOT NULL,
    value_std DOUBLE PRECISION NOT NULL,
    unit_std TEXT NOT NULL,
    observed_at TIMESTAMPTZ NOT NULL,
    temperature_c DOUBLE PRECISION,
    humidity_pct DOUBLE PRECISION,
    wind_speed_ms DOUBLE PRECISION,
    precipitation_mm DOUBLE PRECISION,
    UNIQUE (station_id, pollutant, observed_at)
);

CREATE TABLE IF NOT EXISTS mart.station_daily_pollutant (
    station_id TEXT NOT NULL,
    city TEXT,
    pollutant TEXT NOT NULL,
    obs_date DATE NOT NULL,
    avg_value DOUBLE PRECISION,
    min_value DOUBLE PRECISION,
    max_value DOUBLE PRECISION,
    observation_count INTEGER,
    PRIMARY KEY (station_id, pollutant, obs_date)
);

CREATE TABLE IF NOT EXISTS clean.missing_periods (
    id BIGSERIAL PRIMARY KEY,
    station_id TEXT NOT NULL,
    pollutant TEXT NOT NULL,
    gap_start TIMESTAMPTZ NOT NULL,
    gap_end TIMESTAMPTZ NOT NULL,
    gap_hours DOUBLE PRECISION NOT NULL,
    detected_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (station_id, pollutant, gap_start, gap_end)
);

CREATE TABLE IF NOT EXISTS mart.city_daily_aqi (
    city TEXT NOT NULL,
    obs_date DATE NOT NULL,
    avg_aqi DOUBLE PRECISION,
    aqi_category TEXT,
    dominant_pollutant TEXT,
    PRIMARY KEY (city, obs_date)
);
