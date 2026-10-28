"""
ingestion/openaq_collector.py
──────────────────────────────
Collects air-quality measurements from the OpenAQ v3 REST API.

Target pollutants: PM2.5, PM10, NO2, O3, CO, SO2
Target cities:     Delhi, Mumbai, Bengaluru, Chennai, Kolkata (India)

Raw JSON responses are written to:
    data/raw/openaq/YYYY-MM-DD/<location_id>.json

Reference: https://docs.openaq.org/
"""

from __future__ import annotations

import json
import logging
import os
import time
import uuid
from datetime import date, datetime, timedelta, timezone
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

OPENAQ_BASE_URL = "https://api.openaq.org/v3"
TARGET_POLLUTANTS = ["pm25", "pm10", "no2", "o3", "co", "so2"]
RAW_DATA_DIR = Path(os.getenv("RAW_DATA_DIR", "data/raw/openaq"))

# City → approximate bounding box (lat_min, lon_min, lat_max, lon_max)
CITY_BBOXES: dict[str, tuple[float, float, float, float]] = {
    "Delhi":     (28.40, 76.84, 28.88, 77.35),
    "Mumbai":    (18.89, 72.77, 19.27, 73.02),
    "Bengaluru": (12.83, 77.46, 13.14, 77.78),
    "Chennai":   (12.88, 80.13, 13.23, 80.33),
    "Kolkata":   (22.43, 88.27, 22.68, 88.49),
}

REQUEST_TIMEOUT = 30    # seconds
RATE_LIMIT_DELAY = 0.3  # seconds between API calls (with key, can be faster)
MAX_LOCATIONS_PER_CITY = 5   # cap to keep runtime sane (increase once working)

# Target pollutant parameter names (OpenAQ v3 sensor parameter.name)
TARGET_PARAMETERS = {"pm25", "pm25_mass", "pm10", "no2", "o3", "co", "so2"}


class OpenAQCollector:
    """
    Fetches and stores raw OpenAQ measurements for Indian cities.

    Parameters
    ----------
    extraction_date : date to label the raw files (defaults to today UTC)
    api_key         : optional OpenAQ API key for higher rate limits
    raw_dir         : override default raw landing directory
    """

    def __init__(
        self,
        extraction_date: date | None = None,
        api_key: str | None = None,
        raw_dir: Path | None = None,
    ) -> None:
        self.extraction_date = extraction_date or date.today()
        self.api_key = api_key or os.getenv("OPENAQ_API_KEY", "")
        self.raw_dir = raw_dir or RAW_DATA_DIR
        self.run_id = uuid.uuid4()
        self.ingestion_logger = IngestionLogger()
        self.session = self._build_session()

    # ── HTTP session ──────────────────────────────────────────────────────────

    def _build_session(self) -> requests.Session:
        s = requests.Session()
        headers = {"Accept": "application/json"}
        if self.api_key:
            headers["X-API-Key"] = self.api_key
        s.headers.update(headers)
        return s

    # ── API helpers ───────────────────────────────────────────────────────────

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=2, max=30),
        retry=retry_if_exception_type(requests.RequestException),
        reraise=True,
    )
    def _get(self, endpoint: str, params: dict[str, Any]) -> dict:
        url = f"{OPENAQ_BASE_URL}/{endpoint}"
        response = self.session.get(url, params=params, timeout=REQUEST_TIMEOUT)
        if response.status_code in (401, 403):
            # Not a RequestException on purpose: it must not be retried or swallowed into "0 records",
            # which would let the DAG report success with no data.
            raise PermissionError(
                f"OpenAQ rejected the API key (HTTP {response.status_code}). "
                "Create a new key at https://explore.openaq.org and set OPENAQ_API_KEY in .env, "
                "then restart the Airflow containers."
            )
        response.raise_for_status()
        return response.json()

    # ── Location discovery ────────────────────────────────────────────────────

    def get_locations(self, city: str) -> list[dict]:
        """Return all monitoring stations within a city's bounding box."""
        bbox = CITY_BBOXES.get(city)
        if bbox is None:
            logger.warning("No bounding box defined for city: %s", city)
            return []

        lat_min, lon_min, lat_max, lon_max = bbox
        params = {
            "bbox": f"{lon_min},{lat_min},{lon_max},{lat_max}",
            "limit": 200,
            "page": 1,
        }
        try:
            data = self._get("locations", params)
            locations = data.get("results", [])
            # Sort by datetimeLast descending so recently active stations come first
            def _get_dt(loc: dict) -> str:
                dt = loc.get("datetimeLast")
                return dt.get("utc", "") if isinstance(dt, dict) else ""
            locations.sort(key=_get_dt, reverse=True)
            logger.info("Found %d stations for %s", len(locations), city)
            return locations
        except requests.RequestException as exc:
            logger.error("Failed to fetch locations for %s: %s", city, exc)
            return []

    def select_stations(self, locations: list[dict]) -> list[dict]:
        """
        Stable station set per city: still reporting, >= 5 target sensors, longest history first.
        Recency-sorted picks drift to brand-new low-cost sensors with no history.
        """
        def _utc(loc: dict, key: str, default: str) -> str:
            dt = loc.get(key)
            return (dt.get("utc", "") if isinstance(dt, dict) else "")[:10] or default

        cutoff = (date.today() - timedelta(days=14)).isoformat()
        candidates = [
            l for l in locations
            if _utc(l, "datetimeLast", "") >= cutoff and len(self._extract_target_sensors(l)) >= 5
        ]
        candidates.sort(key=lambda l: (_utc(l, "datetimeFirst", "9999-12-31"), l.get("id", 0)))
        return (candidates or locations)[:MAX_LOCATIONS_PER_CITY]

    # ── Measurement fetch ─────────────────────────────────────────────────────

    def _extract_target_sensors(self, location: dict) -> list[dict]:
        """Return only sensors for target pollutants from a location dict."""
        sensors = location.get("sensors", []) or []
        return [
            s for s in sensors
            if (s.get("parameter", {}).get("name", "").lower() in TARGET_PARAMETERS)
        ]

    def fetch_sensor_measurements(
        self,
        sensor_id: int,
        date_from: str,
        date_to: str,
        limit: int = 500,
    ) -> list[dict]:
        """Fetch measurements for a single sensor via OpenAQ v3 sensor endpoint."""
        params = {
            "datetime_from": date_from,
            "datetime_to":   date_to,
            "limit":         limit,
            "page":          1,
        }
        try:
            data = self._get(f"sensors/{sensor_id}/measurements", params)
            results = data.get("results", [])
            return results
        except requests.RequestException as exc:
            logger.error("Error fetching sensor %d: %s", sensor_id, exc)
            return []

    # ── Main collection run ───────────────────────────────────────────────────

    def collect(self, cities: list[str] | None = None) -> dict[str, int]:
        """
        Collect measurements for all target cities using the OpenAQ v3
        sensor-based endpoints.

        Flow:
          1. GET /v3/locations?bbox=...  → list of locations (each with sensors)
          2. For each location, extract sensors for target pollutants
          3. GET /v3/sensors/{id}/measurements?datetime_from=...&datetime_to=...

        Returns
        -------
        dict : {city: total_measurement_count}
        """
        target_cities = cities or list(CITY_BBOXES.keys())
        date_str  = self.extraction_date.strftime("%Y-%m-%d")
        date_from = f"{date_str}T00:00:00Z"
        date_to   = f"{date_str}T23:59:59Z"

        results_summary: dict[str, int] = {}

        for city in target_cities:
            city_total  = 0
            city_errors = 0
            started     = datetime.now(timezone.utc)

            logger.info("Collecting OpenAQ data for %s on %s", city, date_str)
            locations = self.get_locations(city)

            for loc in self.select_stations(locations):
                loc_id = loc.get("id")
                if not loc_id:
                    continue

                sensors = self._extract_target_sensors(loc)
                if not sensors:
                    logger.debug("No target sensors at location %d (%s)", loc_id, loc.get("name"))
                    continue

                loc_measurements: list[dict] = []
                for sensor in sensors:
                    sensor_id    = sensor["id"]
                    sensor_param = sensor.get("parameter", {}).get("name", "unknown")
                    logger.info(
                        "  Fetching sensor %d (%s) at %s",
                        sensor_id, sensor_param, loc.get("name", loc_id),
                    )
                    measurements = self.fetch_sensor_measurements(sensor_id, date_from, date_to)
                    logger.info("    -> %d records", len(measurements))

                    # Flatten to the shape normalize_measurements() expects
                    for m in measurements:
                        m["_sensor_id"]   = sensor_id
                        m["_parameter"]   = sensor_param
                        m["_sensor_units"]= sensor.get("parameter", {}).get("units", "µg/m³")
                    loc_measurements.extend(measurements)
                    time.sleep(RATE_LIMIT_DELAY)

                if not loc_measurements:
                    continue

                try:
                    self._save_raw(city, loc_id, loc, loc_measurements, date_str)
                    city_total += len(loc_measurements)
                except OSError as exc:
                    logger.error("Failed to save raw file for location %d: %s", loc_id, exc)
                    city_errors += 1

            status = "failed" if city_errors > 0 and city_total == 0 else (
                "partial" if city_errors > 0 else "success"
            )
            self.ingestion_logger.log(
                run_id=str(self.run_id),
                source=f"openaq:{city}",
                extraction_date=self.extraction_date,
                started_at=started,
                completed_at=datetime.now(timezone.utc),
                status=status,
                row_count=city_total,
                rejected_count=0,
                error_message=f"{city_errors} location(s) failed" if city_errors else None,
                file_path=str(self.raw_dir / date_str / city),
            )

            results_summary[city] = city_total
            logger.info("Collected %d measurements for %s", city_total, city)

        return results_summary

    # ── Raw file storage ──────────────────────────────────────────────────────

    def _save_raw(
        self,
        city: str,
        location_id: int,
        location_meta: dict,
        measurements: list[dict],
        date_str: str,
    ) -> Path:
        """Save raw API response as JSON in the landing zone."""
        city_dir = self.raw_dir / date_str / city.replace(" ", "_")
        city_dir.mkdir(parents=True, exist_ok=True)

        payload = {
            "extraction_date": date_str,
            "run_id": str(self.run_id),
            "city": city,
            "location_id": location_id,
            "location_meta": location_meta,
            "measurements": measurements,
            "fetched_at": datetime.now(timezone.utc).isoformat(),
        }

        file_path = city_dir / f"location_{location_id}.json"
        with open(file_path, "w", encoding="utf-8") as f:
            json.dump(payload, f, indent=2, ensure_ascii=False)

        logger.debug("Saved %d records → %s", len(measurements), file_path)
        return file_path

    # ── Load from raw files ───────────────────────────────────────────────────

    def load_raw_files(self, extraction_date: date | None = None) -> list[dict]:
        """
        Load all raw JSON files for a given extraction date.
        Used by the ETL pipeline to read the landing zone.
        """
        target_date = extraction_date or self.extraction_date
        date_str = target_date.strftime("%Y-%m-%d")
        date_dir = self.raw_dir / date_str

        all_records: list[dict] = []
        if not date_dir.exists():
            logger.warning("Raw directory does not exist: %s", date_dir)
            return all_records

        for json_file in date_dir.rglob("*.json"):
            try:
                with open(json_file, encoding="utf-8") as f:
                    data = json.load(f)
                    for measurement in data.get("measurements", []):
                        measurement["_city"] = data.get("city")
                        measurement["_location_meta"] = data.get("location_meta", {})
                        measurement["_run_id"] = data.get("run_id")
                        measurement["_extraction_date"] = data.get("extraction_date")
                        all_records.append(measurement)
            except (json.JSONDecodeError, OSError) as exc:
                logger.error("Failed to load raw file %s: %s", json_file, exc)

        logger.info("Loaded %d raw records from %s", len(all_records), date_dir)
        return all_records


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    collector = OpenAQCollector()
    summary = collector.collect()
    print("Collection summary:", summary)
