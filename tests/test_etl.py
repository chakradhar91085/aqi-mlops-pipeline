"""
tests/test_etl.py
──────────────────
Unit tests for ETL normalization and validation modules.
"""

import pytest
import pandas as pd
import numpy as np
from datetime import date, timezone

from etl.normalize import (
    normalize_measurements,
    normalize_weather,
    _clean_station_name,
    _clean_city_name,
    _to_ugm3,
)
from etl.validate import (
    validate_measurements,
    validate_weather,
    identify_missing_periods,
    POLLUTANT_RANGES,
)


# ── Normalization tests ───────────────────────────────────────────────────────

class TestStationNameCleaning:
    def test_basic_title_case(self):
        assert _clean_station_name("anand vihar") == "Anand Vihar"

    def test_strips_whitespace(self):
        assert _clean_station_name("  OKHLA PHASE 2  ") == "Okhla Phase 2"

    def test_collapses_spaces(self):
        assert _clean_station_name("Lodi   Garden") == "Lodi Garden"

    def test_none_returns_unknown(self):
        assert _clean_station_name(None) == "Unknown Station"

    def test_empty_returns_unknown(self):
        assert _clean_station_name("") == "Unknown Station"


class TestCityNameCleaning:
    def test_delhi_aliases(self):
        assert _clean_city_name("new delhi") == "Delhi"
        assert _clean_city_name("NCR") == "Delhi"
        assert _clean_city_name("delhi") == "Delhi"

    def test_bangalore_alias(self):
        assert _clean_city_name("bangalore") == "Bengaluru"

    def test_bombay_alias(self):
        assert _clean_city_name("bombay") == "Mumbai"

    def test_calcutta_alias(self):
        assert _clean_city_name("calcutta") == "Kolkata"

    def test_none_returns_unknown(self):
        assert _clean_city_name(None) == "Unknown"


class TestUnitConversion:
    def test_ugm3_passthrough(self):
        assert _to_ugm3(50.0, "µg/m³", "pm25") == pytest.approx(50.0)

    def test_no2_ppb_conversion(self):
        # 1 ppb NO2 ≈ 1.912 µg/m³
        result = _to_ugm3(1.0, "ppb", "no2")
        assert result == pytest.approx(1.912, rel=0.01)

    def test_o3_ppb_conversion(self):
        result = _to_ugm3(1.0, "ppb", "o3")
        assert result == pytest.approx(1.9957, rel=0.01)

    def test_co_ppm_conversion(self):
        result = _to_ugm3(1.0, "ppm", "co")
        assert result == pytest.approx(1145.0, rel=0.01)

    def test_nan_returns_none(self):
        assert _to_ugm3(float("nan"), "µg/m³", "pm25") is None


class TestNormalizeMeasurements:
    def _make_raw_record(self, **overrides):
        base = {
            "parameter": "pm25",
            "value": 50.0,
            "unit": "µg/m³",
            "date": {"utc": "2026-09-01T12:00:00Z"},
            "_city": "Delhi",
            "_extraction_date": "2026-09-01",
            "_run_id": "test-run-1",
            "_location_meta": {
                "id": 1001,
                "name": "Anand Vihar",
                "city": "Delhi",
                "country": "IN",
                "coordinates": {"latitude": 28.6448, "longitude": 77.2167},
            },
        }
        base.update(overrides)
        return base

    def test_basic_normalization(self):
        records = [self._make_raw_record()]
        df = normalize_measurements(records)
        assert len(df) == 1
        assert df.iloc[0]["parameter"] == "pm25"
        assert df.iloc[0]["value_ugm3"] == 50.0
        assert df.iloc[0]["city"] == "Delhi"
        assert df.iloc[0]["station_name"] == "Anand Vihar"

    def test_timestamp_conversion(self):
        records = [self._make_raw_record()]
        df = normalize_measurements(records)
        utc_ts = df.iloc[0]["measured_at_utc"]
        ist_ts = df.iloc[0]["measured_at_ist"]
        # Same instant: stored in TIMESTAMPTZ, an added offset would become a real 5.5h error.
        assert ist_ts == utc_ts
        # IST wall clock = UTC + 5:30
        assert ist_ts.tz_localize(None) - utc_ts.tz_localize(None) == pd.Timedelta("5h30min")

    def test_empty_input(self):
        df = normalize_measurements([])
        assert df.empty

    def test_missing_critical_fields_dropped(self):
        bad_record = self._make_raw_record(value=None)
        records = [bad_record, self._make_raw_record()]
        df = normalize_measurements(records)
        # One valid, one invalid
        assert len(df) == 1


# ── Validation tests ──────────────────────────────────────────────────────────

class TestValidateMeasurements:
    def _make_df(self, rows):
        return pd.DataFrame(rows)

    def test_valid_records_pass(self):
        df = self._make_df([{
            "location_id": 1001,
            "parameter": "pm25",
            "value_ugm3": 50.0,
            "measured_at_utc": pd.Timestamp("2026-09-01 12:00", tz="UTC"),
            "station_name": "Anand Vihar",
            "city": "Delhi",
        }])
        result = validate_measurements(df)
        assert len(result.valid) == 1
        assert len(result.rejected) == 0

    def test_out_of_range_rejected(self):
        df = self._make_df([{
            "location_id": 1001,
            "parameter": "pm25",
            "value_ugm3": 9999.0,  # way above max
            "measured_at_utc": pd.Timestamp("2026-09-01 12:00", tz="UTC"),
            "station_name": "Anand Vihar",
            "city": "Delhi",
        }])
        result = validate_measurements(df)
        assert len(result.valid) == 0
        assert len(result.rejected) == 1

    def test_duplicate_records_removed(self):
        row = {
            "location_id": 1001,
            "parameter": "pm25",
            "value_ugm3": 50.0,
            "measured_at_utc": pd.Timestamp("2026-09-01 12:00", tz="UTC"),
            "station_name": "Anand Vihar",
            "city": "Delhi",
        }
        df = self._make_df([row, row.copy()])
        result = validate_measurements(df)
        assert len(result.valid) == 1

    def test_rejection_rate_calculation(self):
        rows = [
            {"location_id": 1, "parameter": "pm25", "value_ugm3": 50.0,
             "measured_at_utc": pd.Timestamp("2026-09-01 12:00", tz="UTC"),
             "station_name": "A", "city": "Delhi"},
            {"location_id": 2, "parameter": "pm25", "value_ugm3": 9999.0,
             "measured_at_utc": pd.Timestamp("2026-09-01 12:00", tz="UTC"),
             "station_name": "B", "city": "Delhi"},
        ]
        df = self._make_df(rows)
        result = validate_measurements(df)
        assert result.rejection_rate == pytest.approx(50.0, abs=1)

    def test_null_value_rejected(self):
        df = self._make_df([{
            "location_id": 1001,
            "parameter": "pm25",
            "value_ugm3": None,
            "measured_at_utc": pd.Timestamp("2026-09-01 12:00", tz="UTC"),
            "station_name": "Anand Vihar",
            "city": "Delhi",
        }])
        result = validate_measurements(df)
        assert len(result.rejected) == 1


class TestIdentifyMissingPeriods:
    def test_detects_gap(self):
        df = pd.DataFrame({
            "location_id": [1001, 1001, 1001],
            "parameter": ["pm25", "pm25", "pm25"],
            "measured_at_utc": [
                pd.Timestamp("2026-09-01 00:00", tz="UTC"),
                pd.Timestamp("2026-09-01 01:00", tz="UTC"),
                pd.Timestamp("2026-09-01 05:00", tz="UTC"),  # 4-hour gap
            ],
        })
        gaps = identify_missing_periods(df, threshold_hours=2)
        assert len(gaps) == 1
        assert gaps.iloc[0]["gap_hours"] == pytest.approx(4.0)

    def test_no_gap_below_threshold(self):
        df = pd.DataFrame({
            "location_id": [1001, 1001],
            "parameter": ["pm25", "pm25"],
            "measured_at_utc": [
                pd.Timestamp("2026-09-01 00:00", tz="UTC"),
                pd.Timestamp("2026-09-01 01:30", tz="UTC"),  # 1.5h gap < 2h threshold
            ],
        })
        gaps = identify_missing_periods(df, threshold_hours=2)
        assert gaps.empty

    def test_empty_df(self):
        gaps = identify_missing_periods(pd.DataFrame())
        assert gaps.empty
