from etl.aqi_calculator import aqi_category, calculate_aqi, sub_index


class TestAqiCategory:
    def test_good(self):
        assert aqi_category(0) == "Good"
        assert aqi_category(50) == "Good"

    def test_moderate(self):
        assert aqi_category(51) == "Moderate"
        assert aqi_category(100) == "Moderate"

    def test_hazardous(self):
        assert aqi_category(301) == "Hazardous"
        assert aqi_category(500) == "Hazardous"

    def test_above_500_still_hazardous(self):
        assert aqi_category(600) == "Hazardous"

    def test_none(self):
        assert aqi_category(None) == "Unknown"


class TestSubIndex:
    def test_pm25_good(self):
        result = sub_index("pm25", 5.0)
        assert result is not None
        assert 0 <= result <= 50

    def test_pm25_unhealthy(self):
        result = sub_index("pm25", 100.0)
        assert 151 <= result <= 200

    def test_pm25_above_max_breakpoint_caps_at_500(self):
        assert sub_index("pm25", 600.0) == 500.0

    def test_negative_value_invalid(self):
        assert sub_index("pm25", -1.0) is None

    def test_zero_is_valid(self):
        assert sub_index("pm25", 0.0) == 0.0

    def test_none_concentration(self):
        assert sub_index("pm25", None) is None

    def test_unknown_pollutant(self):
        assert sub_index("xyz", 50.0) is None

    def test_breakpoint_boundary_pm25(self):
        # At C_lo=0.0, AQI should be exactly 0
        assert sub_index("pm25", 0.0) == 0.0
        # At C_lo=12.1 (start of Moderate), AQI should be close to 51
        assert abs(sub_index("pm25", 12.1) - 51.0) < 2


class TestCalculateAqi:
    def test_single_pollutant(self):
        result = calculate_aqi({"pm25": 10.0})
        assert result.dominant_pollutant == "pm25"
        assert result.category == "Good"

    def test_max_sub_index_wins(self):
        # pm25=250 (Very Unhealthy), no2=30 (Good) -> overall driven by pm25
        result = calculate_aqi({"pm25": 250.0, "no2": 30.0})
        assert result.dominant_pollutant == "pm25"
        assert result.aqi >= 201

    def test_all_none_returns_unknown(self):
        result = calculate_aqi({"pm25": None, "pm10": None})
        assert result.aqi is None
        assert result.category == "Unknown"
        assert result.dominant_pollutant is None

    def test_mixed_none_and_valid(self):
        result = calculate_aqi({"pm25": None, "pm10": 80.0})
        assert result.dominant_pollutant == "pm10"

    def test_empty_dict(self):
        result = calculate_aqi({})
        assert result.aqi is None

    def test_co_ppb_mislabeled_as_ugm3_would_read_near_zero(self):
        # Regression guard for the exact CO unit-mismatch bug found in this
        # project (see docs/data_dictionary.md "Known caveat"): a near-zero
        # concentration must map to sub-index 0, not silently disappear or
        # error, so a mislabeled reading is visibly implausible rather than
        # hidden inside a plausible-looking average.
        result = calculate_aqi({"co": 0.88})
        assert result.aqi == 0
        assert result.category == "Good"
