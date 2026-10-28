"""
dags/aqi_ml_monitoring_dag.py
──────────────────────────────
Daily ML operations (05:00 UTC, one hour after aqi_etl):

  predict_tomorrow -> backfill_actuals -> compute_metrics -> check_retrain -+-> trigger_training
                                                                            +-> no_retrain
"""

from __future__ import annotations

import logging
import os
from datetime import timedelta

import requests
from airflow import DAG
from airflow.operators.empty import EmptyOperator
from airflow.operators.python import BranchPythonOperator, PythonOperator
from airflow.operators.trigger_dagrun import TriggerDagRunOperator
from airflow.utils.dates import days_ago

logger = logging.getLogger(__name__)

API_URL = os.getenv("API_URL", "http://api:8000")
TARGET_CITIES = [c.strip() for c in os.getenv("TARGET_CITIES", "Delhi,Mumbai,Bengaluru,Chennai,Kolkata").split(",")]

DEFAULT_ARGS = {
    "owner": "aqi_pipeline",
    "depends_on_past": False,
    "retries": 2,
    "retry_delay": timedelta(minutes=5),
}


def predict_tomorrow(**context) -> int:
    """Call the serving API for every city and store the forecast as a live prediction."""
    import pandas as pd
    from db.db_utils import upsert_dataframe

    rows = []
    for city in TARGET_CITIES:
        r = requests.get(f"{API_URL}/predict", params={"city": city}, timeout=30)
        if r.status_code != 200:
            logger.error("predict %s -> %s %s", city, r.status_code, r.text[:200])
            continue
        b = r.json()
        rows.append({
            "city": b["city"], "as_of": b["as_of"], "forecast_date": b["forecast_date"],
            "predicted_aqi": b["predicted_aqi"], "predicted_category": b["predicted_category"],
            "model_version": str(b["model_version"]), "source": "live",
        })
    if not rows:
        raise RuntimeError("No live predictions were produced — is the API up and a champion loaded?")
    upsert_dataframe(
        pd.DataFrame(rows), "predictions", "ml",
        ["city", "forecast_date", "model_version", "source"],
        ["as_of", "predicted_aqi", "predicted_category"],
    )
    return len(rows)


def run_backfill_actuals(**context) -> int:
    from ml.monitoring import backfill_actuals
    return backfill_actuals()


def run_compute_metrics(**context) -> dict:
    from ml.monitoring import compute_metrics
    return compute_metrics()


def check_retrain(**context) -> str:
    summary = context["ti"].xcom_pull(task_ids="compute_metrics") or {}
    reasons = summary.get("retrain_reasons", [])
    if reasons:
        logger.warning("Retraining triggered: %s", "; ".join(reasons))
        return "trigger_training"
    logger.info("No retraining needed")
    return "no_retrain"


with DAG(
    dag_id="aqi_ml_monitoring",
    description="Daily live predictions, actuals backfill, drift/error monitoring and retrain check",
    default_args=DEFAULT_ARGS,
    schedule_interval="0 5 * * *",
    start_date=days_ago(1),
    catchup=False,
    max_active_runs=1,
    tags=["aqi", "mlops", "monitoring"],
) as dag:
    t_predict = PythonOperator(task_id="predict_tomorrow", python_callable=predict_tomorrow)
    t_actuals = PythonOperator(task_id="backfill_actuals", python_callable=run_backfill_actuals)
    t_metrics = PythonOperator(task_id="compute_metrics", python_callable=run_compute_metrics)
    t_check = BranchPythonOperator(task_id="check_retrain", python_callable=check_retrain)
    t_train = TriggerDagRunOperator(task_id="trigger_training", trigger_dag_id="aqi_ml_training", wait_for_completion=False)
    t_none = EmptyOperator(task_id="no_retrain")

    t_predict >> t_actuals >> t_metrics >> t_check >> [t_train, t_none]
