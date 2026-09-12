from __future__ import annotations

from datetime import datetime, timedelta

from airflow import DAG
from airflow.operators.python import PythonOperator

from ingestion.fetch_openaq import fetch_openaq
from ingestion.fetch_openmeteo import fetch_openmeteo
from etl.validate_and_stage import validate_and_stage
from etl.transform_clean import transform_clean
from etl.build_mart import build_mart
from etl.detect_gaps import detect_gaps

default_args = {
    "owner": "aqi_project",
    "retries": 2,
    "retry_delay": timedelta(minutes=5),
}

with DAG(
    dag_id="aqi_ingestion_pipeline",
    description="Ingest AQI + weather data, validate, stage, clean, and build analytical marts",
    default_args=default_args,
    schedule="@daily",
    start_date=datetime(2026, 1, 1),
    catchup=False,
    tags=["aqi", "part1"],
) as dag:

    fetch_openaq_task = PythonOperator(
        task_id="fetch_openaq",
        python_callable=fetch_openaq,
        op_kwargs={"run_date": "{{ ds }}"},
    )

    fetch_openmeteo_task = PythonOperator(
        task_id="fetch_openmeteo",
        python_callable=fetch_openmeteo,
        op_kwargs={"run_date": "{{ ds }}"},
    )

    validate_and_stage_task = PythonOperator(
        task_id="validate_and_stage",
        python_callable=validate_and_stage,
        op_kwargs={"run_date": "{{ ds }}"},
    )

    transform_clean_task = PythonOperator(
        task_id="transform_clean",
        python_callable=transform_clean,
        op_kwargs={"run_date": "{{ ds }}"},
    )

    build_mart_task = PythonOperator(
        task_id="build_mart",
        python_callable=build_mart,
        op_kwargs={"run_date": "{{ ds }}"},
    )

    detect_gaps_task = PythonOperator(
        task_id="detect_gaps",
        python_callable=detect_gaps,
        op_kwargs={"run_date": "{{ ds }}"},
    )

    [fetch_openaq_task, fetch_openmeteo_task] >> validate_and_stage_task >> transform_clean_task
    transform_clean_task >> [build_mart_task, detect_gaps_task]
