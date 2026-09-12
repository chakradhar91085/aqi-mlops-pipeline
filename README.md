# AQI Analysis & Prediction — Part 1 (Data Pipeline)

## Stack

- **Postgres 16** — analytical warehouse (`aqi_db`, schemas `staging` / `clean` / `mart`) and Airflow's own metadata DB, one container, both run through Docker.
- **Apache Airflow 2.9.3** (`LocalExecutor`) — orchestrates the daily DAG.
- **Streamlit** — dashboard over the `mart` tables.
- Everything runs via a single `docker-compose.yml`; no local Postgres/Airflow install needed.

## Setup

1. Copy `.env.example` to `.env` and fill in:
   - A Postgres password.
   - An [OpenAQ](https://explore.openaq.org) API key and a location ID for your target station.
   - Latitude/longitude for the Open-Meteo weather call.
2. Build and start everything:

   ```
   docker compose up --build
   ```

   First run: `airflow-init` creates the Airflow metadata schema, an admin user, and (via `postgres-init/01-init.sh`) the `aqi_db` database with the `staging`/`clean`/`mart` schemas from `sql/schema.sql`.

3. Airflow UI: http://localhost:8080 (login from `AIRFLOW_ADMIN_USER` / `AIRFLOW_ADMIN_PASSWORD`).
4. Unpause and trigger `aqi_ingestion_pipeline` for the date(s) you want to backfill.
5. Once the mart tables have data, run the dashboard locally:

   ```
   pip install -r requirements.txt
   $env:AQI_DB_HOST="localhost"
   $env:AQI_DB_PORT="5433"
   streamlit run dashboards/app.py
   ```

   Postgres is published on `localhost:5433` (not 5432 — that's reserved for your native Postgres install), so the dashboard can connect from outside Docker without conflicting with it.

## Pipeline layers

| Layer | Where | Populated by |
|---|---|---|
| Raw | `data/raw/openaq/`, `data/raw/openmeteo/` (JSON, one file per run date) | `scripts/ingestion/*` |
| Staging | Postgres schema `staging` | `scripts/etl/validate_and_stage.py` — also logs rejected rows to `clean.rejected_records` and run summaries to `staging.ingestion_log` |
| Clean | Postgres schema `clean` | `scripts/etl/transform_clean.py` — dedupes, normalizes, joins pollution + weather |
| Mart (gold) | Postgres schema `mart` | `scripts/etl/build_mart.py` — `station_daily_pollutant`, `city_daily_aqi` |

DAG: `dags/aqi_pipeline_dag.py` — `[fetch_openaq, fetch_openmeteo] >> validate_and_stage >> transform_clean >> [build_mart, detect_gaps]`.

## AQI methodology

`build_mart.py` uses the real **US EPA AQI formula** (`scripts/etl/aqi_calculator.py`) — per-pollutant breakpoint interpolation, overall AQI = max sub-index, dominant pollutant = whichever one produced it. See `docs/data_dictionary.md` for the breakpoint tables.

## Testing

```
pip install -r requirements.txt pytest
pytest
```

36 tests covering the AQI calculator, validation ranges, unit conversion, and gap detection — no Docker required, these are pure-function unit tests.

## Part 2 (not in scope here)

Model training, MLflow tracking, FastAPI serving, and Docker-based deployment of the inference service are Part 2 requirements and are intentionally not part of this pipeline.

## Submission package (Part 1)

| Item | Location |
|---|---|
| Source code and ETL scripts | `scripts/`, `dags/` |
| Airflow DAG | `dags/aqi_pipeline_dag.py` |
| Database schema | `sql/schema.sql` |
| Dataset source info + access instructions | `docs/data_sources.md` |
| Architecture diagram | `docs/architecture.md` |
| Data dictionary + validation rules | `docs/data_dictionary.md` |
| Streamlit application | `dashboards/app.py` |
| Project report | `docs/report.md` |
| Screenshots / execution evidence | `docs/screenshots/` |
| Setup/run instructions | this file |
