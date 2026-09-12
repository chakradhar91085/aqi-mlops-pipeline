import json
import logging
import os
from pathlib import Path

import requests

logger = logging.getLogger(__name__)

RAW_DIR = Path("/opt/airflow/data/raw/openaq")
BASE_URL = "https://api.openaq.org/v3"
TARGET_PARAMETERS = {"pm25", "pm10", "no2", "so2", "o3", "co"}


def _get(session: requests.Session, path: str, **params) -> dict:
    response = session.get(f"{BASE_URL}{path}", params=params, timeout=30)
    response.raise_for_status()
    return response.json()


def fetch_openaq(run_date: str) -> str:
    api_key = os.environ["OPENAQ_API_KEY"]
    location_id = os.environ["OPENAQ_LOCATION_ID"]

    session = requests.Session()
    session.headers.update({"X-API-Key": api_key})

    location = _get(session, f"/locations/{location_id}")["results"][0]
    city = location.get("locality") or os.environ.get("CITY_NAME")
    coordinates = location.get("coordinates") or {}

    sensors_by_parameter: dict[str, list[dict]] = {}
    for sensor in location.get("sensors", []):
        param = sensor["parameter"]["name"]
        if param in TARGET_PARAMETERS:
            sensors_by_parameter.setdefault(param, []).append(sensor)

    records = []
    for param, sensors in sensors_by_parameter.items():
        # A location can have multiple sensor ids per pollutant (different
        # instruments/eras); use whichever one actually has data for this date.
        for sensor in sensors:
            payload = _get(
                session,
                f"/sensors/{sensor['id']}/measurements",
                datetime_from=f"{run_date}T00:00:00Z",
                datetime_to=f"{run_date}T23:59:59Z",
                limit=1000,
            )
            results = payload.get("results", [])
            if not results:
                continue
            for r in results:
                records.append({
                    "station_id": str(location_id),
                    "station_name": location.get("name"),
                    "city": city,
                    "latitude": coordinates.get("latitude"),
                    "longitude": coordinates.get("longitude"),
                    "pollutant": param,
                    "value": r.get("value"),
                    "unit": (r.get("parameter") or {}).get("units"),
                    "observed_at": (r.get("period") or {}).get("datetimeFrom", {}).get("utc"),
                })
            break

    RAW_DIR.mkdir(parents=True, exist_ok=True)
    out_path = RAW_DIR / f"{run_date}_{location_id}.json"
    out_path.write_text(json.dumps({"results": records}))

    logger.info("OpenAQ: wrote %s records to %s", len(records), out_path)
    return str(out_path)
