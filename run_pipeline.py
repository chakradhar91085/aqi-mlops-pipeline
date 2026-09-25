"""
run_pipeline.py
────────────────
One-click local pipeline runner (no Docker / no Airflow required).

Usage:
    python run_pipeline.py              # run for today
    python run_pipeline.py 2026-09-06   # run for a specific date

Steps:
    1. Test DB connection
    2. Ingest OpenAQ data  → data/raw/openaq/YYYY-MM-DD/
    3. Ingest Open-Meteo   → data/raw/openmeteo/YYYY-MM-DD/
    4. Normalize + Validate
    5. Load cleaned tables
    6. Build gold layer (AQI + analytics tables)

After this finishes, run: streamlit run dashboard/Home.py
"""

from __future__ import annotations

import sys
import logging
from datetime import date, datetime, timedelta
from pathlib import Path

# Make project root importable
ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("run_pipeline")


def run(extraction_date: date) -> None:
    print(f"\n{'='*60}")
    print(f"  AQI Pipeline — {extraction_date}")
    print(f"{'='*60}\n")

    # ── Step 0: Load env ─────────────────────────────────────────
    from dotenv import load_dotenv
    load_dotenv()

    # ── Step 1: Test DB connection ───────────────────────────────
    print("Step 1 / 6 — Testing database connection…")
    from db.db_utils import test_connection
    if not test_connection():
        print("\n❌ ERROR: Cannot connect to PostgreSQL!")
        print("   Make sure PostgreSQL is running and .env is configured correctly.")
        print("   See README.md → Step 2 for setup instructions.")
        sys.exit(1)
    print("   ✅ Database connected\n")

    # ── Step 2: Ingest OpenAQ ────────────────────────────────────
    print("Step 2 / 6 — Ingesting OpenAQ air quality data…")
    from ingestion.openaq_collector import OpenAQCollector
    aq_collector = OpenAQCollector(extraction_date=extraction_date)
    aq_summary = aq_collector.collect()
    total_aq = sum(aq_summary.values())
    for city, count in aq_summary.items():
        status = "✅" if count > 0 else "⚠️ "
        print(f"   {status} {city}: {count} records")
    print(f"   Total: {total_aq} air quality records\n")

    # ── Step 3: Ingest Open-Meteo ────────────────────────────────
    print("Step 3 / 6 — Ingesting Open-Meteo weather data…")
    from ingestion.openmeteo_collector import OpenMeteoCollector
    weather_collector = OpenMeteoCollector(extraction_date=extraction_date)
    weather_summary = weather_collector.collect()
    total_weather = sum(weather_summary.values())
    for city, count in weather_summary.items():
        status = "✅" if count > 0 else "⚠️ "
        print(f"   {status} {city}: {count} hourly records")
    print(f"   Total: {total_weather} weather records\n")

    if total_aq == 0 and total_weather == 0:
        print("❌ No data collected from either source. Check your internet connection.")
        sys.exit(1)
    if total_aq == 0:
        print("   ⚠️  OpenAQ returned 0 records (API key may be missing/invalid).")
        print("      Add OPENAQ_API_KEY to .env and re-run for full AQ data.\n")
    if total_weather == 0:
        print("   ⚠️  Open-Meteo returned 0 records.\n")

    # ── Step 4: Normalize ────────────────────────────────────────
    print("Step 4 / 6 — Normalizing data…")
    from etl.normalize import normalize_measurements, normalize_weather

    raw_aq = aq_collector.load_raw_files(extraction_date)
    raw_weather = weather_collector.load_raw_files(extraction_date)

    df_aq = normalize_measurements(raw_aq)
    df_weather = normalize_weather(raw_weather)
    print(f"   ✅ Normalized: {len(df_aq)} measurements, {len(df_weather)} weather records\n")

    # ── Step 5: Validate and load cleaned ───────────────────────
    print("Step 5 / 6 — Validating and loading cleaned data…")
    import pandas as pd
    from etl.validate import validate_measurements, validate_weather
    from db.db_utils import upsert_dataframe

    aq_result = validate_measurements(df_aq)
    weather_result = validate_weather(df_weather)

    print(f"   Air quality:  {len(aq_result.valid)} valid, "
          f"{len(aq_result.rejected)} rejected "
          f"({aq_result.rejection_rate:.1f}% rejection rate)")
    print(f"   Weather:      {len(weather_result.valid)} valid, "
          f"{len(weather_result.rejected)} rejected")

    if not aq_result.valid.empty:
        aq_result.valid["cleaned_at"] = pd.Timestamp.utcnow()
        aq_result.valid["is_valid"] = True
        # Select only columns that exist in cleaned.air_quality
        aq_cols = [
            "location_id", "station_name", "city", "country",
            "latitude", "longitude", "parameter", "value_ugm3",
            "measured_at_utc", "measured_at_ist",
            "aqi_sub_index", "is_valid", "cleaned_at",
        ]
        aq_to_load = aq_result.valid[[c for c in aq_cols if c in aq_result.valid.columns]].copy()
        aq_to_load = aq_to_load.drop_duplicates(
            subset=["location_id", "parameter", "measured_at_utc"], keep="last"
        )
        upsert_dataframe(
            df=aq_to_load,
            table="air_quality",
            schema="cleaned",
            conflict_columns=["location_id", "parameter", "measured_at_utc"],
            update_columns=[c for c in aq_to_load.columns if c not in ["location_id", "parameter", "measured_at_utc"]],
        )
        print("   ✅ Air quality data loaded to cleaned.air_quality")

    if not weather_result.valid.empty:
        weather_result.valid["cleaned_at"] = pd.Timestamp.utcnow()
        # Select only columns that exist in cleaned.weather
        weather_cols = [
            "city", "measured_at_utc", "measured_at_ist",
            "temperature_2m", "relative_humidity_2m", "wind_speed_10m",
            "wind_direction_10m", "precipitation", "surface_pressure",
            "cloud_cover", "cleaned_at",
        ]
        weather_to_load = weather_result.valid[[c for c in weather_cols if c in weather_result.valid.columns]].copy()
        weather_to_load = weather_to_load.drop_duplicates(
            subset=["city", "measured_at_utc"], keep="last"
        )
        upsert_dataframe(
            df=weather_to_load,
            table="weather",
            schema="cleaned",
            conflict_columns=["city", "measured_at_utc"],
            update_columns=[c for c in weather_to_load.columns if c not in ["city", "measured_at_utc"]],
        )
        print("   ✅ Weather data loaded to cleaned.weather")

    print()

    # ── Step 6: Build gold layer ─────────────────────────────────
    print("Step 6 / 6 — Building analytical gold layer…")
    from etl.gold_layer import build_gold_layer

    gold_results = build_gold_layer(aq_result.valid, weather_result.valid)
    for table, df in gold_results.items():
        count = len(df) if hasattr(df, '__len__') else '?'
        print(f"   ✅ gold.{table}: {count} rows")

    print(f"\n{'='*60}")
    print("  ✅ Pipeline complete!")
    print(f"{'='*60}")
    print("\nNext step: launch the dashboard")
    print("    streamlit run dashboard/Home.py")
    print("    Then open: http://localhost:8501\n")


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1].isdigit():
        # Backfill mode: `python run_pipeline.py 14` runs the last 14 days
        # (oldest first), so trend/rolling-average charts have real history.
        n_days = int(sys.argv[1])
        for offset in range(n_days - 1, -1, -1):
            run(date.today() - timedelta(days=offset))
        sys.exit(0)

    if len(sys.argv) > 1:
        try:
            target_date = datetime.strptime(sys.argv[1], "%Y-%m-%d").date()
        except ValueError:
            print(f"Invalid date '{sys.argv[1]}'. Use format: YYYY-MM-DD")
            sys.exit(1)
    else:
        target_date = date.today()

    run(target_date)
