"""
dags/aqi_ingestion_dag.py
──────────────────────────
Apache Airflow DAG: Daily AQI Data Ingestion

Schedule: Daily at 02:00 UTC (07:30 IST) — collects previous day's data
          once it is fully available from OpenAQ.

Tasks:
  1. check_db_connection    — Verify PostgreSQL is reachable
  2. ingest_openaq          — Fetch air quality data for all 5 cities
  3. ingest_openmeteo       — Fetch weather data for all 5 cities
  4. log_ingestion_summary  — Write summary to meta.ingestion_log

Retries: 3 attempts, 5-minute exponential backoff
"""

from __future__ import annotations

import logging
import os
from datetime import datetime, timedelta, date

from airflow import DAG
from airflow.operators.python import PythonOperator
from airflow.utils.dates import days_ago

logger = logging.getLogger(__name__)

# ── Default args ──────────────────────────────────────────────────────────────

DEFAULT_ARGS = {
    "owner": "aqi_pipeline",
    "depends_on_past": False,
    "email_on_failure": False,
    "email_on_retry": False,
    "retries": 3,
    "retry_delay": timedelta(minutes=5),
    "retry_exponential_backoff": True,
}

TARGET_CITIES = [c.strip() for c in os.getenv("TARGET_CITIES", "Delhi,Mumbai,Bengaluru,Chennai,Kolkata").split(",")]


# ── Task functions ────────────────────────────────────────────────────────────

def check_db_connection(**context) -> None:
    """Verify PostgreSQL connectivity before starting ingestion."""
    from db.db_utils import test_connection
    if not test_connection():
        raise RuntimeError("Database connection failed — aborting ingestion DAG")
    logger.info("Database connection verified")


def fill_gaps(**context) -> dict:
    """Self-heal after downtime: load any days missing between the last loaded day and yesterday."""
    from run_backfill import fill_gaps as _fill

    result = _fill()
    logger.info("Gap fill result: %s", result)
    return result


def ingest_openaq(**context) -> dict:
    """
    Collect OpenAQ measurements for the logical date (yesterday).
    Pushes summary dict to XCom for downstream tasks.
    """
    from ingestion.openaq_collector import OpenAQCollector

    # Airflow logical_date is the start of the schedule interval
    # We collect data for that calendar date
    logical_date: datetime = context["logical_date"]
    extraction_date: date = logical_date.date()

    logger.info("Ingesting OpenAQ data for %s", extraction_date)
    collector = OpenAQCollector(extraction_date=extraction_date)
    summary = collector.collect(cities=TARGET_CITIES)

    total_rows = sum(summary.values())
    logger.info("OpenAQ ingestion complete: %d total records across %d cities", total_rows, len(summary))

    return {"summary": summary, "total_rows": total_rows, "extraction_date": str(extraction_date)}


def ingest_openmeteo(**context) -> dict:
    """Collect Open-Meteo weather data for the logical date."""
    from ingestion.openmeteo_collector import OpenMeteoCollector

    logical_date: datetime = context["logical_date"]
    extraction_date: date = logical_date.date()

    logger.info("Ingesting Open-Meteo weather for %s", extraction_date)
    collector = OpenMeteoCollector(extraction_date=extraction_date)
    summary = collector.collect(cities=TARGET_CITIES)

    total_rows = sum(summary.values())
    logger.info("Open-Meteo ingestion complete: %d hourly records across %d cities", total_rows, len(summary))

    return {"summary": summary, "total_rows": total_rows, "extraction_date": str(extraction_date)}


def log_ingestion_summary(**context) -> None:
    """Pull XCom results from upstream tasks and log a combined summary."""
    ti = context["ti"]
    openaq_result = ti.xcom_pull(task_ids="ingest_openaq") or {}
    openmeteo_result = ti.xcom_pull(task_ids="ingest_openmeteo") or {}

    logger.info(
        "Ingestion Summary — Date: %s | OpenAQ rows: %d | OpenMeteo rows: %d",
        context["logical_date"].date(),
        openaq_result.get("total_rows", 0),
        openmeteo_result.get("total_rows", 0),
    )


# ── DAG definition ────────────────────────────────────────────────────────────

with DAG(
    dag_id="aqi_ingestion",
    description="Daily AQI data ingestion from OpenAQ and Open-Meteo APIs",
    default_args=DEFAULT_ARGS,
    schedule_interval="0 2 * * *",    # 02:00 UTC daily
    start_date=days_ago(1),
    catchup=False,
    max_active_runs=1,
    tags=["aqi", "ingestion", "data-engineering"],
    doc_md="""
## AQI Ingestion DAG

Fetches daily air quality and weather data for 5 Indian cities:
- **Delhi**, **Mumbai**, **Bengaluru**, **Chennai**, **Kolkata**

### Sources
- **OpenAQ v3 API** — PM2.5, PM10, NO2, O3, CO, SO2
- **Open-Meteo Archive API** — Temperature, Humidity, Wind, Precipitation

### Raw Data Storage
Raw JSON files are stored in `data/raw/openaq/YYYY-MM-DD/` and `data/raw/openmeteo/YYYY-MM-DD/`.

### Downstream
This DAG triggers `aqi_etl` DAG via external trigger or schedule dependency.
    """,
) as dag:

    task_check_db = PythonOperator(
        task_id="check_db_connection",
        python_callable=check_db_connection,
        doc_md="Verifies PostgreSQL is reachable before ingestion begins.",
    )

    task_fill_gaps = PythonOperator(
        task_id="fill_gaps",
        python_callable=fill_gaps,
        doc_md="Loads days missing since the last run (catchup=False means downtime would otherwise leave permanent gaps).",
    )

    task_ingest_openaq = PythonOperator(
        task_id="ingest_openaq",
        python_callable=ingest_openaq,
        doc_md="Fetches OpenAQ measurements for all target cities and saves raw JSON.",
    )

    task_ingest_openmeteo = PythonOperator(
        task_id="ingest_openmeteo",
        python_callable=ingest_openmeteo,
        doc_md="Fetches Open-Meteo hourly weather data and saves raw JSON.",
    )

    task_log_summary = PythonOperator(
        task_id="log_ingestion_summary",
        python_callable=log_ingestion_summary,
        doc_md="Logs combined ingestion summary from both sources.",
    )

    # ── Task dependencies ─────────────────────────────────────────────────────
    # DB check → parallel ingestion → summary log
    task_check_db >> task_fill_gaps >> [task_ingest_openaq, task_ingest_openmeteo] >> task_log_summary
