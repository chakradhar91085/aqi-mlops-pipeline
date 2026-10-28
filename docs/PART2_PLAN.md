# Part 2 — MLOps Extension: Build Plan

**Goal:** Extend the Part 1 pipeline so it predicts **next-day AQI per city**, tracks experiments in **MLflow**, serves the champion model through **FastAPI + Docker**, monitors data/drift/errors/latency, and retrains under defined criteria.

**Status as of 2026-10-03:** Part 1 pipeline is complete. Nothing from Part 2 exists yet (no ML code, no MLflow, no FastAPI).

---

## 0. Key design decisions

| Decision | Choice | Why |
|---|---|---|
| Prediction task | **Regression: next-day AQI** (primary) + **classification: next-day AQI category** (comparison) | Requirement says "compare regression and classification". The regression output also gives a category through the existing `etl.aqi_calculator.aqi_category()`, so one served model covers both. |
| Granularity | **City × day** (`gold.daily_city_aqi` joined with `gold.weather_aqi_daily`) | Matches "next-day AQI". These tables already exist, so no new ETL is needed. |
| Models | Baselines (persistence, 7-day mean) → Ridge / RandomForest / HistGradientBoosting (+ classifier equivalents) | Everything is in scikit-learn, so no new boosting library is needed. HistGradientBoosting handles NaN natively. |
| Split | **Chronological by date**: 70% train / 15% val / 15% test, with the same cut dates for every city | Time series. A random split leaks future data into training. |
| Tracking | MLflow server with a **SQLite backend** and a local artifact volume | Model registry works on SQLite. It also avoids changing the existing Postgres volume. `ponytail:` single-user ceiling; move to a Postgres backend if several people train at once. |
| Serving | FastAPI loads `models:/aqi_next_day@champion` and builds features from Postgres with the **same `ml/features.py`** used in training | One feature code path means no training/serving skew, and the client only sends `city`. |
| Monitoring | Plain numpy/scipy (PSI, KS) → `ml.monitoring_metrics` table → Streamlit page | No Evidently, Prometheus or Grafana. Postgres plus a dashboard page meets every monitoring requirement. |
| Orchestration | Two new Airflow DAGs: `aqi_ml_monitoring` (daily) and `aqi_ml_training` (weekly or triggered) | Reuses the existing Airflow stack. |

**Deliberately not built** (add only if a requirement forces it): feature store (Feast), Evidently, Prometheus/Grafana, XGBoost/LightGBM, Kubernetes, API auth (the service is local-only), Open-Meteo *forecast* weather as a feature.

---

## 1. Target architecture

```
                  ┌──────────── Part 1 (exists) ────────────┐
OpenAQ/Open-Meteo → ingestion → raw → staging → cleaned → gold.daily_city_aqi
                                                        gold.weather_aqi_daily
                                                               │
                  ┌──────────── Part 2 (new) ──────────────────┼───────────────┐
                  │                                            ▼               │
                  │                                   ml/features.py           │
                  │                          (lags, rolling, weather, calendar)│
                  │                      ┌──────────────┴──────────────┐       │
                  │                      ▼                             ▼       │
                  │              ml/train.py                     api/main.py   │
                  │        (baselines + 6 models)              FastAPI :8000   │
                  │                      │                  loads @champion    │
                  │                      ▼                             │       │
                  │            MLflow :5000 ───── registry ────────────┘       │
                  │       (params, metrics, artifacts,                 │       │
                  │        model versions, @champion alias)            ▼       │
                  │                                         ml.predictions     │
                  │                                         ml.api_requests    │
                  │                                                │           │
                  │   aqi_ml_monitoring DAG (daily) ───────────────┤           │
                  │   predict → backfill actuals → metrics →       ▼           │
                  │   retrain check ──trigger──▶ aqi_ml_training   ml.monitoring_metrics
                  │                              DAG (weekly)       │          │
                  │                                                 ▼          │
                  │                        Streamlit: 06_AQI_Forecast,         │
                  │                                   07_Model_Monitoring      │
                  └────────────────────────────────────────────────────────────┘
```

---

## 2. New files and folders

```
mlops/
├── ml/
│   ├── __init__.py
│   ├── features.py          # build_feature_frame(), make_xy() — shared by train/api/monitoring
│   ├── train.py             # baselines + models, MLflow logging, register, promote
│   └── monitoring.py        # psi(), drift report, missing-data %, rolling error
├── api/
│   ├── main.py              # FastAPI: /health, /predict, /reload (+ free /docs Swagger UI)
│   └── Dockerfile
├── airflow/
│   └── Dockerfile           # FROM apache/airflow:2.9.2 + requirements-ml.txt
├── dags/
│   ├── aqi_ml_monitoring_dag.py
│   └── aqi_ml_training_dag.py
├── dashboard/pages/
│   ├── 06_AQI_Forecast.py
│   └── 07_Model_Monitoring.py
├── db/ml_schema.sql         # schema `ml` + 3 tables
├── requirements-ml.txt      # scikit-learn, mlflow-skinny, fastapi, uvicorn, scipy (pinned)
├── tests/
│   ├── test_features.py     # leakage + lag correctness
│   ├── test_monitoring.py   # PSI sanity
│   └── test_api.py          # TestClient with a dummy model
└── docs/model_lifecycle.md  # retraining criteria + lifecycle
```

**Modified:** `docker-compose.yml` adds the `mlflow` and `api` services, builds Airflow from `airflow/Dockerfile`, mounts `./ml`, and adds the `MLFLOW_TRACKING_URI` and `API_URL` env vars. `.env.example` gets the new env vars. `README.md` gets a Part 2 section.

---

## 3. Phases

Each phase ends with an **acceptance check**: something you can run that proves the phase works. Day estimates assume part-time work.

### Phase 0: Data backfill (BLOCKER, about 1 day mostly waiting)

There are about 26 days × 5 cities ≈ 130 rows today. A next-day model needs **at least 180 days**, and ideally **365** so it sees a full winter. Indian AQI is strongly seasonal and peaks from Nov to Jan.

1. Start Docker Desktop, then run `docker compose up -d postgres`.
2. **Measure before backfilling.** Run `python run_pipeline.py 2025-10-01` for one past date. Check that OpenAQ returns data for that date, and note the runtime and request count.
3. Backfill with the code that already exists: `python run_pipeline.py 365`. That loops oldest to newest, one day at a time. Run it overnight. OpenAQ rate limits (about 60 req/min) mean this takes hours, not minutes.
4. If OpenAQ has too little history for those dates, use the fallback the course already approves: the Kaggle *"Air Quality Data in India"* `city_day.csv`. Load it into `gold.daily_city_aqi` with a one-off script and document it as a second source.

**Acceptance:** this query shows ≥180 days per city and <10% missing days:
```sql
SELECT city, count(*) days, min(date_ist), max(date_ist) FROM gold.daily_city_aqi GROUP BY city;
```

### Phase 1: Feature pipeline `ml/features.py` (about 1 day) · *Rubric: model dev*

`build_feature_frame(cities, start, end) -> DataFrame`. It reads gold via `db.db_utils.read_sql`, with one row per (city, date):

| Group | Features |
|---|---|
| AQI lags | `aqi_lag1, lag2, lag3, lag7` |
| Rolling | `aqi_roll3_mean, roll7_mean, roll7_std, roll14_mean` (computed on data **up to and including day t** only) |
| Pollutants (day t) | `pm25_avg, pm10_avg, no2_avg, o3_avg, co_avg, so2_avg`, `pm25_lag1` |
| Weather (day t) | `temp_avg, humidity_avg, wind_speed_avg, precipitation_sum` |
| Calendar | `dow, month, is_weekend` |
| Categorical | `city` (one-hot inside the sklearn Pipeline, so the API sends the raw value) |
| **Targets** | `y_aqi = aqi.shift(-1)` (day t+1), `y_cat = aqi_category(y_aqi)` |

Details that matter:
- **Reindex each city to a complete daily calendar before shifting.** Otherwise `shift(1)` on a frame with missing days returns the wrong day's AQI as "lag1".
- `make_xy(df)` drops rows where the target is NaN (the last day). Inference uses the same frame without dropping.
- Rare categories: if "Hazardous" has fewer than about 10 rows, merge it into "Very Unhealthy". Note that in the docs.

**Acceptance:** `pytest tests/test_features.py` passes. It checks that, on a synthetic 30-day frame, `aqi_lag1[t] == aqi[t-1]`, `y_aqi[t] == aqi[t+1]`, a missing day yields NaN rather than a shifted value, and no feature column correlates exactly with the target (leakage guard).

### Phase 2: MLflow server + training `ml/train.py` (about 2 days) · *Rubric: model dev & eval (10), MLflow/registry (part of 10)*

**docker-compose `mlflow` service:**
```yaml
mlflow:
  image: ghcr.io/mlflow/mlflow:<pin same version as requirements-ml.txt>
  command: mlflow server --host 0.0.0.0 --port 5000
           --backend-store-uri sqlite:////mlflow/mlflow.db
           --artifacts-destination /mlflow/artifacts --serve-artifacts
  ports: ["5000:5000"]
  volumes: [mlflow_data:/mlflow]
  networks: [aqi_net]
```

**`ml/train.py`** (`python -m ml.train [--promote]`):
1. Build features, then split chronologically by date (70/15/15). Log `train_start/train_end/val_end/test_end` as params, because monitoring reuses them to rebuild the drift reference.
2. **Baselines** (logged as runs): persistence (`ŷ = aqi_t`) and 7-day mean. Every real model must beat persistence. If none does, say so honestly in the report.
3. **Regression:** `Ridge`, `RandomForestRegressor`, `HistGradientBoostingRegressor`. Each is a sklearn `Pipeline` of `ColumnTransformer` (OneHot city + SimpleImputer for numerics) followed by the model, with a small grid tuned on **val**.
   Metrics: **RMSE, MAE, R²** on val and test.
4. **Classification:** `LogisticRegression`, `RandomForestClassifier`, `HistGradientBoostingClassifier` (`class_weight="balanced"`), plus "best regressor → bucketed category".
   Metrics: **precision, recall, F1 (macro + weighted)**, with a confusion matrix PNG as an artifact.
5. Each run logs params, metrics, the model with `signature` + `input_example`, the feature list, and a residual plot (regression).
6. **Register:** the best regressor by val MAE becomes model `aqi_next_day`. With `--promote`, the alias `champion` moves only if the challenger's test MAE ≤ the current champion's MAE on the **same** test window. The old version stays registered, so rollback means moving the alias back.

**Acceptance:** http://localhost:5000 shows the experiment `aqi_next_day` with ≥8 runs (2 baselines + 3 reg + 3 clf), and the registry shows `aqi_next_day` v1 with `@champion`. A comparison table (model × RMSE/MAE/F1) can be exported for the report.

### Phase 3: FastAPI service + Docker (about 1.5 days) · *Rubric: deployment & Docker (part of 10)*

**`api/main.py`:**

| Endpoint | Behaviour |
|---|---|
| `GET /health` | `{"status":"ok","model_version":"3"}`; 503 if no model is loaded |
| `GET /predict?city=Delhi[&as_of=2026-10-02]` | Builds the feature row for `as_of` (default: latest gold date) and returns `{city, as_of, forecast_date, predicted_aqi, predicted_category, model_name, model_version}`. 404 for an unknown city, 422 if there are fewer than 14 days of history. |
| `POST /reload` | Reloads `@champion` from the registry. The training DAG calls it after a promotion. |
| `GET /docs` | Swagger UI that FastAPI generates on its own, useful for the demo |

- **Middleware** logs every request to `ml.api_requests` (timestamp, endpoint, city, status, latency_ms, error). `ponytail:` one DB insert per request is fine at this volume; batch it or move it to a log shipper if traffic grows.
- **`api/Dockerfile`:** `python:3.12-slim` (match the Airflow image's Python so pickled sklearn models load), install `requirements-ml.txt` + `psycopg2-binary`, copy `api/ ml/ db/ etl/aqi_calculator.py`, run `uvicorn api.main:app --host 0.0.0.0 --port 8000`, and add a HEALTHCHECK on `/health`.
- **compose `api` service:** port 8000, `depends_on: postgres(healthy), mlflow`, `env_file: .env`, and `MLFLOW_TRACKING_URI=http://mlflow:5000`.

**Acceptance:** `docker compose up -d api`, then `curl "localhost:8000/predict?city=Delhi"` returns valid JSON, and `pytest tests/test_api.py` passes (it uses FastAPI's `TestClient` with a stub model, so no MLflow is needed).

### Phase 4: Airflow integration + daily predictions (about 1 day) · *Rubric: MLflow/deployment, orchestration*

1. **`airflow/Dockerfile`:** `FROM apache/airflow:2.9.2`, then `pip install -r requirements-ml.txt` **with Airflow's constraints file**. Use `mlflow-skinny` (client only) to avoid clashing with Airflow's SQLAlchemy 1.4 pin. **Verify this build first**; it is the riskiest step.
2. In `docker-compose.yml`, change `x-airflow-common` from `image:` to `build: ./airflow`, add the volume `./ml:/opt/airflow/ml`, and add the env vars `MLFLOW_TRACKING_URI`, `API_URL=http://api:8000`.
3. **`db/ml_schema.sql`** (apply once to the existing DB with `docker exec -i aqi_postgres psql -U aqi_user -d aqi_db < db/ml_schema.sql`, and also mount it as `02_ml_schema.sql` in initdb for fresh setups):
   ```sql
   CREATE SCHEMA IF NOT EXISTS ml;
   CREATE TABLE IF NOT EXISTS ml.predictions (
     id BIGSERIAL PRIMARY KEY, city VARCHAR(100) NOT NULL,
     as_of DATE NOT NULL, forecast_date DATE NOT NULL,
     predicted_aqi DOUBLE PRECISION, predicted_category VARCHAR(50),
     actual_aqi INTEGER, actual_category VARCHAR(50), abs_error DOUBLE PRECISION,
     model_version VARCHAR(20) NOT NULL, source VARCHAR(10) NOT NULL DEFAULT 'live', -- live | backtest
     predicted_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
     UNIQUE (city, forecast_date, model_version, source));
   CREATE TABLE IF NOT EXISTS ml.api_requests (
     id BIGSERIAL PRIMARY KEY, ts TIMESTAMPTZ NOT NULL DEFAULT NOW(),
     endpoint VARCHAR(50), city VARCHAR(100), status_code INTEGER,
     latency_ms DOUBLE PRECISION, error TEXT);
   CREATE TABLE IF NOT EXISTS ml.monitoring_metrics (
     id BIGSERIAL PRIMARY KEY, run_date DATE NOT NULL,
     metric VARCHAR(50) NOT NULL,      -- live_mae_14d | psi | ks_pvalue | missing_pct | latency_p95_ms | error_rate
     scope VARCHAR(100) NOT NULL,      -- city name, feature name, or 'all'
     value DOUBLE PRECISION, threshold DOUBLE PRECISION, breached BOOLEAN,
     UNIQUE (run_date, metric, scope));
   ```
4. **Seed backtest predictions:** `train.py` writes the champion's test-set predictions to `ml.predictions` with `source='backtest'`, so the monitoring pages have history immediately instead of after weeks of live runs.
5. **Start live predictions early.** The first task of `aqi_ml_monitoring` (Phase 5) is `predict_tomorrow`. Ship that task alone as soon as the API works, so live predictions start piling up while you build the rest.

**Acceptance:** after one DAG run, `SELECT * FROM ml.predictions WHERE source='live'` has 5 rows (one per city).

### Phase 5: Monitoring + retraining (about 2 days) · *Rubric: monitoring & retraining (5)*

**`dags/aqi_ml_monitoring_dag.py`**, daily at **05:00 UTC** (an hour after `aqi_etl`):
```
predict_tomorrow ──▶ backfill_actuals ──▶ compute_metrics ──▶ check_retrain ─┬─▶ trigger_training (TriggerDagRunOperator)
                                                                              └─▶ no_retrain
```
| Task | Does | Covers requirement |
|---|---|---|
| `predict_tomorrow` | `GET {API_URL}/predict` per city → upsert `ml.predictions` | serving in production |
| `backfill_actuals` | `UPDATE ml.predictions SET actual_aqi, actual_category, abs_error` from `gold.daily_city_aqi` where actual is NULL | forecasting error |
| `compute_metrics` | writes to `ml.monitoring_metrics`, see the list below | all monitoring items |
| `check_retrain` | branch on the retraining criteria | retraining |

`compute_metrics` writes:
- **Forecasting error / model performance:** `live_mae_14d` per city and overall
- **Missing data / input quality:** `missing_pct` = share of NaN features in today's inference rows, plus cities with no gold row for yesterday
- **Feature drift:** `psi` and `ks_pvalue` per key feature (`aqi_lag1, pm25_avg, pm10_avg, temp_avg, humidity_avg, wind_speed_avg`). The last 14 days are compared with the champion's training window, rebuilt from the run's logged `train_start/train_end`.
- **Latency / failures:** `latency_p95_ms` and `error_rate` over 24h from `ml.api_requests`

**Retraining criteria** (written up in `docs/model_lifecycle.md`):
| Trigger | Threshold |
|---|---|
| Scheduled | weekly, Sunday 06:00 UTC (`aqi_ml_training` schedule) |
| Performance decay | `live_mae_14d` > **1.3 ×** champion's test MAE |
| Feature drift | PSI > **0.25** on ≥2 key features (PSI <0.1 means stable, 0.1–0.25 moderate, >0.25 significant) |
| New data | ≥30 new days since the champion's `train_end` |
| **Promotion gate** | the challenger is promoted only if its test MAE ≤ the champion's on the same window; otherwise it stays registered without the alias |

**`dags/aqi_ml_training_dag.py`:** `train_and_evaluate` (`python -m ml.train`) ──▶ `promote_if_better` ──▶ `reload_api` (`POST /reload`).

**Acceptance:** `pytest tests/test_monitoring.py` (PSI of identical samples ≈ 0, PSI of a +2σ shifted sample > 0.25). To force a retrain, temporarily set the MAE multiplier to 0.1. The monitoring DAG should then trigger `aqi_ml_training`, a new model version should appear in MLflow, and the API's `/health` should report the new version.

### Phase 6: Dashboard integration (about 1 day) · *Rubric: visualization (5)*

- **`06_AQI_Forecast.py`:** pick a city and see tomorrow's predicted AQI plus a category badge (live call to `API_URL/predict`, with a clear message if the API is down). Below that, a **predicted vs actual** line chart from `ml.predictions` joined with `gold.daily_city_aqi`, with backtest and live shaded differently, and a model card (version, train window, test MAE/RMSE, from `/health` plus the MLflow run).
- **`07_Model_Monitoring.py`:** time-series from `ml.monitoring_metrics`: rolling MAE vs threshold, a PSI-per-feature heatmap, missing %, latency p95 and error rate. Breached points are shown in red.
- Add the queries to `dashboard/utils/db_queries.py` following the existing `@st.cache_data` pattern, and set `API_URL` in the streamlit service env.

**Acceptance:** both pages render with backtest data even before any live predictions exist.

### Phase 7: Documentation + demo (about 1.5 days) · *Rubric: documentation & demo (5), design (5)*

- `docs/model_lifecycle.md` covers: data → features → train → evaluate → register → promote → serve → monitor → retrain → rollback (move the alias back).
- Update `docs/architecture_diagram.md` with the MLOps layer (section 1 above).
- README gets a Part 2 quick-start, the new service URLs (MLflow :5000, API :8000/docs) and how to run training.
- Report sections: problem framing, feature design, split strategy, a model comparison table (all runs vs persistence baseline), the chosen model and why, deployment, monitoring, retraining criteria, and limitations (small data, seasonality, no forecast weather).
- Screenshots: MLflow runs comparison, registry with `@champion`, Swagger `/predict` response, Airflow ML DAG graphs, both new dashboard pages.

**Demo script (end to end):**
```bash
docker compose up -d --build                         # all services incl. mlflow + api
docker compose exec airflow-scheduler python -m ml.train --promote
curl "localhost:8000/predict?city=Delhi"             # live prediction
# Airflow UI: trigger aqi_ml_monitoring → show ml.predictions + metrics
# Streamlit: 06_AQI_Forecast, 07_Model_Monitoring
# Force a retrain (threshold tweak) → new version in MLflow → /health shows it
```

---

## 4. Rubric coverage (Part 2 = 50 marks)

| Component | Marks | Delivered by |
|---|---:|---|
| Problem understanding & design | 5 | Section 0 decisions, architecture diagram |
| Data source & ingestion | 5 | Phase 0 backfill (+ Kaggle fallback if used) |
| ETL, validation & storage | 5 | `ml/features.py`, `ml` schema |
| Visualization & interpretation | 5 | Phase 6 pages |
| Documentation & demonstration | 5 | Phase 7 |
| Model development & evaluation | 10 | Phases 1–2 (baselines, 6 models, chronological split, RMSE/MAE/F1) |
| MLflow, registry, deployment, Docker | 10 | Phases 2–4 |
| Monitoring & retraining design | 5 | Phase 5 |

---

## 5. Risks

| Risk | Impact | Mitigation |
|---|---|---|
| OpenAQ has little history for these stations, or rate limits make the backfill too slow | No usable model | Measure 1 day first (Phase 0, step 2). Fall back to the Kaggle `city_day.csv`. |
| `mlflow` deps clash with Airflow 2.9's constraints (SQLAlchemy 1.4) | Airflow image won't build | Use `mlflow-skinny` with the Airflow constraints file, and build the image before writing any DAG |
| sklearn version skew between training (Airflow) and serving (API) | Model fails to load in the API | Both images install the same pinned `requirements-ml.txt` on the same Python minor version |
| Class imbalance (Delhi is mostly Unhealthy+, Bengaluru mostly Moderate) | Misleading accuracy | Macro-F1, `class_weight="balanced"`, merge rare classes |
| Small dataset leads to overfitting | Overstated metrics | Simple models, val-only tuning, untouched test set, always report against persistence |
| `schema.sql` initdb doesn't re-run on the existing Postgres volume | `ml` tables missing | Apply `db/ml_schema.sql` manually once (Phase 4, step 3) |

---

## 6. Order of work at a glance

| # | Phase | Est. | Depends on |
|---|---|---|---|
| 0 | Data backfill | ~1 day (overnight run) | Docker running |
| 1 | Feature pipeline + test | ~1 day | 0 |
| 2 | MLflow + training + registry | ~2 days | 1 |
| 3 | FastAPI + Docker | ~1.5 days | 2 |
| 4 | Airflow image + ml schema + live predictions | ~1 day | 3 |
| 5 | Monitoring + retraining DAGs | ~2 days | 4 |
| 6 | Dashboard pages | ~1 day | 4 (5 for the monitoring page) |
| 7 | Docs, report, demo | ~1.5 days | all |

**Total: about 11 working days.** Start Phase 0 right away, because the backfill takes a long time to run and everything else depends on it.
