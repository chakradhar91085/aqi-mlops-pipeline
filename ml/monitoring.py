"""
ml/monitoring.py
─────────────────
Model + data monitoring for the AQI forecaster. Pure numpy/scipy (no extra tooling).

Writes to ml.monitoring_metrics:
  psi / ks_pvalue   per key feature   — feature drift vs the champion's training window
  missing_pct       all               — % NaN in today's inference feature rows
  stale_cities      all               — cities with no gold row for the latest day
  data_age_days     all               — days between today and the newest gold day
  live_mae_14d      city + all        — forecasting error of live predictions
  latency_p95_ms    all               — API latency over the last 24h
  error_rate        all               — API 5xx / exception share over the last 24h
"""

from __future__ import annotations

import logging
import os
from datetime import date, timedelta

import numpy as np
import pandas as pd

from etl.aqi_calculator import aqi_category
from ml.features import FEATURES, build_feature_frame, load_daily, merge_category

logger = logging.getLogger("ml.monitoring")

DRIFT_FEATURES = ["aqi_lag1", "pm25_avg", "pm10_avg", "temp_avg", "humidity_avg", "wind_speed_avg"]

PSI_THRESHOLD = 0.25        # >0.25 = significant shift
PSI_FEATURES_TO_RETRAIN = 2
MAE_MULTIPLIER = 1.3        # live MAE > 1.3 x champion test MAE
MIN_LIVE_POINTS = 10        # don't judge MAE on fewer predictions than this
NEW_DATA_DAYS = 30          # retrain once this many new days exist past train_end
MISSING_PCT_THRESHOLD = 20.0
LATENCY_P95_THRESHOLD_MS = 2000.0
ERROR_RATE_THRESHOLD = 0.05
RETRAIN_COOLDOWN_DAYS = 7  # after any training run, wait before triggering another
DATA_AGE_THRESHOLD_DAYS = 2  # newest gold day older than this = ingestion is not keeping up


def psi(expected, actual, bins: int = 10) -> float:
    """Population Stability Index between a reference and a current sample (quantile bins)."""
    e = np.asarray(expected, dtype=float); a = np.asarray(actual, dtype=float)
    e, a = e[~np.isnan(e)], a[~np.isnan(a)]
    if len(e) < 2 or len(a) < 2:
        return float("nan")
    edges = np.unique(np.quantile(e, np.linspace(0, 1, bins + 1)))
    if len(edges) < 3:
        return 0.0 if np.allclose(e.mean(), a.mean()) else float("inf")
    edges[0], edges[-1] = -np.inf, np.inf
    pe = np.clip(np.histogram(e, edges)[0] / len(e), 1e-4, None)
    pa = np.clip(np.histogram(a, edges)[0] / len(a), 1e-4, None)
    return float(np.sum((pa - pe) * np.log(pa / pe)))


def psi_bins(n_current: int) -> int:
    """Fewer bins for small windows (a 14-day window has ~70 rows across 5 cities)."""
    return int(np.clip(n_current // 15, 3, 10))


def psi_threshold(n_ref: int, n_current: int, bins: int) -> float:
    """
    Alert level: the usual 0.25, raised to ~4x the PSI expected from sampling noise alone,
    (bins-1)*(1/n_ref + 1/n_current). Measured here: random halves of identical data reach
    PSI ~1.0 at 10 bins / ~100 rows, so a fixed 0.25 would alert permanently.
    """
    return max(PSI_THRESHOLD, 4 * (bins - 1) * (1 / max(n_ref, 1) + 1 / max(n_current, 1)))


def seasonal_reference(ref: pd.DataFrame, today, window_days: int = 21, min_rows: int = 30) -> pd.DataFrame:
    """
    Keep reference rows from the same time of year as `today` (+/- window_days). Comparing a
    14-day autumn window with a whole Oct-Jun training period flags seasonality as drift.
    Falls back to the full reference when the training data has too few rows for that season.
    """
    gap = (ref["date"].dt.dayofyear - pd.Timestamp(today).dayofyear + 182) % 365 - 182
    seasonal = ref[gap.abs() <= window_days]
    return seasonal if len(seasonal) >= min_rows else ref


def evaluate_retrain(live_mae, n_live, champion_mae, n_psi_breached, days_since_train_end,
                     days_since_last_training=None) -> list[str]:
    """
    Return the retraining triggers that fired (empty = no retrain).
    Cooldown: a persistent trigger (drift stays until the champion changes) must not retrain daily.
    """
    if days_since_last_training is not None and days_since_last_training < RETRAIN_COOLDOWN_DAYS:
        return []
    reasons = []
    if live_mae is not None and champion_mae and n_live >= MIN_LIVE_POINTS and live_mae > MAE_MULTIPLIER * champion_mae:
        reasons.append(f"performance: live MAE {live_mae:.1f} > {MAE_MULTIPLIER} x champion test MAE {champion_mae:.1f}")
    if n_psi_breached >= PSI_FEATURES_TO_RETRAIN:
        reasons.append(f"drift: PSI above its sample-size-adjusted threshold on {n_psi_breached} features")
    if days_since_train_end is not None and days_since_train_end >= NEW_DATA_DAYS:
        reasons.append(f"new data: {days_since_train_end} days past champion train_end")
    return reasons


# ── Champion info ─────────────────────────────────────────────────────────────

def champion_info() -> dict:
    import mlflow
    from mlflow import MlflowClient

    mlflow.set_tracking_uri(os.getenv("MLFLOW_TRACKING_URI", "http://mlflow:5000"))
    client = MlflowClient()
    mv = client.get_model_version_by_alias("aqi_next_day", "champion")
    run = client.get_run(mv.run_id)
    return {
        "version": str(mv.version),
        "train_start": run.data.params.get("train_start"),
        "train_end": run.data.params.get("train_end"),
        "data_end": run.data.params.get("test_end"),  # last day of data the model has seen
        "test_mae": run.data.metrics.get("test_mae"),
        "last_trained_ms": max(v.creation_timestamp for v in client.search_model_versions("name='aqi_next_day'")),
    }


# ── Actuals backfill ──────────────────────────────────────────────────────────

def backfill_actuals() -> int:
    """Fill actual_aqi / abs_error on predictions whose forecast day is now in gold."""
    from db.db_utils import execute_sql, read_sql

    pending = read_sql(
        """
        SELECT p.id, p.predicted_aqi, d.aqi AS actual
        FROM ml.predictions p
        JOIN gold.daily_city_aqi d ON d.city = p.city AND d.date_ist = p.forecast_date
        WHERE p.actual_aqi IS NULL AND d.aqi IS NOT NULL
        """
    )
    for r in pending.itertuples():
        execute_sql(
            "UPDATE ml.predictions SET actual_aqi=:a, actual_category=:c, abs_error=:e WHERE id=:i",
            {"a": int(r.actual), "c": merge_category(aqi_category(r.actual)),
             "e": round(abs(float(r.predicted_aqi) - float(r.actual)), 2), "i": int(r.id)},
        )
    logger.info("Backfilled actuals for %d predictions", len(pending))
    return len(pending)


# ── Metrics ───────────────────────────────────────────────────────────────────

def _row(run_date, metric, scope, value, threshold=None, breached=None) -> dict:
    v = None if value is None or (isinstance(value, float) and not np.isfinite(value)) else float(value)
    return {"run_date": run_date, "metric": metric, "scope": scope, "value": v,
            "threshold": threshold, "breached": bool(breached) if breached is not None else None}


def compute_metrics(run_date: date | None = None) -> dict:
    """Compute and store all monitoring metrics; return a summary incl. the retrain decision."""
    from db.db_utils import read_sql, upsert_dataframe

    run_date = run_date or date.today()
    rows: list[dict] = []
    champ = champion_info()

    # Feature frame over all history; reference = champion's training window, current = last 14 days.
    frame = build_feature_frame(load_daily())
    latest_day = frame.loc[frame["aqi"].notna(), "date"].max()
    ref = frame[(frame["date"] >= champ["train_start"]) & (frame["date"] <= champ["train_end"])]
    cur = frame[(frame["date"] > latest_day - pd.Timedelta(days=14)) & (frame["date"] <= latest_day)]
    ref = seasonal_reference(ref, latest_day)

    n_psi_breached = 0
    for f in DRIFT_FEATURES:
        n_ref, n_cur = int(ref[f].notna().sum()), int(cur[f].notna().sum())
        bins = psi_bins(n_cur)
        p, thr_f = psi(ref[f], cur[f], bins), psi_threshold(n_ref, n_cur, bins)
        breached = bool(np.isfinite(p) and p > thr_f)
        n_psi_breached += breached
        rows.append(_row(run_date, "psi", f, p, thr_f, breached))
        if ref[f].notna().sum() > 1 and cur[f].notna().sum() > 1:
            from scipy.stats import ks_2samp
            ks_p = ks_2samp(ref[f].dropna(), cur[f].dropna()).pvalue
            rows.append(_row(run_date, "ks_pvalue", f, ks_p, 0.05, ks_p < 0.05))

    # Input quality: latest inference row per city.
    latest_rows = frame.sort_values("date").groupby("city").tail(1)
    missing_pct = float(latest_rows[FEATURES].isna().mean().mean() * 100)
    rows.append(_row(run_date, "missing_pct", "all", missing_pct, MISSING_PCT_THRESHOLD, missing_pct > MISSING_PCT_THRESHOLD))
    data_age = (pd.Timestamp(run_date) - latest_day).days
    rows.append(_row(run_date, "data_age_days", "all", data_age, DATA_AGE_THRESHOLD_DAYS, data_age > DATA_AGE_THRESHOLD_DAYS))
    stale = int((latest_rows["date"] < latest_day).sum())
    rows.append(_row(run_date, "stale_cities", "all", stale, 0, stale > 0))

    # Forecasting error of live predictions.
    live = read_sql(
        "SELECT city, abs_error FROM ml.predictions WHERE source='live' AND abs_error IS NOT NULL "
        "AND forecast_date > CAST(:d AS date) - 14",
        {"d": str(run_date)},
    )
    champion_mae = champ["test_mae"]
    thr = MAE_MULTIPLIER * champion_mae if champion_mae else None
    live_mae = float(live["abs_error"].mean()) if len(live) else None
    for city, g in live.groupby("city"):
        rows.append(_row(run_date, "live_mae_14d", city, g["abs_error"].mean(), thr))
    if live_mae is not None:
        rows.append(_row(run_date, "live_mae_14d", "all", live_mae, thr,
                         thr is not None and len(live) >= MIN_LIVE_POINTS and live_mae > thr))

    # API health (last 24h).
    api = read_sql(
        "SELECT percentile_cont(0.95) WITHIN GROUP (ORDER BY latency_ms) AS p95, "
        "AVG(CASE WHEN status_code >= 500 OR error IS NOT NULL THEN 1.0 ELSE 0.0 END) AS err, COUNT(*) AS n "
        "FROM ml.api_requests WHERE ts > NOW() - INTERVAL '24 hours'"
    )
    if int(api["n"].iloc[0]) > 0:
        p95, err = float(api["p95"].iloc[0]), float(api["err"].iloc[0])
        rows.append(_row(run_date, "latency_p95_ms", "all", p95, LATENCY_P95_THRESHOLD_MS, p95 > LATENCY_P95_THRESHOLD_MS))
        rows.append(_row(run_date, "error_rate", "all", err, ERROR_RATE_THRESHOLD, err > ERROR_RATE_THRESHOLD))

    upsert_dataframe(pd.DataFrame(rows), "monitoring_metrics", "ml", ["run_date", "metric", "scope"],
                     ["value", "threshold", "breached"])

    days_since = (latest_day - pd.Timestamp(champ["data_end"] or champ["train_end"])).days
    since_train = (pd.Timestamp.now(tz='UTC') - pd.Timestamp(champ['last_trained_ms'], unit='ms', tz='UTC')).days
    reasons = evaluate_retrain(live_mae, len(live), champion_mae, n_psi_breached, days_since, since_train)
    summary = {"champion_version": champ["version"], "live_mae": live_mae, "n_live": len(live),
               "n_psi_breached": n_psi_breached, "days_since_train_end": days_since,
               "missing_pct": missing_pct, "retrain_reasons": reasons}
    logger.info("Monitoring summary: %s", summary)
    return summary
