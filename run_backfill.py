"""
run_backfill.py
────────────────
Bulk historical backfill (needed for Part 2: ML needs months of history).

The daily pipeline calls OpenAQ ~150 times per day; backfilling a year that way hits the
free-tier hourly request cap for ~25 hours. This script instead fetches each sensor's whole
date range from OpenAQ's /hours endpoint (paged), splits the rows into the SAME per-day raw
JSON files the daily collector writes, then runs the normal ETL (normalize -> validate ->
cleaned) per day and rebuilds the gold layer per IST-day window.

Usage:
    python run_backfill.py 2025-10-02 2026-08-30
    python run_backfill.py 2025-10-02 2026-08-30 --skip-fetch     # raw files already on disk
"""

from __future__ import annotations

import argparse
import logging
import sys
import time
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).resolve().parent))

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s — %(message)s", datefmt="%H:%M:%S")
logger = logging.getLogger("run_backfill")

PAGE_LIMIT = 1000
MAX_PAGES = 12          # 12k hourly rows ≈ 500 days per sensor
CALL_DELAY = 1.1        # seconds; stays under OpenAQ's 60 requests/min
RATE_LIMIT_SLEEP = 65


def _d(s: str) -> date:
    return datetime.strptime(s, "%Y-%m-%d").date()


def fetch_sensor_hours(collector, sensor_id: int, start: date, end: date) -> list[dict]:
    """All hourly rows for one sensor between start and end (inclusive), paged."""
    params = {
        "datetime_from": f"{start}T00:00:00Z",
        "datetime_to": f"{end}T23:59:59Z",
        "limit": PAGE_LIMIT,
    }
    rows: list[dict] = []
    for page in range(1, MAX_PAGES + 1):
        data = None
        for _ in range(6):
            try:
                data = collector._get(f"sensors/{sensor_id}/hours", {**params, "page": page})
                break
            except requests.HTTPError as exc:
                if exc.response is not None and exc.response.status_code == 429:
                    logger.warning("429 on sensor %s page %d — sleeping %ds", sensor_id, page, RATE_LIMIT_SLEEP)
                    time.sleep(RATE_LIMIT_SLEEP)
                    continue
                logger.error("sensor %s page %d failed: %s", sensor_id, page, exc)
                return rows
            except requests.RequestException as exc:
                logger.error("sensor %s page %d failed: %s", sensor_id, page, exc)
                return rows
        if data is None:
            return rows
        results = data.get("results", [])
        rows.extend(results)
        time.sleep(CALL_DELAY)
        if len(results) < PAGE_LIMIT:
            break
    return rows


def fetch_openaq_range(start: date, end: date) -> None:
    from ingestion.openaq_collector import CITY_BBOXES, OpenAQCollector

    collector = OpenAQCollector(extraction_date=end)
    for city in CITY_BBOXES:
        started = datetime.now(timezone.utc)
        city_total = 0
        locations = collector.select_stations(collector.get_locations(city))
        for loc in locations:
            loc_id = loc.get("id")
            if not loc_id:
                continue
            by_date: dict[str, list[dict]] = defaultdict(list)
            for sensor in collector._extract_target_sensors(loc):
                units = sensor.get("parameter", {}).get("units", "µg/m³")
                pname = sensor.get("parameter", {}).get("name", "unknown")
                rows = fetch_sensor_hours(collector, sensor["id"], start, end)
                logger.info("%s | %s | sensor %s (%s): %d hourly rows", city, loc.get("name"), sensor["id"], pname, len(rows))
                for r in rows:
                    day = ((r.get("period") or {}).get("datetimeFrom") or {}).get("utc", "")[:10]
                    if not day or not (start <= _d(day) <= end):
                        continue
                    r["_sensor_id"], r["_parameter"], r["_sensor_units"] = sensor["id"], pname, units
                    by_date[day].append(r)
            for day, items in by_date.items():
                collector._save_raw(city, loc_id, loc, items, day)
                city_total += len(items)
        collector.ingestion_logger.log(
            run_id=str(collector.run_id), source=f"openaq:{city}", extraction_date=end,
            started_at=started, completed_at=datetime.now(timezone.utc),
            status="success" if city_total else "failed", row_count=city_total, rejected_count=0,
            error_message=None if city_total else "no rows returned in backfill range",
            file_path=str(collector.raw_dir),
        )
        logger.info("%s: saved %d hourly rows across %d days", city, city_total, (end - start).days + 1)


def collect_weather(days: list[date]) -> None:
    from ingestion.openmeteo_collector import OpenMeteoCollector

    for d in days:
        wc = OpenMeteoCollector(extraction_date=d)
        if wc.load_raw_files(d):
            continue
        wc.collect()


def etl_days(days: list[date]) -> None:
    from ingestion.openaq_collector import OpenAQCollector
    from ingestion.openmeteo_collector import OpenMeteoCollector
    from run_pipeline import normalize_validate_load

    for i, d in enumerate(days, 1):
        try:
            normalize_validate_load(d, OpenAQCollector(extraction_date=d), OpenMeteoCollector(extraction_date=d))
        except Exception as exc:
            logger.error("ETL failed for %s: %s", d, exc)
        if i % 20 == 0:
            logger.info("ETL progress: %d/%d days", i, len(days))


def selected_location_ids() -> set[int]:
    from ingestion.openaq_collector import CITY_BBOXES, OpenAQCollector

    c = OpenAQCollector()
    return {l["id"] for city in CITY_BBOXES for l in c.select_stations(c.get_locations(city))}


def build_gold_windows(start: date, end: date, window_days: int = 31) -> None:
    from db.db_utils import read_sql
    from etl.gold_layer import build_gold_layer

    # cleaned.* still holds rows from earlier (recency-picked) stations; keep gold on one station set.
    station_ids = selected_location_ids()
    logger.info("Gold restricted to %d selected stations", len(station_ids))
    cur = start
    while cur <= end:
        win_end = min(cur + timedelta(days=window_days - 1), end)
        q = "SELECT * FROM cleaned.{t} WHERE date(measured_at_ist) BETWEEN CAST(:a AS date) AND CAST(:b AS date)"
        df_aq = read_sql(q.format(t="air_quality"), {"a": str(cur), "b": str(win_end)})
        df_w = read_sql(q.format(t="weather"), {"a": str(cur), "b": str(win_end)})
        df_aq = df_aq[df_aq["location_id"].isin(station_ids)]
        if not df_aq.empty:
            res = build_gold_layer(df_aq, df_w)
            logger.info("gold %s..%s: %s", cur, win_end, {k: len(v) for k, v in res.items()})
        cur = win_end + timedelta(days=1)


def fill_gaps(leave_days: int = 1, max_days: int = 400) -> dict:
    """
    Load every day between the last day in gold and `leave_days` before today.
    The daily DAGs use catchup=False, so downtime (machine off, Docker stopped) otherwise leaves a permanent gap.
    The most recent `leave_days` days are left to the regular daily tasks.
    """
    import pandas as pd

    from db.db_utils import read_sql

    latest = read_sql("SELECT MAX(date_ist) AS d FROM gold.daily_city_aqi")["d"].iloc[0]
    if pd.isna(latest):
        return {"filled": 0, "reason": "gold is empty - run run_backfill.py with an explicit range first"}
    start = pd.Timestamp(latest).date()          # re-load the last day: it may have been partial
    end = date.today() - timedelta(days=1 + leave_days)
    if start > end:
        return {"filled": 0, "reason": f"up to date (latest={start})"}
    if (end - start).days > max_days:
        raise RuntimeError(f"Gap of {(end - start).days} days exceeds {max_days}; run run_backfill.py manually")

    days = [start + timedelta(days=i) for i in range((end - start).days + 1)]
    logger.info("Filling gap %s .. %s (%d days)", start, end, len(days))
    fetch_openaq_range(start, end)
    collect_weather(days)
    etl_days(days)
    build_gold_windows(start, end)
    return {"filled": len(days), "start": str(start), "end": str(end)}


if __name__ == "__main__":
    from dotenv import load_dotenv

    load_dotenv()
    ap = argparse.ArgumentParser()
    ap.add_argument("start")
    ap.add_argument("end")
    ap.add_argument("--skip-fetch", action="store_true", help="reuse raw OpenAQ files already on disk")
    ap.add_argument("--skip-weather", action="store_true")
    a = ap.parse_args()
    s, e = _d(a.start), _d(a.end)
    days = [s + timedelta(days=i) for i in range((e - s).days + 1)]

    if not a.skip_fetch:
        fetch_openaq_range(s, e)
    if not a.skip_weather:
        collect_weather(days)
    etl_days(days)
    build_gold_windows(s, e)
    logger.info("Backfill complete: %s .. %s", s, e)
