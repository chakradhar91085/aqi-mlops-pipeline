from etl.validate_and_stage import POLLUTANT_RANGES, _is_valid


class TestIsValid:
    def test_pm25_within_range(self):
        assert _is_valid("pm25", 50.0) is True

    def test_pm25_negative_rejected(self):
        assert _is_valid("pm25", -1.0) is False

    def test_co_below_tightened_floor_rejected(self):
        # Regression test for the real CO anomaly this pipeline caught: a
        # near-zero reading (e.g. 0.88, mislabeled ppb) must be rejected by
        # the tightened floor of 100, not pass silently like it did before.
        assert _is_valid("co", 0.88) is False

    def test_co_above_floor_accepted(self):
        assert _is_valid("co", 500.0) is True

    def test_unknown_pollutant_rejected(self):
        assert _is_valid("xyz", 10.0) is False

    def test_none_value_rejected(self):
        assert _is_valid("pm25", None) is False

    def test_all_target_pollutants_have_ranges(self):
        for pollutant in ["pm25", "pm10", "no2", "so2", "o3", "co"]:
            assert pollutant in POLLUTANT_RANGES
