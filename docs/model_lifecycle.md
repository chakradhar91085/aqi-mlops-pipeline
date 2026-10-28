# Model Lifecycle & Retraining Policy

Task: **predict next-day AQI for each of 5 Indian cities** (regression), with a derived AQI category.
A classification comparison (predict the category directly) is trained alongside it.

## 1. Lifecycle

```
gold.daily_city_aqi + gold.weather_aqi_daily
        │  ml/features.py  (lags, rolling stats, pollutants, weather, calendar)
        ▼
ml/train.py ── chronological 70/15/15 split ── baselines + 3 regressors + 3 classifiers
        │            every run → MLflow (params, metrics, artifacts, signature)
        ▼
MLflow Model Registry  `aqi_next_day`  v1, v2, …
        │  alias @champion  (promotion gate below)
        ▼
FastAPI  /predict  (loads models:/aqi_next_day@champion, same ml/features.py)
        │
        ▼
ml.predictions (live) ──▶ actuals back-filled next day ──▶ ml.monitoring_metrics
        │                                                       │
        └──────────────── retrain triggers ◀────────────────────┘
```

| Stage | Where | Notes |
|---|---|---|
| Features | `ml/features.py` | One code path for training, serving and monitoring. Each city is reindexed to a full daily calendar before lagging, so a missing day gives NaN, never a shifted value. |
| Split | `chronological_split` | Same cut dates for all cities: first 70% of dates train, next 15% validation (model selection), last 15% test (reported once). No shuffling. |
| Baselines | `ml/train.py` | Persistence (tomorrow = today) and 7-day mean. A model that cannot beat persistence is not worth deploying. |
| Models | `ml/train.py` | Regression: Ridge, RandomForest, HistGradientBoosting. Classification: LogisticRegression, RandomForest, HistGradientBoosting (`class_weight=balanced`). Small grids tuned on validation only. |
| Metrics | MLflow | Regression: RMSE, MAE, R². Classification: accuracy, precision/recall/F1 (macro + weighted), confusion matrix. |
| Registry | MLflow | Best regressor by validation MAE is registered as `aqi_next_day`. |
| Serving | `api/main.py` | `GET /predict?city=`, `GET /health`, `POST /reload`. Request latency/status logged to `ml.api_requests`. |

## 2. Promotion gate

A newly trained model is **registered** every time, but only gets the `champion` alias if its
**test MAE is lower than the current champion's by at least 0.05 AQI points on the same test window**
(champion is re-scored on the new test set; the margin ignores retraining noise). Otherwise it stays registered without the alias, and the API keeps serving the champion.

**Rollback:** move the `champion` alias back to the previous version
(`MlflowClient().set_registered_model_alias("aqi_next_day", "champion", "<version>")`) and call `POST /reload`.

## 3. Monitoring (daily, `aqi_ml_monitoring` DAG, 05:00 UTC)

| Concern | Metric | Alert threshold |
|---|---|---|
| Forecasting error | `live_mae_14d` (live predictions vs actuals) | > 1.3 × champion test MAE (needs ≥ 10 scored predictions) |
| Feature drift | PSI + KS test per key feature, last 14 days vs the champion's training data **from the same time of year** (±21 days; falls back to the whole training window) | PSI > max(0.25, 4 × expected sampling noise), see below; KS p < 0.05 |
| Missing / bad input | `missing_pct` of NaN in today's inference rows; `stale_cities` | > 20% missing; any city without yesterday's data |
| API latency | p95 over 24 h | > 2000 ms |
| Failures | share of requests with 5xx / exception over 24 h | > 5% |

PSI reading: < 0.1 stable, 0.1 – 0.25 moderate shift, > 0.25 significant shift — for large samples.
With only ~70 current rows (14 days × 5 cities) PSI is noisy: random halves of *identical* data reached
PSI ≈ 1.0 at 10 bins. The monitor therefore uses fewer bins for small windows and raises the alert level to
4 × the PSI expected from sampling noise alone, (bins − 1) × (1/n_ref + 1/n_current), never below 0.25.
A seasonal reference matters too: comparing an autumn window with a whole Oct–Jun training period flags
the season itself as drift.

## 4. Retraining criteria

Retraining runs the `aqi_ml_training` DAG (train → promotion gate → API reload). It fires when **any** of:

| Trigger | Condition |
|---|---|
| Scheduled | Weekly, Sunday 06:00 UTC |
| Performance decay | `live_mae_14d` > 1.3 × champion test MAE |
| Feature drift | PSI above its adjusted threshold on ≥ 2 of the key features |
| New data | ≥ 30 days of data newer than the last day the champion was trained/evaluated on (`test_end`) |

The monitoring DAG evaluates the last three daily and triggers training through `TriggerDagRunOperator`.

## 5. Known limitations

- **History length.** The model sees ≈ 1 year of data, so it has seen each season once; winter peaks are
  represented by a single winter.
- **No forecast weather.** Weather features are the observed values for day *t*, not the forecast for
  *t+1*. Using a weather forecast would likely help and is the first extension to try.
- **Station set.** Daily city AQI averages over each city's selected stations (`select_stations` in
  `ingestion/openaq_collector.py`). If OpenAQ retires a station the set can change and shift the series; the
  drift monitor is the safety net.
- **MLflow backend** is SQLite on a single volume: fine for one user, move to Postgres for concurrent use.
