"""
US EPA AQI calculation for six criteria pollutants (PM2.5, PM10, NO2, O3, CO, SO2).

Reference: EPA Technical Assistance Document for Reporting the Daily AQI
https://www.airnow.gov/sites/default/files/2020-05/aqi-technical-assistance-document-sept2018.pdf

Replaces the earlier average-across-pollutants placeholder in build_mart.py
(see docs/data_dictionary.md "Known caveat" history) with the real per-pollutant
breakpoint interpolation + max-sub-index methodology.
"""

from __future__ import annotations

from typing import NamedTuple

AQI_CATEGORIES: list[tuple[int, int, str]] = [
    (0, 50, "Good"),
    (51, 100, "Moderate"),
    (101, 150, "Unhealthy for Sensitive Groups"),
    (151, 200, "Unhealthy"),
    (201, 300, "Very Unhealthy"),
    (301, 500, "Hazardous"),
]


def aqi_category(aqi_value: float | None) -> str:
    if aqi_value is None:
        return "Unknown"
    aqi_int = int(round(aqi_value))
    for lo, hi, label in AQI_CATEGORIES:
        if lo <= aqi_int <= hi:
            return label
    return "Hazardous" if aqi_int > 500 else "Unknown"


class Breakpoint(NamedTuple):
    c_lo: float
    c_hi: float
    i_lo: int
    i_hi: int


# All concentrations in µg/m³ (matches clean.air_quality.value_std / unit_std).
EPA_BREAKPOINTS: dict[str, list[Breakpoint]] = {
    "pm25": [
        Breakpoint(0.0, 12.0, 0, 50),
        Breakpoint(12.1, 35.4, 51, 100),
        Breakpoint(35.5, 55.4, 101, 150),
        Breakpoint(55.5, 150.4, 151, 200),
        Breakpoint(150.5, 250.4, 201, 300),
        Breakpoint(250.5, 350.4, 301, 400),
        Breakpoint(350.5, 500.4, 401, 500),
    ],
    "pm10": [
        Breakpoint(0, 54, 0, 50),
        Breakpoint(55, 154, 51, 100),
        Breakpoint(155, 254, 101, 150),
        Breakpoint(255, 354, 151, 200),
        Breakpoint(355, 424, 201, 300),
        Breakpoint(425, 504, 301, 400),
        Breakpoint(505, 604, 401, 500),
    ],
    # NO2 EPA breakpoints are published in ppb; converted here at 1 ppb = 1.882 ug/m3
    # to match this pipeline's already-normalized ug/m3 values.
    "no2": [
        Breakpoint(0.0, 100.5, 0, 50),
        Breakpoint(100.6, 188.9, 51, 100),
        Breakpoint(189.0, 677.1, 101, 150),
        Breakpoint(677.2, 1221.0, 151, 200),
        Breakpoint(1221.1, 2349.6, 201, 300),
        Breakpoint(2349.7, 3099.6, 301, 400),
        Breakpoint(3099.7, 3773.0, 401, 500),
    ],
    "o3": [
        Breakpoint(0.0, 124.7, 0, 50),
        Breakpoint(124.8, 164.3, 51, 100),
        Breakpoint(164.4, 204.0, 101, 150),
        Breakpoint(204.1, 403.8, 151, 200),
        Breakpoint(403.9, 603.6, 201, 300),
        Breakpoint(603.7, 803.4, 301, 400),
        Breakpoint(803.5, 1203.0, 401, 500),
    ],
    "co": [
        Breakpoint(0.0, 5152.5, 0, 50),
        Breakpoint(5152.6, 11450.0, 51, 100),
        Breakpoint(11450.1, 14877.0, 101, 150),
        Breakpoint(14877.1, 17175.0, 151, 200),
        Breakpoint(17175.1, 22900.0, 201, 300),
        Breakpoint(22900.1, 28625.0, 301, 400),
        Breakpoint(28625.1, 57250.0, 401, 500),
    ],
    "so2": [
        Breakpoint(0.0, 91.7, 0, 50),
        Breakpoint(91.8, 196.5, 51, 100),
        Breakpoint(196.6, 484.7, 101, 150),
        Breakpoint(484.8, 797.9, 151, 200),
        Breakpoint(798.0, 1046.7, 201, 300),
        Breakpoint(1046.8, 1309.4, 301, 400),
        Breakpoint(1309.5, 2619.0, 401, 500),
    ],
}


def _interpolate(concentration: float, bp: Breakpoint) -> float:
    return (bp.i_hi - bp.i_lo) / (bp.c_hi - bp.c_lo) * (concentration - bp.c_lo) + bp.i_lo


def sub_index(pollutant: str, concentration: float | None) -> float | None:
    """AQI sub-index (0-500) for one pollutant concentration in µg/m³, or None."""
    if concentration is None or concentration < 0:
        return None

    breakpoints = EPA_BREAKPOINTS.get(pollutant)
    if breakpoints is None:
        return None

    for bp in breakpoints:
        if bp.c_lo <= concentration <= bp.c_hi:
            return _interpolate(concentration, bp)

    if concentration > breakpoints[-1].c_hi:
        return 500.0
    return None


class AQIResult(NamedTuple):
    aqi: int | None
    category: str
    dominant_pollutant: str | None
    sub_indices: dict[str, float | None]


def calculate_aqi(pollutant_concentrations: dict[str, float | None]) -> AQIResult:
    """Overall AQI = max sub-index across pollutants; dominant = pollutant that produced it."""
    sub_indices = {p: sub_index(p, c) for p, c in pollutant_concentrations.items()}
    valid = {p: v for p, v in sub_indices.items() if v is not None}

    if not valid:
        return AQIResult(aqi=None, category="Unknown", dominant_pollutant=None, sub_indices=sub_indices)

    dominant = max(valid, key=lambda p: valid[p])
    overall = int(round(valid[dominant]))
    return AQIResult(
        aqi=overall,
        category=aqi_category(overall),
        dominant_pollutant=dominant,
        sub_indices=sub_indices,
    )
