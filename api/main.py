"""
api/main.py
────────────
FastAPI inference service. Loads the registered `champion` model from MLflow and builds
features from Postgres with the same code used in training (ml/features.py).

  GET  /health               model + version status
  GET  /predict?city=Delhi   next-day AQI for a city  (optional &as_of=YYYY-MM-DD)
  POST /reload               reload the champion model from the registry
"""

from __future__ import annotations

import logging
import os
import time
from contextlib import asynccontextmanager
from datetime import date, timedelta

import numpy as np
from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import JSONResponse

from etl.aqi_calculator import aqi_category
from ml.features import latest_feature_row, merge_category

logger = logging.getLogger("api")

MODEL_NAME = "aqi_next_day"
ALIAS = "champion"
MIN_HISTORY_DAYS = 14


class ModelHolder:
    model = None
    version: str | None = None


def load_champion() -> None:
    """Load models:/aqi_next_day@champion. Leaves the holder empty on failure."""
    import mlflow
    import mlflow.sklearn
    from mlflow import MlflowClient

    mlflow.set_tracking_uri(os.getenv("MLFLOW_TRACKING_URI", "http://mlflow:5000"))
    version = MlflowClient().get_model_version_by_alias(MODEL_NAME, ALIAS).version
    ModelHolder.model = mlflow.sklearn.load_model(f"models:/{MODEL_NAME}@{ALIAS}")
    ModelHolder.version = str(version)
    logger.info("Loaded %s v%s", MODEL_NAME, version)


@asynccontextmanager
async def lifespan(app: FastAPI):
    try:
        load_champion()
    except Exception as exc:  # service stays up; /health reports 503 until a champion exists
        logger.warning("No champion model loaded yet: %s", exc)
    yield


app = FastAPI(title="AQI Next-Day Forecast API", version="1.0", lifespan=lifespan)


def _log_request(endpoint: str, city: str | None, status: int, latency_ms: float, error: str | None) -> None:
    # ponytail: one INSERT per request is fine at this volume; batch if traffic grows.
    try:
        from db.db_utils import execute_sql

        execute_sql(
            "INSERT INTO ml.api_requests (endpoint, city, status_code, latency_ms, error) "
            "VALUES (:e, :c, :s, :l, :err)",
            {"e": endpoint, "c": city, "s": status, "l": round(latency_ms, 2), "err": error},
        )
    except Exception as exc:
        logger.warning("request log failed: %s", exc)


@app.middleware("http")
async def log_requests(request: Request, call_next):
    t0 = time.perf_counter()
    status, error = 500, None
    try:
        response = await call_next(request)
        status = response.status_code
        return response
    except Exception as exc:
        error = repr(exc)
        raise
    finally:
        if request.url.path in ("/predict", "/reload"):
            _log_request(
                request.url.path, request.query_params.get("city"), status,
                (time.perf_counter() - t0) * 1000, error,
            )


@app.get("/health")
def health():
    if ModelHolder.model is None:
        return JSONResponse(status_code=503, content={"status": "no_model", "model_version": None})
    return {"status": "ok", "model_name": MODEL_NAME, "model_version": ModelHolder.version}


@app.get("/predict")
def predict(city: str = Query(..., min_length=1), as_of: str | None = None):
    if ModelHolder.model is None:
        raise HTTPException(503, "No champion model loaded")
    try:
        X, as_of_date, n_hist = latest_feature_row(city, as_of)
    except LookupError as exc:
        raise HTTPException(404, str(exc))
    except ValueError:
        raise HTTPException(422, "as_of must be YYYY-MM-DD")
    if n_hist < MIN_HISTORY_DAYS:
        raise HTTPException(422, f"Only {n_hist} days of history for {city}; need >= {MIN_HISTORY_DAYS}")

    pred = float(np.clip(ModelHolder.model.predict(X)[0], 0, 500))
    return {
        "city": city,
        "as_of": str(as_of_date),
        "forecast_date": str(as_of_date + timedelta(days=1)),
        "predicted_aqi": round(pred, 1),
        "predicted_category": merge_category(aqi_category(pred)),
        "model_name": MODEL_NAME,
        "model_version": ModelHolder.version,
        "data_age_days": (date.today() - as_of_date).days,
    }


@app.post("/reload")
def reload_model():
    try:
        load_champion()
    except Exception as exc:
        raise HTTPException(503, f"Reload failed: {exc}")
    return {"status": "reloaded", "model_version": ModelHolder.version}
