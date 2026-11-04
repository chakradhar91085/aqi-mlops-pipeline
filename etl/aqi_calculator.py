"""
etl/aqi_calculator.py
──────────────────────
US EPA AQI calculation for six criteria pollutants:
  PM2.5 (24-hr), PM10 (24-hr), NO2 (1-hr), O3 (8-hr), CO (8-hr), SO2 (1-hr)

Reference:
  EPA Technical Assistance Document for Reporting the Daily AQI
  https://www.airnow.gov/sites/default/files/2020-05/aqi-technical-assistance-document-sept2018.pdf

AQI Categories (US EPA):
  0–50    Good
  51–100  Moderate
  101–150 Unhealthy for Sensitive Groups
  151–200 Unhealthy
  201–300 Very Unhealthy
  301–500 Hazardous
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import NamedTuple

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)

# ── AQI Category thresholds ───────────────────────────────────────────────────

AQI_CATEGORIES: list[tuple[int, int, str]] = [
    (0,   50,  "Good"),
    (51,  100, "Moderate"),
    (101, 150, "Unhealthy for Sensitive Groups"),
    (151, 200, "Unhealthy"),
    (201, 300, "Very Unhealthy"),
    (301, 500, "Hazardous"),
]


def aqi_category(aqi_value: float | int | None) -> str:
    """Return the AQI category string for a given AQI value."""
    if aqi_value is None or np.isnan(aqi_value):
        return "Unknown"
    aqi_int = int(round(aqi_value))
    for lo, hi, label in AQI_CATEGORIES:
        if lo <= aqi_int <= hi:
            return label
    return "Hazardous" if aqi_int > 500 else "Unknown"


# ── EPA Breakpoint Tables ─────────────────────────────────────────────────────
# Each row: (C_lo, C_hi, I_lo, I_hi)
# C = concentration in pollutant-specific units
# I = corresponding AQI index values

class Breakpoint(NamedTuple):
    c_lo: float
    c_hi: float
    i_lo: int
    i_hi: int


EPA_BREAKPOINTS: dict[str, list[Breakpoint]] = {
    # PM2.5 (µg/m³, 24-hr average, precision 0.1)
    "pm25": [
        Breakpoint(0.0,   12.0,  0,   50),
        Breakpoint(12.1,  35.4,  51,  100),
        Breakpoint(35.5,  55.4,  101, 150),
        Breakpoint(55.5,  150.4, 151, 200),
        Breakpoint(150.5, 250.4, 201, 300),
        Breakpoint(250.5, 350.4, 301, 400),
        Breakpoint(350.5, 500.4, 401, 500),
    ],
    # PM10 (µg/m³, 24-hr average, integer)
    "pm10": [
        Breakpoint(0,    54,   0,   50),
        Breakpoint(55,   154,  51,  100),
        Breakpoint(155,  254,  101, 150),
        Breakpoint(255,  354,  151, 200),
        Breakpoint(355,  424,  201, 300),
        Breakpoint(425,  504,  301, 400),
        Breakpoint(505,  604,  401, 500),
    ],
    # NO2 (µg/m³, 1-hr average — converted from ppb: 1 ppb = 1.912 µg/m³)
    # EPA breakpoints in ppb × 1.912
    "no2": [
        Breakpoint(0.0,   100.5,  0,   50),
        Breakpoint(100.6, 188.9,  51,  100),
        Breakpoint(189.0, 677.1,  101, 150),
        Breakpoint(677.2, 1221.0, 151, 200),
        Breakpoint(1221.1,2349.6, 201, 300),
        Breakpoint(2349.7,3099.6, 301, 400),
        Breakpoint(3099.7,3773.0, 401, 500),
    ],
    # O3 (µg/m³, 8-hr average — converted from ppm: 1 ppm = 1995.7 µg/m³)
    "o3": [
        Breakpoint(0.0,    124.7,  0,   50),
        Breakpoint(124.8,  164.3,  51,  100),
        Breakpoint(164.4,  204.0,  101, 150),
        Breakpoint(204.1,  403.8,  151, 200),
        Breakpoint(403.9,  603.6,  201, 300),
        Breakpoint(603.7,  803.4,  301, 400),
        Breakpoint(803.5,  1203.0, 401, 500),
    ],
    # CO (µg/m³, 8-hr average — converted from ppm: 1 ppm = 1145 µg/m³)
    "co": [
        Breakpoint(0.0,     5152.5,  0,   50),
        Breakpoint(5152.6,  11450.0, 51,  100),
        Breakpoint(11450.1, 14877.0, 101, 150),
        Breakpoint(14877.1, 17175.0, 151, 200),
        Breakpoint(17175.1, 22900.0, 201, 300),
        Breakpoint(22900.1, 28625.0, 301, 400),
        Breakpoint(28625.1, 57250.0, 401, 500),
    ],
    # SO2 (µg/m³, 1-hr average — converted from ppb: 1 ppb = 2.620 µg/m³)
    "so2": [
        Breakpoint(0.0,    91.7,   0,   50),
        Breakpoint(91.8,   196.5,  51,  100),
        Breakpoint(196.6,  484.7,  101, 150),
        Breakpoint(484.8,  797.9,  151, 200),
        Breakpoint(798.0,  1046.7, 201, 300),
        Breakpoint(1046.8, 1309.4, 301, 400),
        Breakpoint(1309.5, 2619.0, 401, 500),
    ],
}


# ── Core AQI formula ──────────────────────────────────────────────────────────

def _linear_interpolation(
    concentration: float,
    bp: Breakpoint,
) -> float:
    """Apply EPA linear interpolation formula for sub-index calculation."""
    return (bp.i_hi - bp.i_lo) / (bp.c_hi - bp.c_lo) * (concentration - bp.c_lo) + bp.i_lo


def sub_index(parameter: str, concentration: float | None) -> float | None:
    """
    Calculate the AQI sub-index for a single pollutant concentration.

    Parameters
    ----------
    parameter     : pollutant name ('pm25', 'pm10', 'no2', 'o3', 'co', 'so2')
    concentration : measured concentration in µg/m³

    Returns
    -------
    float | None : AQI sub-index (0–500) or None if not calculable
    """
    if concentration is None or np.isnan(concentration) or concentration < 0:
        return None

    breakpoints = EPA_BREAKPOINTS.get(parameter)
    if breakpoints is None:
        return None

    # Above the highest breakpoint → cap at 500
    if concentration > breakpoints[-1].c_hi:
        logger.debug(
            "Concentration %.2f for %s exceeds max breakpoint — capping AQI at 500",
            concentration, parameter,
        )
        return 500.0

    # Bands leave gaps (PM10 54 → 55, PM2.5 12.0 → 12.1). EPA truncates to the table's precision
    # first, which puts a gap value at the top of the lower band; dropping it would understate AQI.
    bp = next(b for b in reversed(breakpoints) if b.c_lo <= concentration)
    return _linear_interpolation(min(concentration, bp.c_hi), bp)


@dataclass
class AQIResult:
    """Container for AQI calculation output."""
    aqi: int | None
    category: str
    dominant_pollutant: str | None
    sub_indices: dict[str, float | None]


def calculate_aqi(pollutant_concentrations: dict[str, float | None]) -> AQIResult:
    """
    Calculate overall AQI from a dict of pollutant concentrations.

    Overall AQI = maximum sub-index across all pollutants.
    Dominant pollutant = the one with the highest sub-index.

    Parameters
    ----------
    pollutant_concentrations : {'pm25': 45.2, 'pm10': 80.0, ...}

    Returns
    -------
    AQIResult
    """
    sub_indices: dict[str, float | None] = {}
    for param, concentration in pollutant_concentrations.items():
        sub_indices[param] = sub_index(param, concentration)

    valid_indices = {k: v for k, v in sub_indices.items() if v is not None}
    if not valid_indices:
        return AQIResult(aqi=None, category="Unknown", dominant_pollutant=None, sub_indices=sub_indices)

    dominant = max(valid_indices, key=lambda k: valid_indices[k])
    overall_aqi = int(round(valid_indices[dominant]))

    return AQIResult(
        aqi=overall_aqi,
        category=aqi_category(overall_aqi),
        dominant_pollutant=dominant,
        sub_indices=sub_indices,
    )


# ── DataFrame-level AQI calculation ──────────────────────────────────────────

def calculate_aqi_for_df(df: pd.DataFrame) -> pd.DataFrame:
    """
    Add AQI columns to a DataFrame that has one column per pollutant.

    Expected columns (all in µg/m³): pm25, pm10, no2, o3, co, so2
    Adds columns: aqi, aqi_category, dominant_pollutant

    Parameters
    ----------
    df : DataFrame with pollutant average columns

    Returns
    -------
    pd.DataFrame with AQI columns added
    """
    pollutant_cols = [p for p in EPA_BREAKPOINTS.keys() if p in df.columns]
    if not pollutant_cols:
        logger.warning("No pollutant columns found in DataFrame for AQI calculation")
        df["aqi"] = None
        df["aqi_category"] = "Unknown"
        df["dominant_pollutant"] = None
        return df

    def _row_aqi(row: pd.Series) -> pd.Series:
        concs = {p: (row[p] if not pd.isna(row[p]) else None) for p in pollutant_cols}
        result = calculate_aqi(concs)
        return pd.Series({
            "aqi": result.aqi,
            "aqi_category": result.category,
            "dominant_pollutant": result.dominant_pollutant,
        })

    aqi_cols = df.apply(_row_aqi, axis=1)
    df = pd.concat([df, aqi_cols], axis=1)
    logger.info("Calculated AQI for %d rows", len(df))
    return df
