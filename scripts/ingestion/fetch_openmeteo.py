import json
import logging
import os
from pathlib import Path

import requests

logger = logging.getLogger(__name__)

RAW_DIR = Path("/opt/airflow/data/raw/openmeteo")
BASE_URL = "https://archive-api.open-meteo.com/v1/archive"


def fetch_openmeteo(run_date: str) -> str:
    latitude = os.environ["STATION_LATITUDE"]
    longitude = os.environ["STATION_LONGITUDE"]

    response = requests.get(
        BASE_URL,
        params={
            "latitude": latitude,
            "longitude": longitude,
            "start_date": run_date,
            "end_date": run_date,
            "hourly": "temperature_2m,relative_humidity_2m,precipitation,wind_speed_10m",
            "timezone": "UTC",
        },
        timeout=30,
    )
    response.raise_for_status()
    payload = response.json()

    RAW_DIR.mkdir(parents=True, exist_ok=True)
    out_path = RAW_DIR / f"{run_date}.json"
    out_path.write_text(json.dumps(payload))

    logger.info("Open-Meteo: wrote hourly weather for %s to %s", run_date, out_path)
    return str(out_path)
