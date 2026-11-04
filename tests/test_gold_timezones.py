"""IST day/hour attribution in the gold layer must not depend on the session timezone."""

import numpy as np
import pandas as pd
import pytest

from etl.aqi_calculator import EPA_BREAKPOINTS, sub_index
from etl.gold_layer import build_hourly_station_aqi, build_weather_aqi_daily
from etl.normalize import normalize_measurements


@pytest.mark.parametrize("parameter", EPA_BREAKPOINTS)
def test_sub_index_has_no_gaps_and_never_decreases(parameter):
    top = EPA_BREAKPOINTS[parameter][-1].c_hi
    values = [sub_index(parameter, float(c)) for c in np.linspace(0, top + 1, 20001)]
    assert None not in values
    assert all(a <= b for a, b in zip(values, values[1:]))


def test_values_in_breakpoint_gaps_truncate_to_lower_band():
    assert sub_index("pm25", 12.04) == pytest.approx(50)
    assert sub_index("pm10", 54.5) == pytest.approx(50)

# 19:00 UTC on the 1st is 00:30 IST on the 2nd.
INSTANT = pd.Timestamp("2026-09-01 19:00", tz="UTC")
SESSION_TIMEZONES = ("UTC", "Asia/Kolkata")


def test_hourly_keys_same_for_any_session_timezone():
    for tz in SESSION_TIMEZONES:
        read_back = INSTANT.tz_convert(tz)
        df = pd.DataFrame([{
            "location_id": 1, "station_name": "S", "city": "Delhi", "parameter": "pm25",
            "value_ugm3": 50.0, "measured_at_utc": read_back, "measured_at_ist": read_back,
        }])
        row = build_hourly_station_aqi(df).iloc[0]
        assert row["hour_utc"] == INSTANT, tz
        assert row["hour_ist"].strftime("%Y-%m-%d %H:%M") == "2026-09-02 00:00", tz


def test_weather_day_is_ist_for_any_session_timezone():
    daily = pd.DataFrame([{"city": "Delhi", "date_ist": pd.Timestamp("2026-09-02").date(),
                           "aqi": 100, "aqi_category": "Moderate"}])
    for tz in SESSION_TIMEZONES:
        weather = pd.DataFrame([{
            "city": "Delhi", "measured_at_ist": INSTANT.tz_convert(tz), "temperature_2m": 30.0,
            "relative_humidity_2m": 50.0, "wind_speed_10m": 5.0, "precipitation": 0.0,
        }])
        assert len(build_weather_aqi_daily(daily, weather)) == 1, tz


def test_normalized_measurement_lands_on_ist_day():
    df = normalize_measurements([{
        "value": 50.0, "_parameter": "pm25", "_city": "Delhi",
        "_location_meta": {"id": 1, "name": "S"}, "_extraction_date": "2026-09-01",
        "period": {"datetimeFrom": {"utc": "2026-09-01T19:00:00Z"}},
    }])
    assert df.iloc[0]["measured_at_ist"] == INSTANT
    assert build_hourly_station_aqi(df).iloc[0]["hour_ist"].day == 2
