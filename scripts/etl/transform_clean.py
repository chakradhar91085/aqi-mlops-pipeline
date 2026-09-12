import logging

import pandas as pd
from sqlalchemy import text

from db import get_engine

logger = logging.getLogger(__name__)

# ppb -> µg/m³ at 25°C / 1 atm (µg/m³ = ppb * molecular_weight / 24.45)
PPB_TO_UGM3 = {
    "co": 1.145,
    "no2": 1.882,
    "so2": 2.620,
    "o3": 1.963,
}


def _to_std_units(pollutant: str, value: float, unit: str) -> tuple[float, str]:
    if value is None or unit == "µg/m³":
        return value, unit
    if unit == "ppb" and pollutant in PPB_TO_UGM3:
        return value * PPB_TO_UGM3[pollutant], "µg/m³"
    return value, unit


def transform_clean(run_date: str) -> None:
    engine = get_engine()

    aq = pd.read_sql(
        text("SELECT * FROM staging.air_quality_observations WHERE observed_at::date = :run_date"),
        engine,
        params={"run_date": run_date},
    )
    weather = pd.read_sql(
        text("SELECT * FROM staging.weather_observations WHERE observed_at::date = :run_date"),
        engine,
        params={"run_date": run_date},
    )

    if aq.empty:
        logger.info("No staged air-quality rows for %s, skipping clean load", run_date)
        return

    aq = aq.drop_duplicates(subset=["station_id", "pollutant", "observed_at"])
    aq["pollutant"] = aq["pollutant"].str.lower()
    aq[["value", "unit_std"]] = aq.apply(
        lambda r: pd.Series(_to_std_units(r["pollutant"], r["value"], r["unit"])), axis=1
    )
    aq["observed_at"] = pd.to_datetime(aq["observed_at"], utc=True)

    if not weather.empty:
        weather["observed_at"] = pd.to_datetime(weather["observed_at"], utc=True)
        weather_sorted = weather.sort_values("observed_at")
        aq = pd.merge_asof(
            aq.sort_values("observed_at"),
            weather_sorted[["observed_at", "temperature_c", "humidity_pct", "wind_speed_ms", "precipitation_mm"]],
            on="observed_at",
            direction="nearest",
            tolerance=pd.Timedelta("1h"),
        )
    else:
        for col in ["temperature_c", "humidity_pct", "wind_speed_ms", "precipitation_mm"]:
            aq[col] = None

    clean_df = aq[[
        "station_id", "station_name", "city", "latitude", "longitude",
        "pollutant", "value", "unit_std", "observed_at",
        "temperature_c", "humidity_pct", "wind_speed_ms", "precipitation_mm",
    ]].rename(columns={"value": "value_std"})

    with engine.begin() as conn:
        for _, row in clean_df.iterrows():
            conn.execute(
                text(
                    """
                    INSERT INTO clean.air_quality (
                        station_id, station_name, city, latitude, longitude,
                        pollutant, value_std, unit_std, observed_at,
                        temperature_c, humidity_pct, wind_speed_ms, precipitation_mm
                    ) VALUES (
                        :station_id, :station_name, :city, :latitude, :longitude,
                        :pollutant, :value_std, :unit_std, :observed_at,
                        :temperature_c, :humidity_pct, :wind_speed_ms, :precipitation_mm
                    )
                    ON CONFLICT (station_id, pollutant, observed_at) DO UPDATE SET
                        value_std = EXCLUDED.value_std,
                        temperature_c = EXCLUDED.temperature_c,
                        humidity_pct = EXCLUDED.humidity_pct,
                        wind_speed_ms = EXCLUDED.wind_speed_ms,
                        precipitation_mm = EXCLUDED.precipitation_mm
                    """
                ),
                row.to_dict(),
            )

    logger.info("Upserted %s clean rows for %s", len(clean_df), run_date)
