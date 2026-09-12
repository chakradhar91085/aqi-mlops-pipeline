# Data Dictionary — AQI Analysis & Prediction (Part 1)

Database: `aqi_db` (PostgreSQL, schemas `staging` / `clean` / `mart`)
Raw landing zone: `data/raw/openaq/`, `data/raw/openmeteo/` (JSON, one file per source per run date)

## Raw layer (filesystem)

| File pattern | Format | Written by |
|---|---|---|
| `data/raw/openaq/{run_date}_{location_id}.json` | JSON: `{"results": [{station_id, station_name, city, latitude, longitude, pollutant, value, unit, observed_at}, ...]}` | `scripts/ingestion/fetch_openaq.py` |
| `data/raw/openmeteo/{run_date}.json` | JSON, Open-Meteo archive API response (`hourly` block: `time`, `temperature_2m`, `relative_humidity_2m`, `precipitation`, `wind_speed_10m`) | `scripts/ingestion/fetch_openmeteo.py` |

Raw files are never edited in place — each pipeline run writes a new file per run date, preserving the original API response.

## staging schema

### staging.ingestion_log

Run-level audit trail — one row per ingestion+staging pass.

| Column | Type | Description |
|---|---|---|
| log_id | SERIAL PK | Row identifier |
| source | TEXT | Data source(s) staged in this run, e.g. `openaq+open-meteo` |
| extraction_date | TIMESTAMPTZ | When the staging step ran (default `now()`) |
| status | TEXT | `success` or `failed` |
| row_count | INTEGER | Rows written to staging in this run |
| error_message | TEXT | Populated on failure |

### staging.air_quality_observations

Raw pollutant readings after passing range validation, before deduplication/unit normalization.

| Column | Type | Description |
|---|---|---|
| staging_id | BIGSERIAL PK | Row identifier |
| source | TEXT | Always `openaq` currently |
| station_id | TEXT | OpenAQ location id |
| station_name | TEXT | OpenAQ location name |
| city | TEXT | Locality reported by OpenAQ, falls back to `CITY_NAME` env var if null |
| latitude / longitude | DOUBLE PRECISION | Station coordinates |
| pollutant | TEXT | One of `pm25`, `pm10`, `no2`, `so2`, `o3`, `co` |
| value | DOUBLE PRECISION | Raw reported value, **in the source's original unit** (not yet normalized) |
| unit | TEXT | Unit as reported by the source (`µg/m³` or `ppb` depending on pollutant/instrument) |
| observed_at | TIMESTAMPTZ | Measurement timestamp (UTC) |
| ingested_at | TIMESTAMPTZ | When the row was written to staging |

### staging.weather_observations

Hourly weather readings from Open-Meteo, unfiltered.

| Column | Type | Description |
|---|---|---|
| staging_id | BIGSERIAL PK | Row identifier |
| source | TEXT | Always `open-meteo` |
| station_id | TEXT | Fixed label `weather_station` (single weather point per city in this project) |
| latitude / longitude | DOUBLE PRECISION | Weather query coordinates |
| temperature_c | DOUBLE PRECISION | °C |
| humidity_pct | DOUBLE PRECISION | Relative humidity, % |
| wind_speed_ms | DOUBLE PRECISION | m/s |
| precipitation_mm | DOUBLE PRECISION | mm |
| observed_at | TIMESTAMPTZ | Hour timestamp (UTC) |
| ingested_at | TIMESTAMPTZ | When the row was written to staging |

## clean schema

### clean.rejected_records

Every row that failed a data-quality check, with the reason and the original payload — the error log required by the assignment.

| Column | Type | Description |
|---|---|---|
| rejected_id | BIGSERIAL PK | Row identifier |
| source_table | TEXT | Which staging table the record was destined for |
| raw_payload | JSONB | The full row that was rejected |
| reason | TEXT | Human-readable rejection reason |
| rejected_at | TIMESTAMPTZ | When the rejection was logged |

### clean.air_quality

Deduplicated, unit-normalized pollutant readings joined with the nearest hourly weather observation. This is the canonical "clean" fact table.

| Column | Type | Description |
|---|---|---|
| id | BIGSERIAL PK | Row identifier |
| station_id | TEXT | OpenAQ location id |
| station_name | TEXT | OpenAQ location name |
| city | TEXT | City/locality |
| latitude / longitude | DOUBLE PRECISION | Station coordinates |
| pollutant | TEXT | Pollutant code |
| value_std | DOUBLE PRECISION | Value normalized to µg/m³ (see Validation & Normalization Rules) |
| unit_std | TEXT | Always `µg/m³` after normalization |
| observed_at | TIMESTAMPTZ | Measurement timestamp (UTC) |
| temperature_c / humidity_pct / wind_speed_ms / precipitation_mm | DOUBLE PRECISION | Nearest weather reading within a 1-hour tolerance; null if none found |

Unique constraint: `(station_id, pollutant, observed_at)` — upserted on conflict, so re-running a date is idempotent.

### clean.missing_periods

Automated gap log — the "identify missing periods" requirement, not just a narrative note. Populated by `scripts/etl/detect_gaps.py`, which scans the full `clean.air_quality` history per `(station_id, pollutant)` and records any consecutive-reading gap wider than 2 hours.

| Column | Type | Description |
|---|---|---|
| id | BIGSERIAL PK | Row identifier |
| station_id | TEXT | OpenAQ location id |
| pollutant | TEXT | Pollutant code |
| gap_start / gap_end | TIMESTAMPTZ | The last reading before the gap, and the first reading after it |
| gap_hours | DOUBLE PRECISION | Gap length in hours |
| detected_at | TIMESTAMPTZ | When this gap was logged |

Unique constraint: `(station_id, pollutant, gap_start, gap_end)` — re-running is idempotent (`ON CONFLICT DO NOTHING`).

## mart schema (gold layer)

### mart.station_daily_pollutant

Per-station, per-pollutant, per-day aggregates.

| Column | Type | Description |
|---|---|---|
| station_id | TEXT | OpenAQ location id (part of composite PK) |
| city | TEXT | City/locality |
| pollutant | TEXT | Pollutant code (part of composite PK) |
| obs_date | DATE | Calendar date (part of composite PK) |
| avg_value / min_value / max_value | DOUBLE PRECISION | Daily stats, µg/m³ |
| observation_count | INTEGER | Number of clean readings that day |

### mart.city_daily_aqi

City-level daily AQI summary — the table the dashboard reads from directly.

| Column | Type | Description |
|---|---|---|
| city | TEXT | City name (part of composite PK) |
| obs_date | DATE | Calendar date (part of composite PK) |
| avg_aqi | DOUBLE PRECISION | The city's overall AQI for the day, per the **US EPA formula** (see below) — column name kept for backward compatibility, but this is the real AQI, not an average |
| aqi_category | TEXT | Official US EPA category: `Good` / `Moderate` / `Unhealthy for Sensitive Groups` / `Unhealthy` / `Very Unhealthy` / `Hazardous` |
| dominant_pollutant | TEXT | The pollutant whose sub-index produced the overall AQI (max-sub-index rule) |

**AQI methodology** (`scripts/etl/aqi_calculator.py`): for each pollutant, a sub-index (0–500) is computed via the EPA's piecewise-linear breakpoint interpolation (`I = (I_hi - I_lo)/(C_hi - C_lo) * (C - C_lo) + I_lo`) against the standard EPA breakpoint table (PM2.5, PM10, NO2, O3, CO, SO2, all in µg/m³). The overall AQI is the **maximum** sub-index across pollutants, and the dominant pollutant is whichever one produced it — this is the actual EPA methodology, not an approximation. This replaced an earlier placeholder that averaged pollutant concentrations against ad hoc bands (see git history / `docs/report.md` §10 for that iteration).

## Validation & normalization rules

1. **Range check (staging → clean.rejected_records).** Each pollutant reading must fall within a plausible range before being staged:
   - `pm25`, `pm10`: 0–1000
   - `no2`, `so2`: 0–2000
   - `o3`: 0–1000
   - `co`: **100–50000** (tightened from an initial 0–50000; ambient CO essentially never reads below ~100 in either ppb or µg/m³ — see caveat below)

   Anything outside this range, or with an unrecognized pollutant code, is written to `clean.rejected_records` with `reason = 'value out of expected range or unknown pollutant'` instead of being staged.

2. **Deduplication.** `clean.air_quality` dedupes on `(station_id, pollutant, observed_at)`.

3. **Unit normalization (ppb → µg/m³).** This station reports `co`, `no2`, and `so2` in ppb (not µg/m³ like `pm25`/`pm10`/`o3`). `transform_clean.py` converts ppb to µg/m³ using `µg/m³ = ppb × MW / 24.45` (25°C, 1 atm):

   | Pollutant | Molecular weight (g/mol) | Factor |
   |---|---|---|
   | co | 28.01 | 1.145 |
   | no2 | 46.01 | 1.882 |
   | so2 | 64.07 | 2.620 |
   | o3 | 48.00 | 1.963 (unused here — this source already reports o3 in µg/m³) |

4. **Weather join.** Each pollutant reading is matched to the nearest weather observation within ±1 hour (`pd.merge_asof`, `direction="nearest"`).

## Known caveat — CO unit mismatch

A batch of CO readings from this station were found at ~0.88 ppb, implausibly low for ambient urban air. This is most likely an upstream OpenAQ/CPCB unit-labeling issue (the instrument's raw number may actually be in a different scale than the "ppb" label implies), not a pipeline defect. The tightened `co` range (floor of 100) catches and rejects these — see `clean.rejected_records` for a live example. Documented here as a known data-quality finding, not a resolved root cause.

Note: `transform_clean.py`'s unit converter only rewrites `unit_std` to `µg/m³` when it actually applies a known ppb→µg/m³ factor for that pollutant — an unrecognized unit is left unchanged (`unit_std` stays whatever was reported) rather than silently assumed to already be µg/m³. This is a deliberate design choice specifically to avoid this class of bug propagating undetected.

## Testing

`tests/` (pytest, run with `pytest` from the project root — `pytest.ini` sets `pythonpath = scripts`) covers the pure-function logic: `aqi_calculator.py` (breakpoint interpolation, category bands, max-sub-index selection), `validate_and_stage.py`'s range checks (including a regression test pinning the CO floor at 100), `transform_clean.py`'s unit conversion, and `detect_gaps.py`'s gap-finding logic. 36 tests, run directly against the host Python (no Docker required) since these modules have no Airflow-specific dependencies.
