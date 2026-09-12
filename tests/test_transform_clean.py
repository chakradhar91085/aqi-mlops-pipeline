import pytest

from etl.transform_clean import _to_std_units


class TestToStdUnits:
    def test_ugm3_passthrough(self):
        value, unit = _to_std_units("pm25", 50.0, "µg/m³")
        assert value == 50.0
        assert unit == "µg/m³"

    def test_co_ppb_converted(self):
        value, unit = _to_std_units("co", 100.0, "ppb")
        assert unit == "µg/m³"
        assert value == pytest.approx(100.0 * 1.145)

    def test_no2_ppb_converted(self):
        value, unit = _to_std_units("no2", 50.0, "ppb")
        assert value == pytest.approx(50.0 * 1.882)

    def test_so2_ppb_converted(self):
        value, unit = _to_std_units("so2", 20.0, "ppb")
        assert value == pytest.approx(20.0 * 2.620)

    def test_none_value_passthrough(self):
        value, unit = _to_std_units("co", None, "ppb")
        assert value is None

    def test_unconvertible_unit_passthrough_unchanged(self):
        # No known factor for this pollutant/unit combo -> leave as-is rather
        # than silently guessing (matches the exact bug class found in the
        # reference project, where an unmapped unit fell through to an
        # implicit "assume µg/m³" default).
        value, unit = _to_std_units("pm25", 10.0, "ppm")
        assert value == 10.0
        assert unit == "ppm"
