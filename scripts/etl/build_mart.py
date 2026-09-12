import logging

import pandas as pd
from sqlalchemy import text

from db import get_engine
from etl.aqi_calculator import calculate_aqi

logger = logging.getLogger(__name__)


def build_mart(run_date: str) -> None:
    engine = get_engine()

    with engine.begin() as conn:
        conn.execute(
            text(
                """
                INSERT INTO mart.station_daily_pollutant (
                    station_id, city, pollutant, obs_date,
                    avg_value, min_value, max_value, observation_count
                )
                SELECT
                    station_id, city, pollutant, observed_at::date AS obs_date,
                    AVG(value_std), MIN(value_std), MAX(value_std), COUNT(*)
                FROM clean.air_quality
                WHERE observed_at::date = :run_date
                GROUP BY station_id, city, pollutant, observed_at::date
                ON CONFLICT (station_id, pollutant, obs_date) DO UPDATE SET
                    avg_value = EXCLUDED.avg_value,
                    min_value = EXCLUDED.min_value,
                    max_value = EXCLUDED.max_value,
                    observation_count = EXCLUDED.observation_count
                """
            ),
            {"run_date": run_date},
        )

        city_pollutants = pd.read_sql(
            text(
                "SELECT city, pollutant, avg_value FROM mart.station_daily_pollutant "
                "WHERE obs_date = :run_date"
            ),
            conn,
            params={"run_date": run_date},
        )

        for city, group in city_pollutants.groupby("city"):
            concentrations = dict(zip(group["pollutant"], group["avg_value"]))
            result = calculate_aqi(concentrations)

            conn.execute(
                text(
                    """
                    INSERT INTO mart.city_daily_aqi (city, obs_date, avg_aqi, aqi_category, dominant_pollutant)
                    VALUES (:city, :obs_date, :avg_aqi, :aqi_category, :dominant_pollutant)
                    ON CONFLICT (city, obs_date) DO UPDATE SET
                        avg_aqi = EXCLUDED.avg_aqi,
                        aqi_category = EXCLUDED.aqi_category,
                        dominant_pollutant = EXCLUDED.dominant_pollutant
                    """
                ),
                {
                    "city": city,
                    "obs_date": run_date,
                    "avg_aqi": result.aqi,
                    "aqi_category": result.category,
                    "dominant_pollutant": result.dominant_pollutant,
                },
            )

    logger.info("Built mart tables for %s", run_date)
