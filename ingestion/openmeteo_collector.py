"""
ingestion/openmeteo_collector.py
─────────────────────────────────
Collects hourly weather data from the Open-Meteo free API.
No API key required.

Variables collected:
    temperature_2m, relative_humidity_2m, wind_speed_10m,
    wind_direction_10m, precipitation, surface_pressure, cloud_cover

Reference: https://open-meteo.com/en/docs
"""

from __future__ import annotations

import json
import logging
import os
import time
import uuid
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

import requests
from dotenv import load_dotenv
from tenacity import (
    retry,
    stop_after_attempt,
    wait_exponential,
    retry_if_exception_type,
)

from ingestion.ingestion_log import IngestionLogger

load_dotenv()
logger = logging.getLogger(__name__)

# ── Constants ─────────────────────────────────────────────────────────────────

OPEN_METEO_ARCHIVE_URL = "https://archive-api.open-meteo.com/v1/archive"
OPEN_METEO_FORECAST_URL = "https://api.open-meteo.com/v1/forecast"
PROJECT_ROOT = Path(__file__).resolve().parent.parent
_raw_env = os.getenv("RAW_WEATHER_DIR", "data/raw/openmeteo")
RAW_DATA_DIR = Path(_raw_env) if Path(_raw_env).is_absolute() else (PROJECT_ROOT / _raw_env)

WEATHER_VARIABLES = [
    "temperature_2m",
    "relative_humidity_2m",
    "wind_speed_10m",
    "wind_direction_10m",
    "precipitation",
    "surface_pressure",
    "cloud_cover",
]

# City coordinates (lat, lon)
CITY_COORDS: dict[str, tuple[float, float]] = {
    "Delhi":     (28.6448, 77.2167),
    "Mumbai":    (19.0760, 72.8777),
    "Bengaluru": (12.9716, 77.5946),
    "Chennai":   (13.0827, 80.2707),
    "Kolkata":   (22.5726, 88.3639),
}

REQUEST_TIMEOUT = 30
RATE_LIMIT_DELAY = 0.3


class OpenMeteoCollector:
    """
    Fetches hourly weather data from the Open-Meteo Historical Weather API.

    Parameters
    ----------
    extraction_date : the date for which to fetch weather data
    raw_dir         : override default raw landing directory
    """

    def __init__(
        self,
        extraction_date: date | None = None,
        raw_dir: Path | None = None,
    ) -> None:
        self.extraction_date = extraction_date or date.today()
        self.raw_dir = raw_dir or RAW_DATA_DIR
        self.run_id = uuid.uuid4()
        self.ingestion_logger = IngestionLogger()

    # ── API ───────────────────────────────────────────────────────────────────

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=2, max=30),
        retry=retry_if_exception_type(requests.RequestException),
        reraise=True,
    )
    def _fetch_weather(self, city: str, lat: float, lon: float) -> dict[str, Any]:
        """Call Open-Meteo API for one city and one day.

        - For today / yesterday: use forecast API with past_days (archive doesn't
          have these dates yet). The response may span extra days; filtering happens
          in load_raw_files.
        - For older historical dates: use archive API with start_date/end_date.
        """
        date_str = self.extraction_date.strftime("%Y-%m-%d")
        today = date.today()
        days_ago = (today - self.extraction_date).days

        if days_ago <= 2:
            # Forecast API — do NOT mix start_date/end_date with past_days
            url = OPEN_METEO_FORECAST_URL
            params = {
                "latitude": lat,
                "longitude": lon,
                "hourly": ",".join(WEATHER_VARIABLES),
                "timezone": "Asia/Kolkata",
                "wind_speed_unit": "kmh",
                "precipitation_unit": "mm",
                "past_days": max(days_ago, 1),   # at least 1 so today is included
                "forecast_days": 1,
            }
            logger.debug("Using forecast API for %s (%d day(s) ago)", city, days_ago)
        else:
            # Archive API for older historical dates
            url = OPEN_METEO_ARCHIVE_URL
            params = {
                "latitude": lat,
                "longitude": lon,
                "start_date": date_str,
                "end_date": date_str,
                "hourly": ",".join(WEATHER_VARIABLES),
                "timezone": "Asia/Kolkata",
                "wind_speed_unit": "kmh",
                "precipitation_unit": "mm",
            }
            logger.debug("Using archive API for %s (%d days ago)", city, days_ago)

        response = requests.get(url, params=params, timeout=REQUEST_TIMEOUT)
        response.raise_for_status()
        return response.json()


    # ── Main collection run ───────────────────────────────────────────────────

    def collect(self, cities: list[str] | None = None) -> dict[str, int]:
        """
        Collect hourly weather for all target cities.

        Returns
        -------
        dict : {city: hourly_record_count}
        """
        target_cities = cities or list(CITY_COORDS.keys())
        date_str = self.extraction_date.strftime("%Y-%m-%d")
        results_summary: dict[str, int] = {}

        for city in target_cities:
            coords = CITY_COORDS.get(city)
            if coords is None:
                logger.warning("No coordinates for city: %s", city)
                continue

            lat, lon = coords
            started = datetime.now(timezone.utc)
            status = "success"
            row_count = 0
            error_msg = None

            try:
                logger.info("Fetching Open-Meteo weather for %s on %s", city, date_str)
                data = self._fetch_weather(city, lat, lon)

                hourly = data.get("hourly", {})
                times = hourly.get("time", [])
                row_count = len(times)

                self._save_raw(city, data, date_str)
                logger.info("Fetched %d hourly records for %s", row_count, city)

            except requests.RequestException as exc:
                status = "failed"
                error_msg = str(exc)
                logger.error("Failed to fetch weather for %s: %s", city, exc)

            self.ingestion_logger.log(
                run_id=str(self.run_id),
                source=f"openmeteo:{city}",
                extraction_date=self.extraction_date,
                started_at=started,
                completed_at=datetime.now(timezone.utc),
                status=status,
                row_count=row_count,
                rejected_count=0,
                error_message=error_msg,
                file_path=str(self.raw_dir / date_str / f"{city.replace(' ', '_')}.json"),
            )

            results_summary[city] = row_count
            time.sleep(RATE_LIMIT_DELAY)

        return results_summary

    # ── Raw file storage ──────────────────────────────────────────────────────

    def _save_raw(self, city: str, data: dict, date_str: str) -> Path:
        """Persist raw Open-Meteo response to the landing zone."""
        city_dir = self.raw_dir / date_str
        city_dir.mkdir(parents=True, exist_ok=True)

        payload = {
            "extraction_date": date_str,
            "run_id": str(self.run_id),
            "city": city,
            "fetched_at": datetime.now(timezone.utc).isoformat(),
            "data": data,
        }

        file_path = city_dir / f"{city.replace(' ', '_')}.json"
        with open(file_path, "w", encoding="utf-8") as f:
            json.dump(payload, f, indent=2, ensure_ascii=False)

        logger.debug("Saved weather data → %s", file_path)
        return file_path

    # ── Load from raw files ───────────────────────────────────────────────────

    def load_raw_files(self, extraction_date: date | None = None) -> list[dict]:
        """Load all raw Open-Meteo JSON files for a given extraction date."""
        target_date = extraction_date or self.extraction_date
        date_str = target_date.strftime("%Y-%m-%d")
        date_dir = self.raw_dir / date_str

        all_records: list[dict] = []
        if not date_dir.exists():
            logger.warning("Raw weather directory does not exist: %s", date_dir)
            return all_records

        for json_file in date_dir.glob("*.json"):
            try:
                with open(json_file, encoding="utf-8") as f:
                    payload = json.load(f)

                city = payload.get("city", "Unknown")
                data = payload.get("data", {})
                hourly = data.get("hourly", {})
                times = hourly.get("time", [])

                for i, ts in enumerate(times):
                    record = {
                        "city": city,
                        "measured_at_ist": ts,
                        "run_id": payload.get("run_id"),
                        "extraction_date": payload.get("extraction_date"),
                        "latitude": data.get("latitude"),
                        "longitude": data.get("longitude"),
                    }
                    for var in WEATHER_VARIABLES:
                        values = hourly.get(var, [])
                        record[var] = values[i] if i < len(values) else None
                    all_records.append(record)

            except (json.JSONDecodeError, OSError) as exc:
                logger.error("Failed to load weather file %s: %s", json_file, exc)

        logger.info("Loaded %d raw weather records from %s", len(all_records), date_dir)
        return all_records


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    collector = OpenMeteoCollector()
    summary = collector.collect()
    print("Weather collection summary:", summary)
