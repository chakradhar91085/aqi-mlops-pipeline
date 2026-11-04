"""
etl/gold_layer.py
──────────────────
Builds the gold (analytical) layer tables from cleaned data:
  1. gold.hourly_station_aqi    — hourly averages per station
  2. gold.daily_city_aqi        — daily averages aggregated by city
  3. gold.pollutant_summary     — pollutant contribution percentages
  4. gold.weather_aqi_daily     — joined AQI + weather daily table
  5. gold.dim_stations          — station dimension table (upsert)

All writes use upsert (INSERT ... ON CONFLICT DO UPDATE) so the
gold layer can be safely rebuilt on each pipeline run.
"""

from __future__ import annotations

import logging
from datetime import date

import numpy as np
import pandas as pd

from db.db_utils import upsert_dataframe, read_sql
from etl.aqi_calculator import calculate_aqi_for_df

logger = logging.getLogger(__name__)

# ── Helpers ───────────────────────────────────────────────────────────────────

POLLUTANTS = ["pm25", "pm10", "no2", "o3", "co", "so2"]
IST = "Asia/Kolkata"


def _pivot_pollutants(df: pd.DataFrame, group_cols: list[str], time_col: str) -> pd.DataFrame:
    """
    Pivot cleaned air_quality records into wide format with one column per pollutant.

    Parameters
    ----------
    df         : cleaned.air_quality records (long format)
    group_cols : columns to group by (e.g. ['location_id', 'station_name', 'city'])
    time_col   : time column to group by (e.g. 'hour_utc' or 'date_ist')

    Returns
    -------
    Wide DataFrame with columns: group_cols + time_col + pm25_avg, pm10_avg, …
    """
    all_group_cols = group_cols + [time_col]
    df_pivot = (
        df[df["parameter"].isin(POLLUTANTS)]
        .groupby(all_group_cols + ["parameter"])["value_ugm3"]
        .mean()
        .unstack("parameter")
        .reset_index()
    )
    # Rename columns to <pollutant>_avg
    df_pivot.columns = [
        f"{c}_avg" if c in POLLUTANTS else c
        for c in df_pivot.columns
    ]
    # Ensure all pollutant columns exist even if absent from data
    for p in POLLUTANTS:
        col = f"{p}_avg"
        if col not in df_pivot.columns:
            df_pivot[col] = np.nan
    return df_pivot


# ── 1. Hourly station AQI ─────────────────────────────────────────────────────

def build_hourly_station_aqi(df_cleaned: pd.DataFrame) -> pd.DataFrame:
    """
    Aggregate cleaned measurements to hourly station-level AQI.

    Parameters
    ----------
    df_cleaned : cleaned.air_quality DataFrame

    Returns
    -------
    pd.DataFrame matching gold.hourly_station_aqi schema
    """
    if df_cleaned.empty:
        logger.warning("build_hourly_station_aqi: no cleaned data")
        return pd.DataFrame()

    # Floor timestamps to the hour
    df = df_cleaned.copy()
    # Normalize first: rows read back from Postgres carry the session's offset, and flooring
    # in +05:30 would land on :30 UTC, creating different hour_utc keys than in-memory rows.
    df["hour_utc"] = pd.to_datetime(df["measured_at_utc"], utc=True).dt.floor("h")
    df["hour_ist"] = pd.to_datetime(df["measured_at_ist"], utc=True).dt.tz_convert(IST).dt.floor("h")

    group_cols = ["location_id", "station_name", "city"]
    wide = _pivot_pollutants(df, group_cols, "hour_utc")

    # Recover hour_ist from cleaned data (first per group)
    hour_ist_map = (
        df.groupby(["location_id", "hour_utc"])["hour_ist"].first().reset_index()
    )
    wide = wide.merge(hour_ist_map, on=["location_id", "hour_utc"], how="left")

    # Calculate AQI per row
    pollutant_cols_map = {p: f"{p}_avg" for p in POLLUTANTS if f"{p}_avg" in wide.columns}
    df_for_aqi = wide.rename(columns={v: k for k, v in pollutant_cols_map.items()})
    wide_aqi = calculate_aqi_for_df(df_for_aqi)
    # Rename back
    wide_aqi = wide_aqi.rename(columns=pollutant_cols_map)

    result = wide_aqi[[
        "location_id", "station_name", "city", "hour_utc", "hour_ist",
        "pm25_avg", "pm10_avg", "no2_avg", "o3_avg", "co_avg", "so2_avg",
        "aqi", "aqi_category", "dominant_pollutant",
    ]].copy()

    logger.info("Built %d hourly station AQI records", len(result))
    return result


# ── 2. Daily city AQI ─────────────────────────────────────────────────────────

def build_daily_city_aqi(df_hourly: pd.DataFrame) -> pd.DataFrame:
    """
    Aggregate hourly station AQI to daily city-level AQI.

    Parameters
    ----------
    df_hourly : gold.hourly_station_aqi DataFrame (or equivalent)

    Returns
    -------
    pd.DataFrame matching gold.daily_city_aqi schema
    """
    if df_hourly.empty:
        return pd.DataFrame()

    df = df_hourly.copy()
    df["date_ist"] = pd.to_datetime(df["hour_ist"]).dt.date

    pollutant_avg_cols = [f"{p}_avg" for p in POLLUTANTS if f"{p}_avg" in df.columns]

    daily = (
        df.groupby(["city", "date_ist"])
        .agg(
            **{col: (col, "mean") for col in pollutant_avg_cols},
            station_count=("location_id", "nunique"),
        )
        .reset_index()
    )

    # Recalculate AQI on daily aggregates
    rename_to_param = {f"{p}_avg": p for p in POLLUTANTS}
    df_for_aqi = daily.rename(columns=rename_to_param)
    daily_aqi = calculate_aqi_for_df(df_for_aqi)
    daily_aqi = daily_aqi.rename(columns={p: f"{p}_avg" for p in POLLUTANTS})

    result = daily_aqi[[
        "city", "date_ist",
        "pm25_avg", "pm10_avg", "no2_avg", "o3_avg", "co_avg", "so2_avg",
        "aqi", "aqi_category", "dominant_pollutant", "station_count",
    ]].copy()

    # Round pollutant averages
    for col in pollutant_avg_cols:
        if col in result.columns:
            result[col] = result[col].round(2)

    logger.info("Built %d daily city AQI records", len(result))
    return result


# ── 3. Pollutant summary (contribution analysis) ──────────────────────────────

def build_pollutant_summary(df_daily_city: pd.DataFrame) -> pd.DataFrame:
    """
    Build a long-format pollutant contribution table for pie/bar charts.

    For each city+date, computes each pollutant's AQI sub-index and
    its percentage contribution to total AQI.
    """
    if df_daily_city.empty:
        return pd.DataFrame()

    from etl.aqi_calculator import sub_index as compute_sub_index

    rows: list[dict] = []
    for _, row in df_daily_city.iterrows():
        sub_indices: dict[str, float] = {}
        for p in POLLUTANTS:
            avg_col = f"{p}_avg"
            val = row.get(avg_col)
            if val is not None and not np.isnan(val):
                si = compute_sub_index(p, float(val))
                if si is not None:
                    sub_indices[p] = si

        total_si = sum(sub_indices.values()) or 1.0  # avoid division by zero
        for p, si in sub_indices.items():
            avg_col = f"{p}_avg"
            rows.append({
                "city": row["city"],
                "date_ist": row["date_ist"],
                "parameter": p,
                "avg_value": row.get(avg_col),
                "aqi_sub_index": round(si, 2),
                "contribution_pct": round(si / total_si * 100, 2),
            })

    result = pd.DataFrame(rows) if rows else pd.DataFrame()
    logger.info("Built %d pollutant summary records", len(result))
    return result


# ── 4. Weather + AQI daily join ───────────────────────────────────────────────

def build_weather_aqi_daily(
    df_daily_city: pd.DataFrame,
    df_weather_cleaned: pd.DataFrame,
) -> pd.DataFrame:
    """
    Join daily city AQI with daily weather averages.

    Parameters
    ----------
    df_daily_city    : gold.daily_city_aqi DataFrame
    df_weather_cleaned : cleaned.weather DataFrame (hourly)

    Returns
    -------
    pd.DataFrame matching gold.weather_aqi_daily schema
    """
    if df_daily_city.empty or df_weather_cleaned.empty:
        logger.warning("build_weather_aqi_daily: missing input data")
        return pd.DataFrame()

    df_w = df_weather_cleaned.copy()
    df_w["date_ist"] = pd.to_datetime(df_w["measured_at_ist"], utc=True).dt.tz_convert(IST).dt.date

    weather_daily = (
        df_w.groupby(["city", "date_ist"])
        .agg(
            temp_avg=("temperature_2m", "mean"),
            humidity_avg=("relative_humidity_2m", "mean"),
            wind_speed_avg=("wind_speed_10m", "mean"),
            precipitation_sum=("precipitation", "sum"),
        )
        .reset_index()
    )

    merged = df_daily_city[["city", "date_ist", "aqi", "aqi_category"]].merge(
        weather_daily, on=["city", "date_ist"], how="inner"
    )

    for col in ["temp_avg", "humidity_avg", "wind_speed_avg", "precipitation_sum"]:
        if col in merged.columns:
            merged[col] = merged[col].round(2)

    logger.info("Built %d weather-AQI daily records", len(merged))
    return merged


# ── 5. Station dimension table ────────────────────────────────────────────────

def build_dim_stations(df_cleaned: pd.DataFrame) -> pd.DataFrame:
    """Build/update the station dimension table from cleaned measurements."""
    if df_cleaned.empty:
        return pd.DataFrame()

    dim = (
        df_cleaned.groupby("location_id")
        .agg(
            station_name=("station_name", "first"),
            city=("city", "first"),
            country=("country", "first"),
            latitude=("latitude", "first"),
            longitude=("longitude", "first"),
            first_seen=("measured_at_utc", lambda x: x.min().date()),
            last_seen=("measured_at_utc", lambda x: x.max().date()),
        )
        .reset_index()
    )
    dim["is_active"] = True
    return dim


# ── Master gold layer builder ─────────────────────────────────────────────────

def build_gold_layer(
    df_cleaned_aq: pd.DataFrame,
    df_cleaned_weather: pd.DataFrame,
) -> dict[str, pd.DataFrame]:
    """
    Build all gold-layer tables and write them to PostgreSQL.

    Parameters
    ----------
    df_cleaned_aq      : cleaned.air_quality DataFrame
    df_cleaned_weather : cleaned.weather DataFrame

    Returns
    -------
    dict of gold DataFrames (also written to DB)
    """
    logger.info("Building gold layer…")

    # 1. Hourly station AQI
    df_hourly = build_hourly_station_aqi(df_cleaned_aq)
    if not df_hourly.empty:
        upsert_dataframe(
            df=df_hourly,
            table="hourly_station_aqi",
            schema="gold",
            conflict_columns=["location_id", "hour_utc"],
            update_columns=[
                "hour_ist",
                "pm25_avg", "pm10_avg", "no2_avg", "o3_avg", "co_avg", "so2_avg",
                "aqi", "aqi_category", "dominant_pollutant",
            ],
        )

    # 2. Daily city AQI
    df_daily = build_daily_city_aqi(df_hourly)
    if not df_daily.empty:
        upsert_dataframe(
            df=df_daily,
            table="daily_city_aqi",
            schema="gold",
            conflict_columns=["city", "date_ist"],
            update_columns=[
                "pm25_avg", "pm10_avg", "no2_avg", "o3_avg", "co_avg", "so2_avg",
                "aqi", "aqi_category", "dominant_pollutant", "station_count",
            ],
        )

    # 3. Pollutant summary
    df_pollutant = build_pollutant_summary(df_daily)
    if not df_pollutant.empty:
        upsert_dataframe(
            df=df_pollutant,
            table="pollutant_summary",
            schema="gold",
            conflict_columns=["city", "date_ist", "parameter"],
            update_columns=["avg_value", "aqi_sub_index", "contribution_pct"],
        )

    # 4. Weather AQI daily
    df_weather_aqi = build_weather_aqi_daily(df_daily, df_cleaned_weather)
    if not df_weather_aqi.empty:
        upsert_dataframe(
            df=df_weather_aqi,
            table="weather_aqi_daily",
            schema="gold",
            conflict_columns=["city", "date_ist"],
            update_columns=[
                "aqi", "aqi_category",
                "temp_avg", "humidity_avg", "wind_speed_avg", "precipitation_sum",
            ],
        )

    # 5. Station dimension
    df_stations = build_dim_stations(df_cleaned_aq)
    if not df_stations.empty:
        upsert_dataframe(
            df=df_stations,
            table="dim_stations",
            schema="gold",
            conflict_columns=["location_id"],
            update_columns=[
                "station_name", "city", "country",
                "latitude", "longitude", "last_seen", "is_active",
            ],
        )

    logger.info("Gold layer build complete")
    return {
        "hourly_station_aqi": df_hourly,
        "daily_city_aqi": df_daily,
        "pollutant_summary": df_pollutant,
        "weather_aqi_daily": df_weather_aqi,
        "dim_stations": df_stations,
    }
