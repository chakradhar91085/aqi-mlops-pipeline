# Data Sources — AQI Analysis & Prediction (Part 1)

Both sources are free, public APIs. No scraping, no paid tiers, no personal/private data.

## 1. OpenAQ API v3 — air pollution measurements

- **What it provides:** Pollutant concentrations (`pm25`, `pm10`, `no2`, `so2`, `o3`, `co`) from government and research monitoring stations worldwide, at per-sensor time resolution.
- **Access:** Free, but requires an API key.
  1. Register at https://explore.openaq.org/register
  2. Retrieve the key from https://explore.openaq.org/account
  3. Pass it as the `X-API-Key` header on every request.
- **Endpoints used:**
  - `GET /v3/locations/{location_id}` — station metadata (name, coordinates, sensor list)
  - `GET /v3/sensors/{sensor_id}/measurements?datetime_from=...&datetime_to=...` — raw per-sensor measurements for a date range
- **Station selected for this project:** location id **5613** — "ITO, New Delhi - CPCB" (28.6286° N, 77.2411° E). Chosen because it's an active government (CPCB) station reporting all six target pollutants in the same feed.
- **Rate/availability caveats:** Some sensor IDs at a location are stale duplicates from earlier instrument generations (e.g. this station has both a live 2026-reporting sensor and a dead one last reporting in 2018 for the same pollutant/unit) — `fetch_openaq.py` tries each candidate sensor and keeps whichever one actually returns data for the requested date.
- **Known data-quality issue:** CO readings from this station were found mislabeled/implausibly scaled (see `docs/data_dictionary.md` → "Known caveat"). Documented and actively filtered by the pipeline's validation step.

## 2. Open-Meteo Archive API — weather

- **What it provides:** Historical hourly weather (temperature, relative humidity, precipitation, wind speed) for any coordinate.
- **Access:** Fully free, **no API key or registration required**.
- **Endpoint used:** `GET https://archive-api.open-meteo.com/v1/archive` with `latitude`, `longitude`, `start_date`, `end_date`, `hourly=temperature_2m,relative_humidity_2m,precipitation,wind_speed_10m`.
- **Coordinates used:** 28.628624° N, 77.24106° E — matched to the OpenAQ station 5613 coordinates so pollution and weather readings represent the same physical location.

## Access instructions to reproduce this pipeline

1. Get an OpenAQ API key (steps above).
2. Copy `.env.example` to `.env`, set `OPENAQ_API_KEY` and `OPENAQ_LOCATION_ID` (default `5613`, or pick another station via `GET /v3/locations?coordinates={lat},{lon}&radius=25000`).
3. No credentials needed for Open-Meteo.
4. `docker compose up --build` — see project `README.md` for full setup.

## Dataset rule compliance

Per the assignment's dataset rule ("free, legally accessible, public, institutional, or instructor-approved datasets"): OpenAQ is an open-data nonprofit aggregating government/research monitors under open licenses (CC BY 4.0 per-source, varies by provider — see https://openaq.org for per-station licensing); Open-Meteo's archive API is explicitly free for non-commercial use. No CPCB scraping or Kaggle fallback was needed since OpenAQ's India coverage (including CPCB-operated stations) was sufficient.
