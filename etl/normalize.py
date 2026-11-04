"""
etl/normalize.py
─────────────────
Normalization transforms applied to raw staging data:
  - UTC → IST timestamp conversion
  - Station name standardization (title-case, whitespace trim)
  - Pollutant unit normalization to µg/m³
  - Consistent city/country name formatting
"""

from __future__ import annotations

import logging
import re
from datetime import timezone

import pandas as pd
import numpy as np

logger = logging.getLogger(__name__)

# ── Pollutant name mapping (OpenAQ API name → standard name) ─────────────────
POLLUTANT_NAME_MAP: dict[str, str] = {
    "pm25":  "pm25",
    "pm2.5": "pm25",
    "pm10":  "pm10",
    "no2":   "no2",
    "o3":    "o3",
    "co":    "co",
    "so2":   "so2",
    "nox":   "no2",   # NOx mapped to NO2 as proxy
}

# ── Unit conversion factors → µg/m³ ──────────────────────────────────────────
# ppb conversions at standard conditions (25°C, 1 atm)
UNIT_CONVERSION: dict[str, dict[str, float]] = {
    "pm25":  {"µg/m³": 1.0, "ug/m3": 1.0, "ppb": 1.0},
    "pm10":  {"µg/m³": 1.0, "ug/m3": 1.0, "ppb": 1.0},
    "no2":   {"µg/m³": 1.0, "ug/m3": 1.0, "ppb": 1.912},    # 1 ppb NO2 ≈ 1.912 µg/m³
    "o3":    {"µg/m³": 1.0, "ug/m3": 1.0, "ppb": 1.9957},   # 1 ppb O3 ≈ 1.996 µg/m³
    "co":    {"µg/m³": 1.0, "ug/m3": 1.0, "ppm": 1145.0},   # 1 ppm CO ≈ 1145 µg/m³
    "so2":   {"µg/m³": 1.0, "ug/m3": 1.0, "ppb": 2.6196},   # 1 ppb SO2 ≈ 2.620 µg/m³
}


# ── Station name standardization ─────────────────────────────────────────────

def _clean_station_name(name: str | None) -> str:
    """
    Standardize station names:
    - Strip whitespace
    - Collapse multiple spaces
    - Title-case
    - Remove non-printable characters
    """
    if not name or pd.isna(name):
        return "Unknown Station"
    cleaned = re.sub(r"[^\x20-\x7E\u0900-\u097F]", "", str(name))
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    return cleaned.title()


def _clean_city_name(name: str | None) -> str:
    """Standardize city names to consistent title-case."""
    city_aliases: dict[str, str] = {
        "new delhi":  "Delhi",
        "delhi":      "Delhi",
        "ncr":        "Delhi",
        "bombay":     "Mumbai",
        "bangalore":  "Bengaluru",
        "calcutta":   "Kolkata",
        "madras":     "Chennai",
    }
    if not name or pd.isna(name):
        return "Unknown"
    normalized = str(name).strip().lower()
    return city_aliases.get(normalized, str(name).strip().title())


# ── Unit normalization ────────────────────────────────────────────────────────

def _to_ugm3(value: float, unit: str, parameter: str) -> float | None:
    """Convert a pollutant measurement to µg/m³."""
    if pd.isna(value):
        return None
    unit_clean = unit.strip().lower().replace(" ", "")
    # Lookup conversion table for this pollutant
    conversions = UNIT_CONVERSION.get(parameter, {})
    for unit_key, factor in conversions.items():
        if unit_clean == unit_key.lower().replace(" ", ""):
            return float(value) * factor
    # Fallback: assume already µg/m³
    logger.debug("Unknown unit '%s' for %s — assuming µg/m³", unit, parameter)
    return float(value)


# ── Timestamp normalization ───────────────────────────────────────────────────

def _parse_timestamp_utc(ts: str | pd.Timestamp | None) -> pd.Timestamp | None:
    """Parse any timestamp string/object and return UTC-aware Timestamp."""
    if ts is None or (isinstance(ts, float) and np.isnan(ts)):
        return None
    try:
        parsed = pd.to_datetime(ts, utc=True)
        return parsed
    except Exception:
        return None


def _utc_to_ist(ts_utc: pd.Timestamp | None) -> pd.Timestamp | None:
    """Convert UTC timestamp to IST (UTC+5:30)."""
    if ts_utc is None or pd.isna(ts_utc):
        return None
    return ts_utc.tz_convert("Asia/Kolkata")


# ── Main normalization functions ──────────────────────────────────────────────

def normalize_measurements(raw_records: list[dict]) -> pd.DataFrame:
    """
    Normalize raw OpenAQ measurement records into a staging-ready DataFrame.

    Parameters
    ----------
    raw_records : list of dicts loaded from raw JSON files

    Returns
    -------
    pd.DataFrame with columns matching staging.measurements schema
    """
    if not raw_records:
        logger.warning("normalize_measurements called with empty input")
        return pd.DataFrame()

    rows: list[dict] = []
    for rec in raw_records:
        # Extract nested fields from OpenAQ v3 response
        loc_meta = rec.get("_location_meta", {})
        coords = loc_meta.get("coordinates", {}) or {}

        # ── Parameter ────────────────────────────────────────────────────────
        # v3 sensor format stores parameter in _parameter (set by collector)
        # Fall back to legacy "parameter" field for backwards compat
        raw_param = rec.get("_parameter") or rec.get("parameter")
        if isinstance(raw_param, dict):
            raw_parameter = raw_param.get("name", "")
        else:
            raw_parameter = str(raw_param or "")
        parameter = POLLUTANT_NAME_MAP.get(raw_parameter.lower().strip(), raw_parameter.lower().strip())

        raw_value = rec.get("value")
        raw_unit = (
            rec.get("_sensor_units")
            or rec.get("unit", "µg/m³")
            or "µg/m³"
        )
        value_ugm3 = _to_ugm3(raw_value, raw_unit, parameter)

        # ── Timestamp ────────────────────────────────────────────────────────
        # v3 sensor format: period.datetimeFrom.utc
        # Legacy format:    date.utc  or  measured_at
        period = rec.get("period", {}) or {}
        datetime_from = period.get("datetimeFrom", {}) or {}
        raw_ts = (
            datetime_from.get("utc")
            or (rec.get("date", {}) or {}).get("utc")
            or rec.get("measured_at")
        )
        ts_utc = _parse_timestamp_utc(raw_ts)
        ts_ist = _utc_to_ist(ts_utc)

        city_raw = rec.get("_city") or loc_meta.get("city") or rec.get("city", "")
        station_raw = loc_meta.get("name") or rec.get("location", "")

        country_val = loc_meta.get("country")
        if isinstance(country_val, dict):
            country_str = country_val.get("code") or country_val.get("name") or "IN"
        else:
            country_str = str(country_val) if country_val else "IN"

        rows.append({
            "raw_id": None,   # will be set after inserting into raw.openaq_measurements
            "extraction_date": rec.get("_extraction_date"),
            "location_id": loc_meta.get("id") or rec.get("locationId"),
            "station_name": _clean_station_name(station_raw),
            "city": _clean_city_name(city_raw),
            "country": country_str,
            "latitude": coords.get("latitude") or rec.get("coordinates", {}).get("latitude"),
            "longitude": coords.get("longitude") or rec.get("coordinates", {}).get("longitude"),
            "parameter": parameter,
            "value_ugm3": value_ugm3,
            "measured_at_utc": ts_utc,
            "measured_at_ist": ts_ist,
        })

    df = pd.DataFrame(rows)
    # Drop rows where critical fields are missing
    before = len(df)
    df = df.dropna(subset=["location_id", "parameter", "measured_at_utc", "value_ugm3"])
    dropped = before - len(df)
    if dropped > 0:
        logger.info("Dropped %d records with missing critical fields during normalization", dropped)

    df["extraction_date"] = pd.to_datetime(df["extraction_date"]).dt.date
    df["location_id"] = df["location_id"].astype(int)

    logger.info("Normalized %d measurement records", len(df))
    return df


def normalize_weather(raw_records: list[dict]) -> pd.DataFrame:
    """
    Normalize raw Open-Meteo weather records into a staging-ready DataFrame.

    Parameters
    ----------
    raw_records : list of dicts (each representing one hourly observation)

    Returns
    -------
    pd.DataFrame with columns matching staging.weather schema
    """
    if not raw_records:
        return pd.DataFrame()

    df = pd.DataFrame(raw_records)

    # Normalize timestamps (Open-Meteo returns IST timestamps as strings)
    df["measured_at_ist"] = pd.to_datetime(df["measured_at_ist"], utc=False)
    df["measured_at_ist"] = df["measured_at_ist"].dt.tz_localize("Asia/Kolkata", ambiguous="infer")
    df["measured_at_utc"] = df["measured_at_ist"].dt.tz_convert("UTC")

    # Normalize city names
    df["city"] = df["city"].apply(_clean_city_name)

    # Drop rows with missing timestamps
    before = len(df)
    df = df.dropna(subset=["city", "measured_at_utc"])
    if before - len(df) > 0:
        logger.info("Dropped %d weather records with missing city/timestamp", before - len(df))

    # Keep only columns matching staging.weather schema
    staging_columns = [
        "city",
        "measured_at_utc",
        "measured_at_ist",
        "temperature_2m",
        "relative_humidity_2m",
        "wind_speed_10m",
        "wind_direction_10m",
        "precipitation",
        "surface_pressure",
        "cloud_cover",
    ]
    for col in staging_columns:
        if col not in df.columns:
            df[col] = None
    if "raw_id" not in df.columns:
        df["raw_id"] = None
    df = df[["raw_id"] + staging_columns]

    logger.info("Normalized %d weather records", len(df))
    return df
