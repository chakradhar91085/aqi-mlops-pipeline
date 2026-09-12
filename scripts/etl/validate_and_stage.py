import json
import logging
from pathlib import Path

import pandas as pd
from sqlalchemy import text

from db import get_engine

logger = logging.getLogger(__name__)

POLLUTANT_RANGES = {
    "pm25": (0, 1000),
    "pm10": (0, 1000),
    "no2": (0, 2000),
    "so2": (0, 2000),
    "o3": (0, 1000),
    # Ambient CO rarely reads below ~100 in either ppb or ug/m3 (they're close in
    # scale for CO since the ppb->ug/m3 factor is ~1.145) - a reading near zero
    # usually means an upstream unit/scale mismatch rather than genuinely clean air.
    "co": (100, 50000),
}


def _is_valid(pollutant: str, value) -> bool:
    bounds = POLLUTANT_RANGES.get(pollutant)
    if bounds is None or value is None:
        return False
    low, high = bounds
    return low <= value <= high


def _load_openaq(run_date: str) -> pd.DataFrame:
    files = sorted(Path("/opt/airflow/data/raw/openaq").glob(f"{run_date}_*.json"))
    rows = []
    for file in files:
        payload = json.loads(file.read_text())
        for result in payload.get("results", []):
            rows.append({"source": "openaq", **result})
    return pd.DataFrame(rows)


def _load_openmeteo(run_date: str) -> pd.DataFrame:
    file = Path("/opt/airflow/data/raw/openmeteo") / f"{run_date}.json"
    if not file.exists():
        return pd.DataFrame()

    payload = json.loads(file.read_text())
    hourly = payload.get("hourly", {})
    times = hourly.get("time", [])

    return pd.DataFrame({
        "source": "open-meteo",
        "station_id": "weather_station",
        "latitude": payload.get("latitude"),
        "longitude": payload.get("longitude"),
        "temperature_c": hourly.get("temperature_2m", [None] * len(times)),
        "humidity_pct": hourly.get("relative_humidity_2m", [None] * len(times)),
        "wind_speed_ms": hourly.get("wind_speed_10m", [None] * len(times)),
        "precipitation_mm": hourly.get("precipitation", [None] * len(times)),
        "observed_at": times,
    })


def validate_and_stage(run_date: str) -> None:
    engine = get_engine()

    aq_df = _load_openaq(run_date)
    if not aq_df.empty:
        aq_df["is_valid"] = aq_df.apply(lambda r: _is_valid(r["pollutant"], r["value"]), axis=1)
        accepted = aq_df[aq_df["is_valid"]].drop(columns=["is_valid"])
        rejected = aq_df[~aq_df["is_valid"]].drop(columns=["is_valid"])
    else:
        accepted, rejected = pd.DataFrame(), pd.DataFrame()

    weather_df = _load_openmeteo(run_date)

    with engine.begin() as conn:
        if not accepted.empty:
            accepted.to_sql("air_quality_observations", conn, schema="staging", if_exists="append", index=False)
        if not weather_df.empty:
            weather_df.to_sql("weather_observations", conn, schema="staging", if_exists="append", index=False)
        for _, row in rejected.iterrows():
            conn.execute(
                text(
                    "INSERT INTO clean.rejected_records (source_table, raw_payload, reason) "
                    "VALUES ('staging.air_quality_observations', :payload, :reason)"
                ),
                {"payload": row.to_json(), "reason": "value out of expected range or unknown pollutant"},
            )
        conn.execute(
            text(
                "INSERT INTO staging.ingestion_log (source, status, row_count) "
                "VALUES (:source, :status, :row_count)"
            ),
            {"source": "openaq+open-meteo", "status": "success", "row_count": len(accepted) + len(weather_df)},
        )

    logger.info(
        "Staged %s AQ rows (%s rejected) and %s weather rows for %s",
        len(accepted), len(rejected), len(weather_df), run_date,
    )
