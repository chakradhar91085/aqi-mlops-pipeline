"""
dags/aqi_ml_training_dag.py
────────────────────────────
Weekly (Sunday 06:00 UTC) or triggered retraining:

  train_and_evaluate  -> logs all runs to MLflow, registers the best regressor,
                         promotes it to @champion only if its test MAE <= the champion's
  reload_api          -> POST /reload so the API serves the (possibly new) champion
"""

from __future__ import annotations

import logging
import os
from datetime import timedelta

import requests
from airflow import DAG
from airflow.operators.python import PythonOperator
from airflow.utils.dates import days_ago

logger = logging.getLogger(__name__)

API_URL = os.getenv("API_URL", "http://api:8000")

DEFAULT_ARGS = {
    "owner": "aqi_pipeline",
    "depends_on_past": False,
    "retries": 1,
    "retry_delay": timedelta(minutes=10),
}


def train_and_evaluate(**context) -> dict:
    from ml.train import train

    out = train(promote=True)
    return {"best_regressor": out["best_regressor"], "version": str(out["version"])}


def reload_api(**context) -> dict:
    r = requests.post(f"{API_URL}/reload", timeout=60)
    r.raise_for_status()
    logger.info("API reloaded: %s", r.json())
    return r.json()


with DAG(
    dag_id="aqi_ml_training",
    description="Train/compare models, register in MLflow, promote champion, reload API",
    default_args=DEFAULT_ARGS,
    schedule_interval="0 6 * * 0",
    start_date=days_ago(7),
    catchup=False,
    max_active_runs=1,
    tags=["aqi", "mlops", "training"],
) as dag:
    t_train = PythonOperator(task_id="train_and_evaluate", python_callable=train_and_evaluate)
    t_reload = PythonOperator(task_id="reload_api", python_callable=reload_api)
    t_train >> t_reload
