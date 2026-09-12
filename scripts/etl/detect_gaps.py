import logging

import pandas as pd
from sqlalchemy import text

from db import get_engine

logger = logging.getLogger(__name__)

MISSING_PERIOD_THRESHOLD_HOURS = 2


def identify_missing_periods(
    df: pd.DataFrame,
    threshold_hours: float = MISSING_PERIOD_THRESHOLD_HOURS,
) -> pd.DataFrame:
    """
    Find time gaps larger than threshold_hours per (station_id, pollutant).

    df must have columns: station_id, pollutant, observed_at (sortable timestamps).
    Returns a DataFrame with columns: station_id, pollutant, gap_start, gap_end, gap_hours.
    """
    if df.empty:
        return pd.DataFrame(columns=["station_id", "pollutant", "gap_start", "gap_end", "gap_hours"])

    gaps = []
    threshold = pd.Timedelta(hours=threshold_hours)

    for (station_id, pollutant), group in df.groupby(["station_id", "pollutant"]):
        times = group["observed_at"].sort_values()
        diffs = times.diff()

        for i in range(1, len(times)):
            diff = diffs.iloc[i]
            if pd.isna(diff) or diff <= threshold:
                continue
            gaps.append({
                "station_id": station_id,
                "pollutant": pollutant,
                "gap_start": times.iloc[i - 1],
                "gap_end": times.iloc[i],
                "gap_hours": round(diff.total_seconds() / 3600, 2),
            })

    return pd.DataFrame(gaps, columns=["station_id", "pollutant", "gap_start", "gap_end", "gap_hours"])


def detect_gaps(run_date: str) -> None:
    """
    Scan the full clean.air_quality history (not just run_date) for gaps per
    station/pollutant and record any new ones in clean.missing_periods.
    """
    engine = get_engine()

    history = pd.read_sql(
        text("SELECT station_id, pollutant, observed_at FROM clean.air_quality ORDER BY observed_at"),
        engine,
    )

    gaps = identify_missing_periods(history)
    if gaps.empty:
        logger.info("No missing periods detected as of %s", run_date)
        return

    with engine.begin() as conn:
        for _, row in gaps.iterrows():
            conn.execute(
                text(
                    """
                    INSERT INTO clean.missing_periods (station_id, pollutant, gap_start, gap_end, gap_hours)
                    VALUES (:station_id, :pollutant, :gap_start, :gap_end, :gap_hours)
                    ON CONFLICT (station_id, pollutant, gap_start, gap_end) DO NOTHING
                    """
                ),
                row.to_dict(),
            )

    logger.info("Detected %d missing-period gap(s) as of %s", len(gaps), run_date)
