"""
ml/features.py
───────────────
Single feature pipeline shared by training, the API and monitoring, so the model
sees identical features at train time and serve time.

One row per (city, date). Features use data up to and including day t only;
targets are day t+1.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from etl.aqi_calculator import aqi_category

POLLUTANT_COLS = ["pm25_avg", "pm10_avg", "no2_avg", "o3_avg", "co_avg", "so2_avg"]
WEATHER_COLS = ["temp_avg", "humidity_avg", "wind_speed_avg", "precipitation_sum"]

NUMERIC_FEATURES = [
    "aqi", "aqi_lag1", "aqi_lag2", "aqi_lag3", "aqi_lag7",
    "aqi_roll3_mean", "aqi_roll7_mean", "aqi_roll7_std", "aqi_roll14_mean",
    *POLLUTANT_COLS, "pm25_lag1",
    *WEATHER_COLS,
    "dow", "month", "is_weekend",
]
CATEGORICAL_FEATURES = ["city"]
FEATURES = CATEGORICAL_FEATURES + NUMERIC_FEATURES

# Rare top class is merged so classifiers have enough support.
CATEGORY_ORDER = [
    "Good", "Moderate", "Unhealthy for Sensitive Groups",
    "Unhealthy", "Very Unhealthy",
]


def load_daily(start=None, end=None, city=None) -> pd.DataFrame:
    """Read gold daily tables (AQI + pollutants + weather) from Postgres."""
    from db.db_utils import read_sql

    sql = """
        SELECT d.city, d.date_ist AS date, d.aqi,
               d.pm25_avg, d.pm10_avg, d.no2_avg, d.o3_avg, d.co_avg, d.so2_avg,
               w.temp_avg, w.humidity_avg, w.wind_speed_avg, w.precipitation_sum
        FROM gold.daily_city_aqi d
        LEFT JOIN gold.weather_aqi_daily w
               ON w.city = d.city AND w.date_ist = d.date_ist
        WHERE (CAST(:s AS date) IS NULL OR d.date_ist >= CAST(:s AS date))
          AND (CAST(:e AS date) IS NULL OR d.date_ist <= CAST(:e AS date))
          AND (CAST(:c AS text) IS NULL OR d.city = CAST(:c AS text))
        ORDER BY d.city, d.date_ist
    """
    return read_sql(sql, {"s": start, "e": end, "c": city})


def latest_feature_row(city: str, as_of=None, lookback_days: int = 45):
    """
    Feature row for one city at `as_of` (default: its latest day in gold).
    Returns (X, as_of_date, n_history_days) or raises LookupError.
    """
    as_of_ts = pd.Timestamp(as_of) if as_of is not None else None
    start = (as_of_ts - pd.Timedelta(days=lookback_days)).date() if as_of_ts is not None else None
    daily = load_daily(start=start, end=as_of_ts.date() if as_of_ts is not None else None, city=city)
    if daily.empty:
        raise LookupError(f"No data for {city}")
    daily["date"] = pd.to_datetime(daily["date"])
    if as_of_ts is None:
        as_of_ts = daily["date"].max()
        daily = daily[daily["date"] >= as_of_ts - pd.Timedelta(days=lookback_days)]
    frame = build_feature_frame(daily)
    row = frame[frame["date"] == as_of_ts]
    if row.empty or pd.isna(row["aqi"].iloc[0]):
        raise LookupError(f"No AQI for {city} on {as_of_ts.date()}")
    return as_model_input(row), as_of_ts.date(), int(daily["aqi"].notna().sum())


def merge_category(cat: str | None) -> str | None:
    if cat is None or (isinstance(cat, float) and np.isnan(cat)):
        return None
    return "Very Unhealthy" if cat == "Hazardous" else cat


def build_feature_frame(daily: pd.DataFrame) -> pd.DataFrame:
    """
    daily: columns city, date, aqi + pollutant/weather columns (may have gaps).
    Returns the feature frame with y_aqi / y_cat targets (NaN for the last day).
    """
    daily = daily.copy()
    daily["date"] = pd.to_datetime(daily["date"])
    for col in POLLUTANT_COLS + WEATHER_COLS + ["aqi"]:
        if col not in daily.columns:
            daily[col] = np.nan
        daily[col] = pd.to_numeric(daily[col], errors="coerce")

    frames = []
    for city, g in daily.groupby("city"):
        g = g.drop_duplicates("date").set_index("date").sort_index()
        # Reindex to a complete calendar so shift() means "previous calendar day".
        g = g.reindex(pd.date_range(g.index.min(), g.index.max(), freq="D"))
        g["city"] = city
        g.index.name = "date"

        a = g["aqi"]
        g["aqi_lag1"] = a.shift(1)
        g["aqi_lag2"] = a.shift(2)
        g["aqi_lag3"] = a.shift(3)
        g["aqi_lag7"] = a.shift(7)
        g["aqi_roll3_mean"] = a.rolling(3, min_periods=2).mean()
        g["aqi_roll7_mean"] = a.rolling(7, min_periods=4).mean()
        g["aqi_roll7_std"] = a.rolling(7, min_periods=4).std()
        g["aqi_roll14_mean"] = a.rolling(14, min_periods=7).mean()
        g["pm25_lag1"] = g["pm25_avg"].shift(1)

        g["y_aqi"] = a.shift(-1)
        frames.append(g.reset_index())

    df = pd.concat(frames, ignore_index=True)
    df["dow"] = df["date"].dt.dayofweek
    df["month"] = df["date"].dt.month
    df["is_weekend"] = (df["dow"] >= 5).astype(int)
    df["y_cat"] = df["y_aqi"].map(lambda v: merge_category(aqi_category(v)) if pd.notna(v) else None)
    return df


def make_xy(df: pd.DataFrame, task: str = "reg"):
    """Rows with a known target and today's AQI; returns X, y, meta(city,date)."""
    target = "y_aqi" if task == "reg" else "y_cat"
    d = df[df[target].notna() & df["aqi"].notna()].sort_values(["date", "city"])
    return as_model_input(d), d[target], d[["city", "date"]]


def as_model_input(df: pd.DataFrame) -> pd.DataFrame:
    """Feature columns in model order, numerics as float64 (stable MLflow signature)."""
    X = df[FEATURES].copy()
    X[NUMERIC_FEATURES] = X[NUMERIC_FEATURES].astype("float64")
    return X


def chronological_split(df: pd.DataFrame, train=0.70, val=0.15):
    """Split by calendar date (same cut dates for every city). Returns (train, val, test, cuts)."""
    dates = np.sort(df["date"].unique())
    t_end = dates[int(len(dates) * train) - 1]
    v_end = dates[int(len(dates) * (train + val)) - 1]
    tr = df[df["date"] <= t_end]
    va = df[(df["date"] > t_end) & (df["date"] <= v_end)]
    te = df[df["date"] > v_end]
    cuts = {"train_end": str(pd.Timestamp(t_end).date()), "val_end": str(pd.Timestamp(v_end).date())}
    return tr, va, te, cuts
