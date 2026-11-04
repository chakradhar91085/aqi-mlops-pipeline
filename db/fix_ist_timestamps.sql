-- One-off, idempotent: normalize.py used to store measured_at_ist as UTC + 5:30 *as an instant*,
-- 5.5 hours off in a TIMESTAMPTZ column. Weather rows were always correct.
-- Afterwards rebuild gold so weather days are IST days too (run_backfill.build_gold_windows).
BEGIN;
UPDATE staging.measurements SET measured_at_ist = measured_at_utc WHERE measured_at_ist <> measured_at_utc;
UPDATE cleaned.air_quality  SET measured_at_ist = measured_at_utc WHERE measured_at_ist <> measured_at_utc;
UPDATE gold.hourly_station_aqi SET hour_ist = hour_ist - interval '5 hours 30 minutes'
 WHERE hour_ist - hour_utc > interval '1 hour';
COMMIT;
