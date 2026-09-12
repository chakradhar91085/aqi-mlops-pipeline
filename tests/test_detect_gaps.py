import pandas as pd
import pytest

from etl.detect_gaps import identify_missing_periods


def _df(rows):
    return pd.DataFrame(rows, columns=["station_id", "pollutant", "observed_at"])


class TestIdentifyMissingPeriods:
    def test_empty_input(self):
        result = identify_missing_periods(_df([]))
        assert result.empty

    def test_no_gap_below_threshold(self):
        df = _df([
            {"station_id": "5613", "pollutant": "pm25", "observed_at": pd.Timestamp("2026-09-01T00:00:00Z")},
            {"station_id": "5613", "pollutant": "pm25", "observed_at": pd.Timestamp("2026-09-01T01:00:00Z")},
        ])
        result = identify_missing_periods(df, threshold_hours=2)
        assert result.empty

    def test_detects_gap_above_threshold(self):
        df = _df([
            {"station_id": "5613", "pollutant": "pm25", "observed_at": pd.Timestamp("2026-08-27T00:00:00Z")},
            {"station_id": "5613", "pollutant": "pm25", "observed_at": pd.Timestamp("2026-09-01T00:00:00Z")},
        ])
        result = identify_missing_periods(df, threshold_hours=2)
        assert len(result) == 1
        assert result.iloc[0]["station_id"] == "5613"
        assert result.iloc[0]["pollutant"] == "pm25"
        assert result.iloc[0]["gap_hours"] == pytest.approx(120.0)

    def test_gaps_grouped_independently_per_pollutant(self):
        # A gap in co should not be reported for pm25 at the same station.
        df = _df([
            {"station_id": "5613", "pollutant": "pm25", "observed_at": pd.Timestamp("2026-09-01T00:00:00Z")},
            {"station_id": "5613", "pollutant": "pm25", "observed_at": pd.Timestamp("2026-09-01T01:00:00Z")},
            {"station_id": "5613", "pollutant": "co", "observed_at": pd.Timestamp("2026-09-01T00:00:00Z")},
            {"station_id": "5613", "pollutant": "co", "observed_at": pd.Timestamp("2026-09-05T00:00:00Z")},
        ])
        result = identify_missing_periods(df, threshold_hours=2)
        assert len(result) == 1
        assert result.iloc[0]["pollutant"] == "co"
