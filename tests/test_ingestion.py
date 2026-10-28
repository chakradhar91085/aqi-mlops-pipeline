"""
tests/test_ingestion.py
─────────────────────────
Unit tests for the OpenAQ and Open-Meteo collectors.
Uses pytest-mock to avoid real API calls.
"""

import json
import pytest
from datetime import date
from pathlib import Path
from unittest.mock import patch, MagicMock

from ingestion.openaq_collector import OpenAQCollector
from ingestion.openmeteo_collector import OpenMeteoCollector
from ingestion.ingestion_log import IngestionLogger


# ── IngestionLogger tests ─────────────────────────────────────────────────────

class TestIngestionLogger:
    def test_log_creates_csv(self, tmp_path):
        logger = IngestionLogger(log_dir=tmp_path)
        from datetime import datetime, timezone
        logger.log(
            run_id="test-run-001",
            source="openaq:Delhi",
            extraction_date=date(2026, 9, 1),
            started_at=datetime.now(timezone.utc),
            completed_at=datetime.now(timezone.utc),
            status="success",
            row_count=100,
        )
        log_file = tmp_path / "ingestion_log.csv"
        assert log_file.exists()
        content = log_file.read_text()
        assert "test-run-001" in content
        assert "openaq:Delhi" in content
        assert "success" in content

    def test_log_error_creates_error_csv(self, tmp_path):
        logger = IngestionLogger(log_dir=tmp_path)
        logger.log_error(
            run_id="test-run-001",
            source="validate_measurements",
            record_index=42,
            rejection_reason="out_of_range:pm25 value=9999",
            original_value="9999.0",
        )
        error_file = tmp_path / "error_log.csv"
        assert error_file.exists()
        content = error_file.read_text()
        assert "out_of_range:pm25" in content

    def test_get_summary_returns_list(self, tmp_path):
        logger = IngestionLogger(log_dir=tmp_path)
        from datetime import datetime, timezone
        for i in range(3):
            logger.log(
                run_id=f"run-{i}",
                source="openaq:Delhi",
                extraction_date=date(2026, 9, i + 1),
                started_at=datetime.now(timezone.utc),
                completed_at=datetime.now(timezone.utc),
                status="success",
                row_count=50,
            )
        summary = logger.get_summary()
        assert len(summary) == 3


# ── OpenAQ Collector tests ────────────────────────────────────────────────────

class TestOpenAQCollector:
    def _make_mock_location_response(self):
        return {
            "results": [
                {
                    "id": 9999,
                    "name": "Test Station",
                    "city": "Delhi",
                    "country": "IN",
                    "coordinates": {"latitude": 28.6448, "longitude": 77.2167},
                    "datetimeLast": {"utc": "2026-09-01T23:00:00Z"},
                    "sensors": [
                        {"id": 101, "parameter": {"name": "pm25", "units": "µg/m³"}}
                    ],
                }
            ],
            "meta": {"found": 1},
        }

    def _make_mock_measurements_response(self):
        return {
            "results": [
                {
                    "parameter": "pm25",
                    "value": 55.0,
                    "unit": "µg/m³",
                    "date": {"utc": "2026-09-01T12:00:00Z"},
                    "locationId": 9999,
                    "location": "Test Station",
                },
                {
                    "parameter": "pm10",
                    "value": 80.0,
                    "unit": "µg/m³",
                    "date": {"utc": "2026-09-01T12:00:00Z"},
                    "locationId": 9999,
                    "location": "Test Station",
                },
            ],
            "meta": {"found": 2},
        }

    def test_collect_saves_raw_files(self, tmp_path, mocker):
        """Test that collect() saves raw JSON files to the landing zone."""
        collector = OpenAQCollector(
            extraction_date=date(2026, 9, 1),
            raw_dir=tmp_path / "openaq",
        )
        # Patch out the HTTP calls
        mocker.patch.object(
            collector, "_get",
            side_effect=[
                self._make_mock_location_response(),
                self._make_mock_measurements_response(),
            ]
        )
        mocker.patch.object(collector.ingestion_logger, "log")

        summary = collector.collect(cities=["Delhi"])
        assert "Delhi" in summary
        assert summary["Delhi"] == 2

        # Check file was created
        raw_files = list((tmp_path / "openaq").rglob("*.json"))
        assert len(raw_files) >= 1

    def test_load_raw_files_roundtrip(self, tmp_path, mocker):
        """Test that load_raw_files reads back what collect() wrote."""
        collector = OpenAQCollector(
            extraction_date=date(2026, 9, 1),
            raw_dir=tmp_path / "openaq",
        )
        mocker.patch.object(
            collector, "_get",
            side_effect=[
                self._make_mock_location_response(),
                self._make_mock_measurements_response(),
            ]
        )
        mocker.patch.object(collector.ingestion_logger, "log")

        collector.collect(cities=["Delhi"])
        records = collector.load_raw_files(date(2026, 9, 1))

        assert len(records) == 2
        assert any(r.get("parameter") == "pm25" for r in records)

    def test_city_without_bbox_returns_empty(self, tmp_path, mocker):
        """Cities without a defined bounding box should return []."""
        collector = OpenAQCollector(
            extraction_date=date(2026, 9, 1),
            raw_dir=tmp_path / "openaq",
        )
        mocker.patch.object(collector.ingestion_logger, "log")
        locations = collector.get_locations("NonExistentCity")
        assert locations == []


# ── Open-Meteo Collector tests ────────────────────────────────────────────────

class TestOpenMeteoCollector:
    def _make_mock_weather_response(self):
        return {
            "latitude": 28.6448,
            "longitude": 77.2167,
            "hourly": {
                "time": [
                    "2026-09-01T00:00", "2026-09-01T01:00",
                    "2026-09-01T02:00",
                ],
                "temperature_2m":       [28.5, 27.8, 27.2],
                "relative_humidity_2m": [75.0, 77.0, 79.0],
                "wind_speed_10m":       [12.0, 10.5, 9.8],
                "wind_direction_10m":   [180, 185, 190],
                "precipitation":        [0.0, 0.0, 0.2],
                "surface_pressure":     [1010.0, 1010.2, 1010.1],
                "cloud_cover":          [40.0, 45.0, 50.0],
            },
        }

    def test_collect_saves_raw_files(self, tmp_path, mocker):
        collector = OpenMeteoCollector(
            extraction_date=date(2026, 9, 1),
            raw_dir=tmp_path / "openmeteo",
        )
        mocker.patch(
            "ingestion.openmeteo_collector.requests.get",
            return_value=MagicMock(
                status_code=200,
                json=lambda: self._make_mock_weather_response(),
                raise_for_status=lambda: None,
            ),
        )
        mocker.patch.object(collector.ingestion_logger, "log")

        summary = collector.collect(cities=["Delhi"])
        assert "Delhi" in summary
        assert summary["Delhi"] == 3  # 3 hourly records

    def test_load_raw_files_extracts_records(self, tmp_path, mocker):
        collector = OpenMeteoCollector(
            extraction_date=date(2026, 9, 1),
            raw_dir=tmp_path / "openmeteo",
        )
        mocker.patch(
            "ingestion.openmeteo_collector.requests.get",
            return_value=MagicMock(
                status_code=200,
                json=lambda: self._make_mock_weather_response(),
                raise_for_status=lambda: None,
            ),
        )
        mocker.patch.object(collector.ingestion_logger, "log")

        collector.collect(cities=["Delhi"])
        records = collector.load_raw_files(date(2026, 9, 1))

        assert len(records) == 3
        assert all("temperature_2m" in r for r in records)
        assert all(r["city"] == "Delhi" for r in records)


def test_select_stations_prefers_long_history_active_stations(tmp_path):
    from datetime import date, timedelta

    from ingestion.openaq_collector import OpenAQCollector

    today = date.today()
    recent = (today - timedelta(days=1)).isoformat()
    sensors = [{"parameter": {"name": p}} for p in ("pm25", "pm10", "no2", "o3", "co", "so2")]

    def loc(i, first, last=recent, n=6):
        return {"id": i, "datetimeFirst": {"utc": f"{first}T00:00:00Z"}, "datetimeLast": {"utc": f"{last}T00:00:00Z"},
                "sensors": sensors[:n]}

    locs = [
        loc(1, "2026-08-01"),                          # new low-cost sensor
        loc(2, "2016-02-05"),                          # official, long history
        loc(3, "2018-03-09", last="2022-10-31"),       # dead station
        loc(4, "2018-03-09", n=2),                     # too few target sensors
        loc(5, "2017-09-15"),
    ]
    picked = [l["id"] for l in OpenAQCollector(raw_dir=tmp_path).select_stations(locs)]
    assert picked == [2, 5, 1]


def test_rejected_api_key_fails_loudly_instead_of_collecting_zero_rows(tmp_path):
    collector = OpenAQCollector(extraction_date=date(2026, 9, 1), raw_dir=tmp_path / "openaq", api_key="bad")
    resp = MagicMock(status_code=401)
    with patch.object(collector.session, "get", return_value=resp):
        with pytest.raises(PermissionError, match="API key"):
            collector.get_locations("Delhi")
        with pytest.raises(PermissionError):
            collector.collect(cities=["Delhi"])
