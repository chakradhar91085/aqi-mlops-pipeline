# AQI Pipeline — Data Dictionary

## Overview

This data dictionary documents all tables across the four pipeline layers:
**raw → staging → cleaned → gold**, plus the **meta** tracking schema.

---

## Schema: `meta` — Pipeline Tracking

### `meta.ingestion_log`

Records every data ingestion run with extraction metadata.

| Column | Type | Description | Example |
|---|---|---|---|
| `log_id` | SERIAL PK | Auto-incrementing record ID | 42 |
| `run_id` | UUID | Unique identifier for this pipeline run | `a1b2c3d4-...` |
| `source` | VARCHAR(100) | Source name and city | `openaq:Delhi` |
| `extraction_date` | DATE | Calendar date of data collected | `2026-09-01` |
| `started_at` | TIMESTAMPTZ | When extraction began | `2026-09-01 02:01:05+00` |
| `completed_at` | TIMESTAMPTZ | When extraction finished | `2026-09-01 02:03:22+00` |
| `status` | VARCHAR(20) | `success`, `partial`, or `failed` | `success` |
| `row_count` | INTEGER | Number of records successfully fetched | 1842 |
| `rejected_count` | INTEGER | Records rejected during quality checks | 12 |
| `error_message` | TEXT | Error details (null if success) | `null` |
| `file_path` | TEXT | Path to raw landing zone files | `data/raw/openaq/2026-09-01/Delhi` |

---

## Schema: `raw` — Exact API Payloads

> **Rule:** Raw tables are **never modified** after insertion. They serve as an immutable audit trail.

### `raw.openaq_measurements`

| Column | Type | Description | Example |
|---|---|---|---|
| `id` | BIGSERIAL PK | Auto-incrementing ID | 10001 |
| `ingested_at` | TIMESTAMPTZ | When this record was written to DB | `2026-09-01 02:02:11+00` |
| `extraction_date` | DATE | Date label for this ingestion run | `2026-09-01` |
| `run_id` | UUID | Foreign key to ingestion_log.run_id | `a1b2c3d4-...` |
| `location_id` | INTEGER | OpenAQ monitoring station ID | 9842 |
| `location_name` | TEXT | Station name as returned by API | `Anand Vihar, Delhi - DPCC` |
| `city` | TEXT | City as returned by API | `Delhi` |
| `country` | VARCHAR(10) | ISO country code | `IN` |
| `latitude` | DOUBLE PRECISION | Station latitude | 28.6448 |
| `longitude` | DOUBLE PRECISION | Station longitude | 77.2167 |
| `parameter` | VARCHAR(50) | Pollutant name (API format) | `pm25` |
| `value` | DOUBLE PRECISION | Measured value (original unit) | 55.4 |
| `unit` | VARCHAR(20) | Original measurement unit | `µg/m³` |
| `measured_at` | TIMESTAMPTZ | Observation timestamp (as returned) | `2026-09-01T06:00:00+00:00` |
| `is_mobile` | BOOLEAN | Whether station is mobile | `false` |
| `raw_payload` | JSONB | Complete API response object | `{...}` |

### `raw.openmeteo_weather`

| Column | Type | Description | Unit | Example |
|---|---|---|---|---|
| `id` | BIGSERIAL PK | Auto-incrementing ID | — | 5001 |
| `ingested_at` | TIMESTAMPTZ | When written to DB | — | `2026-09-01 02:05:00+00` |
| `city` | TEXT | City name | — | `Delhi` |
| `latitude` | DOUBLE PRECISION | City centre latitude | ° | 28.6448 |
| `longitude` | DOUBLE PRECISION | City centre longitude | ° | 77.2167 |
| `measured_at` | TIMESTAMPTZ | Observation hour (UTC) | — | `2026-09-01T07:00:00+00` |
| `temperature_2m` | DOUBLE PRECISION | Air temperature at 2m | °C | 32.5 |
| `relative_humidity_2m` | DOUBLE PRECISION | Relative humidity at 2m | % | 68.0 |
| `wind_speed_10m` | DOUBLE PRECISION | Wind speed at 10m | km/h | 15.2 |
| `wind_direction_10m` | DOUBLE PRECISION | Wind direction at 10m | degrees | 225 |
| `precipitation` | DOUBLE PRECISION | Hourly precipitation | mm | 0.0 |
| `surface_pressure` | DOUBLE PRECISION | Sea-level air pressure | hPa | 1009.5 |
| `cloud_cover` | DOUBLE PRECISION | Total cloud cover | % | 40.0 |
| `raw_payload` | JSONB | Complete API response | — | `{...}` |

---

## Schema: `staging` — Normalized Data

### `staging.measurements`

| Column | Type | Description | Validation Rule |
|---|---|---|---|
| `id` | BIGSERIAL PK | Auto-incrementing ID | — |
| `raw_id` | BIGINT FK | Reference to raw record | → raw.openaq_measurements.id |
| `location_id` | INTEGER | Station ID (normalized) | NOT NULL |
| `station_name` | VARCHAR(255) | Title-cased, whitespace-trimmed | NOT NULL |
| `city` | VARCHAR(100) | Standardized city name | NOT NULL |
| `country` | VARCHAR(10) | ISO code | Default 'IN' |
| `latitude` | DOUBLE PRECISION | Decimal degrees WGS84 | −90 to 90 |
| `longitude` | DOUBLE PRECISION | Decimal degrees WGS84 | −180 to 180 |
| `parameter` | VARCHAR(20) | Lowercase standard name | `pm25`, `pm10`, `no2`, `o3`, `co`, `so2` |
| `value_ugm3` | DOUBLE PRECISION | Measurement in µg/m³ | NOT NULL, converted from original unit |
| `measured_at_utc` | TIMESTAMPTZ | UTC observation timestamp | NOT NULL |
| `measured_at_ist` | TIMESTAMPTZ | IST (UTC+5:30) timestamp | NOT NULL |

### `staging.weather`

| Column | Type | Description |
|---|---|---|
| `id` | BIGSERIAL PK | Auto-incrementing ID |
| `raw_id` | BIGINT FK | → raw.openmeteo_weather.id |
| `city` | VARCHAR(100) | Standardized city name |
| `measured_at_utc` | TIMESTAMPTZ | UTC observation hour |
| `measured_at_ist` | TIMESTAMPTZ | IST observation hour |
| `temperature_2m` | DOUBLE PRECISION | °C |
| `relative_humidity_2m` | DOUBLE PRECISION | % |
| `wind_speed_10m` | DOUBLE PRECISION | km/h |
| `wind_direction_10m` | DOUBLE PRECISION | Degrees |
| `precipitation` | DOUBLE PRECISION | mm/hour |
| `surface_pressure` | DOUBLE PRECISION | hPa |
| `cloud_cover` | DOUBLE PRECISION | % |

---

## Schema: `cleaned` — Validated & Quality-Checked

### Validation Rules Applied

| Rule | Action | Logged To |
|---|---|---|
| Missing `value_ugm3` or `measured_at_utc` | Reject record | `error_log.csv` + `cleaned.rejected_records` |
| PM2.5 outside [0, 1000] µg/m³ | Reject record | `error_log.csv` |
| PM10 outside [0, 2000] µg/m³ | Reject record | `error_log.csv` |
| NO2 outside [0, 3000] µg/m³ | Reject record | `error_log.csv` |
| O3 outside [0, 1000] µg/m³ | Reject record | `error_log.csv` |
| CO outside [0, 100,000] µg/m³ | Reject record | `error_log.csv` |
| SO2 outside [0, 2000] µg/m³ | Reject record | `error_log.csv` |
| Duplicate (location_id, parameter, measured_at_utc) | Keep first, discard rest | Pipeline log |
| Time gap > 2 hours at a station | Log warning | Airflow task log |
| Pipeline rejection rate > 20% | Fail the ETL DAG run | Airflow alert |

### `cleaned.air_quality`

| Column | Type | Description |
|---|---|---|
| `id` | BIGSERIAL PK | — |
| `staging_id` | BIGINT FK | → staging.measurements.id |
| `location_id` | INTEGER | Station ID |
| `station_name` | VARCHAR(255) | Clean station name |
| `city` | VARCHAR(100) | City |
| `parameter` | VARCHAR(20) | Pollutant |
| `value_ugm3` | DOUBLE PRECISION | Validated value in µg/m³ |
| `measured_at_utc` | TIMESTAMPTZ | UTC |
| `measured_at_ist` | TIMESTAMPTZ | IST |
| `aqi_sub_index` | DOUBLE PRECISION | EPA AQI sub-index for this measurement |
| `is_valid` | BOOLEAN | Quality flag |
| `validation_note` | TEXT | Reason if flagged |

### `cleaned.rejected_records`

| Column | Type | Description |
|---|---|---|
| `id` | BIGSERIAL PK | — |
| `rejected_at` | TIMESTAMPTZ | When rejected |
| `source_table` | VARCHAR(50) | Source staging table |
| `source_id` | BIGINT | Source record ID |
| `rejection_reason` | TEXT | Human-readable reason |
| `original_value` | TEXT | The problematic value |

---

## Schema: `gold` — Analytical Layer

### `gold.hourly_station_aqi`

One row per (station, hour). Pivot of cleaned measurements.

| Column | Type | Description |
|---|---|---|
| `location_id` | INTEGER | Station ID |
| `station_name` | VARCHAR(255) | Station name |
| `city` | VARCHAR(100) | City |
| `hour_utc` | TIMESTAMPTZ | Hour (UTC) |
| `hour_ist` | TIMESTAMPTZ | Hour (IST) |
| `pm25_avg` | DOUBLE PRECISION | Hourly PM2.5 avg (µg/m³) |
| `pm10_avg` | DOUBLE PRECISION | Hourly PM10 avg (µg/m³) |
| `no2_avg` | DOUBLE PRECISION | Hourly NO2 avg (µg/m³) |
| `o3_avg` | DOUBLE PRECISION | Hourly O3 avg (µg/m³) |
| `co_avg` | DOUBLE PRECISION | Hourly CO avg (µg/m³) |
| `so2_avg` | DOUBLE PRECISION | Hourly SO2 avg (µg/m³) |
| `aqi` | INTEGER | Calculated AQI (US EPA formula) |
| `aqi_category` | VARCHAR(50) | AQI category label |
| `dominant_pollutant` | VARCHAR(20) | Pollutant with highest sub-index |

### `gold.daily_city_aqi`

Primary analytical table. One row per (city, date).

| Column | Type | Description |
|---|---|---|
| `city` | VARCHAR(100) | City name |
| `date_ist` | DATE | Calendar date (IST) |
| `pm25_avg … so2_avg` | DOUBLE PRECISION | 24-hour pollutant averages (µg/m³) |
| `aqi` | INTEGER | Daily AQI |
| `aqi_category` | VARCHAR(50) | AQI category |
| `dominant_pollutant` | VARCHAR(20) | Primary AQI driver |
| `station_count` | INTEGER | Number of stations contributing |

### AQI Category Reference (US EPA)

| AQI Range | Category | Health Concern |
|---|---|---|
| 0–50 | **Good** | Air quality is satisfactory |
| 51–100 | **Moderate** | Acceptable; some pollutants may affect sensitive individuals |
| 101–150 | **Unhealthy for Sensitive Groups** | Sensitive groups may experience effects |
| 151–200 | **Unhealthy** | Everyone may begin to experience effects |
| 201–300 | **Very Unhealthy** | Health alert; serious effects for everyone |
| 301–500 | **Hazardous** | Emergency conditions; everyone is more seriously affected |

### `gold.pollutant_summary`

Long-format pollutant contribution table for charts.

| Column | Type | Description |
|---|---|---|
| `city` | VARCHAR(100) | City |
| `date_ist` | DATE | Date |
| `parameter` | VARCHAR(20) | Pollutant (`pm25`, `pm10`, etc.) |
| `avg_value` | DOUBLE PRECISION | 24-hour average concentration (µg/m³) |
| `aqi_sub_index` | DOUBLE PRECISION | EPA AQI sub-index (0–500) |
| `contribution_pct` | DOUBLE PRECISION | % share of total AQI sub-indices |

### `gold.weather_aqi_daily`

Daily AQI joined with daily weather averages.

| Column | Type | Description |
|---|---|---|
| `city` | VARCHAR(100) | City |
| `date_ist` | DATE | Date |
| `aqi` | INTEGER | Daily AQI |
| `aqi_category` | VARCHAR(50) | AQI category |
| `temp_avg` | DOUBLE PRECISION | Daily average temperature (°C) |
| `humidity_avg` | DOUBLE PRECISION | Daily average relative humidity (%) |
| `wind_speed_avg` | DOUBLE PRECISION | Daily average wind speed (km/h) |
| `precipitation_sum` | DOUBLE PRECISION | Daily total precipitation (mm) |

### `gold.dim_stations`

Station dimension/lookup table.

| Column | Type | Description |
|---|---|---|
| `location_id` | INTEGER PK | OpenAQ station ID |
| `station_name` | VARCHAR(255) | Clean station name |
| `city` | VARCHAR(100) | City |
| `country` | VARCHAR(10) | Country (default 'IN') |
| `latitude` | DOUBLE PRECISION | WGS84 latitude |
| `longitude` | DOUBLE PRECISION | WGS84 longitude |
| `first_seen` | DATE | Date of first observation |
| `last_seen` | DATE | Date of most recent observation |
| `is_active` | BOOLEAN | Whether station is currently reporting |

### Station selection rule

Each city is monitored through a fixed set of up to **5 stations**, chosen by `OpenAQCollector.select_stations`:
still reporting (last data within 14 days), at least 5 target sensors, longest history first (earliest
`datetimeFirst`, ties by station id). Live ingestion and the historical backfill use the same rule, so the
daily city AQI is computed from one consistent station set (25 stations across the 5 cities).

---

## Schema: `ml` — Part 2 (MLOps)

Created by `db/ml_schema.sql`.

### `ml.predictions`

One row per (city, forecast day, model version, source). Actuals are back-filled by the daily monitoring DAG.

| Column | Type | Description | Example |
|---|---|---|---|
| `id` | BIGSERIAL PK | Row ID | 1 |
| `city` | VARCHAR(100) | City | `Delhi` |
| `as_of` | DATE | Last observed day used to build features | `2026-10-02` |
| `forecast_date` | DATE | Day being predicted (`as_of` + 1) | `2026-10-03` |
| `predicted_aqi` | DOUBLE PRECISION | Predicted next-day AQI | 161.6 |
| `predicted_category` | VARCHAR(50) | Category of the prediction | `Unhealthy` |
| `actual_aqi` | INTEGER | Observed AQI once the day is in `gold.daily_city_aqi` (NULL until then) | 158 |
| `actual_category` | VARCHAR(50) | Category of the actual AQI | `Unhealthy` |
| `abs_error` | DOUBLE PRECISION | `abs(predicted_aqi - actual_aqi)` | 3.6 |
| `model_version` | VARCHAR(20) | Registry version that produced it | `3` |
| `source` | VARCHAR(10) | `live` (real forecast) or `backtest` (held-out test period, seeded by training) | `live` |
| `predicted_at` | TIMESTAMPTZ | When the row was written | |

Unique key: `(city, forecast_date, model_version, source)`.

### `ml.api_requests`

Request log written by the FastAPI middleware (`/predict` and `/reload`).

| Column | Type | Description |
|---|---|---|
| `id` | BIGSERIAL PK | Row ID |
| `ts` | TIMESTAMPTZ | Request time |
| `endpoint` | VARCHAR(50) | `/predict` or `/reload` |
| `city` | VARCHAR(100) | City query parameter (NULL for `/reload`) |
| `status_code` | INTEGER | HTTP status returned |
| `latency_ms` | DOUBLE PRECISION | Server-side latency |
| `error` | TEXT | Exception text if the handler raised |

### `ml.monitoring_metrics`

Written daily by the `aqi_ml_monitoring` DAG. Unique key: `(run_date, metric, scope)`.

| `metric` | `scope` | Meaning | Alert rule |
|---|---|---|---|
| `psi` | feature name | Population Stability Index, last 14 days vs the champion's training data from the same time of year | above `max(0.25, 4 × expected sampling noise)` |
| `ks_pvalue` | feature name | Kolmogorov-Smirnov p-value for the same comparison | < 0.05 |
| `missing_pct` | `all` | % of NaN feature values in today's inference rows | > 20 |
| `stale_cities` | `all` | Cities with no gold row for the latest day | > 0 |
| `live_mae_14d` | city or `all` | Mean absolute error of live predictions, last 14 days | > 1.3 × champion test MAE (needs ≥ 10 scored predictions) |
| `latency_p95_ms` | `all` | API p95 latency, last 24 h | > 2000 |
| `error_rate` | `all` | Share of API requests with 5xx or an exception, last 24 h | > 0.05 |

Other columns: `value`, `threshold` (the alert level used), `breached` (BOOLEAN).

### Model features (`ml/features.py`)

Built per (city, date) from `gold.daily_city_aqi` joined to `gold.weather_aqi_daily`. Every feature uses data up to
and including day *t*; the targets are day *t+1*. Each city is reindexed to a complete daily calendar first, so a
missing day produces NaN rather than a shifted value.

| Group | Features |
|---|---|
| Current AQI | `aqi` |
| AQI lags | `aqi_lag1`, `aqi_lag2`, `aqi_lag3`, `aqi_lag7` |
| Rolling AQI | `aqi_roll3_mean`, `aqi_roll7_mean`, `aqi_roll7_std`, `aqi_roll14_mean` |
| Pollutants (daily avg, µg/m³) | `pm25_avg`, `pm10_avg`, `no2_avg`, `o3_avg`, `co_avg`, `so2_avg`, plus `pm25_lag1` |
| Weather (daily) | `temp_avg`, `humidity_avg`, `wind_speed_avg`, `precipitation_sum` |
| Calendar | `dow`, `month`, `is_weekend` |
| Categorical | `city` (one-hot inside the model pipeline) |
| **Targets** | `y_aqi` = next-day AQI (regression); `y_cat` = its AQI category (classification, `Hazardous` merged into `Very Unhealthy`) |

---

## AQI Calculation Method

The pipeline uses the **US EPA AQI formula** with official breakpoint tables.

**Formula:**
```
I = (I_hi - I_lo) / (C_hi - C_lo) × (C - C_lo) + I_lo
```

Where:
- `C` = measured concentration in pollutant-specific units
- `I` = AQI index value
- `C_lo`, `C_hi` = concentration breakpoints for the range containing C
- `I_lo`, `I_hi` = AQI index breakpoints for the same range

**Overall AQI** = maximum sub-index across all pollutants.
**Dominant pollutant** = the one with the highest sub-index.

Reference: [EPA Technical Assistance Document for Reporting the Daily AQI](https://www.airnow.gov/sites/default/files/2020-05/aqi-technical-assistance-document-sept2018.pdf)
