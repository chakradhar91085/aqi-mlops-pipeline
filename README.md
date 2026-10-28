# AQI Analysis & Prediction Pipeline

**Course:** Data Engineering and MLOps | **Part 1 Submission** | **Due: 7 September 2026**

A complete end-to-end data pipeline for Air Quality Index (AQI) analysis across major Indian cities — Delhi, Mumbai, Bengaluru, Chennai, and Kolkata.

---

## 🏗️ Architecture Overview

```
OpenAQ API  ──┐
              ├──▶ Python Collectors ──▶ Raw JSON Landing Zone (data/raw/)
Open-Meteo  ──┘
                          │
                          ▼
                    Apache Airflow
                    ┌─────────────────────────────────┐
                    │  aqi_ingestion DAG (02:00 UTC)  │
                    │  aqi_etl DAG       (04:00 UTC)  │
                    └─────────────────────────────────┘
                          │
                          ▼
               PostgreSQL (4-layer schema)
               ├── raw.*          (exact API payloads)
               ├── staging.*      (normalized)
               ├── cleaned.*      (validated + deduplicated)
               └── gold.*         (aggregated, AQI-calculated)
                          │
                          ▼
               Streamlit Dashboard (http://localhost:8511)
               ├── 📈 AQI Trend
               ├── 🧪 Pollutant Contribution
               ├── 🏙️ City Comparison
               ├── ⏰ Hourly & Monthly Patterns
               └── 🌡️ Weather Impact on AQI
```

---

## 📁 Project Structure

```
ml-ops/
├── docker-compose.yml           # Full stack orchestration
├── .env.example                 # Environment variable template
├── requirements.txt             # Python dependencies
│
├── dags/                        # Airflow DAGs
│   ├── aqi_ingestion_dag.py     # Daily data collection (02:00 UTC)
│   └── aqi_etl_dag.py           # ETL + quality checks (04:00 UTC)
│
├── ingestion/                   # Data collectors
│   ├── openaq_collector.py      # OpenAQ v3 REST API
│   ├── openmeteo_collector.py   # Open-Meteo Archive API
│   └── ingestion_log.py         # Dual-sink run logger (CSV + DB)
│
├── etl/                         # Transformation modules
│   ├── normalize.py             # UTC→IST, unit conversion, name cleaning
│   ├── validate.py              # Range checks, dedup, gap detection
│   ├── aqi_calculator.py        # US EPA AQI formula
│   └── gold_layer.py            # Analytical table builder
│
├── db/
│   ├── schema.sql               # Full 4-layer PostgreSQL DDL
│   └── db_utils.py              # SQLAlchemy helpers + upsert
│
├── dashboard/                   # Streamlit app
│   ├── Home.py                  # Home page + navigation
│   ├── Dockerfile
│   ├── pages/                   # 5 analytics pages
│   └── utils/                   # Cached queries + chart builders
│
├── data/
│   ├── raw/openaq/              # Raw JSON by date/city
│   ├── raw/openmeteo/           # Raw weather JSON
│   └── logs/
│       ├── ingestion_log.csv    # Extraction run metadata
│       └── error_log.csv        # Rejected records
│
├── tests/                       # pytest test suite
│   ├── test_aqi_calculator.py
│   ├── test_etl.py
│   └── test_ingestion.py
│
└── docs/
    ├── architecture_diagram.md
    └── data_dictionary.md
```

---

## 🚀 Quick Start

### Prerequisites
- [Docker Desktop](https://www.docker.com/products/docker-desktop/) installed and running
- Git

### 1. Clone & Configure

```bash
git clone https://github.com/chakradhar91085/aqi-mlops-pipeline.git
cd aqi-mlops-pipeline

# Copy environment template and edit your values
cp .env.example .env
# Edit .env — at minimum change POSTGRES_PASSWORD and generate Fernet key
```

**Generate the Airflow Fernet key:**
```bash
python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```
Paste the output into `.env` as `AIRFLOW_FERNET_KEY`.

### 2. Start All Services

```bash
docker-compose up --build -d
```

This starts:
| Service | URL |
|---|---|
| **Airflow Webserver** | http://localhost:8081 (admin/admin) |
| **Streamlit Dashboard** | http://localhost:8511 |
| **PostgreSQL** | localhost:5432 |

Wait ~2 minutes for Airflow to initialize.

### 3. Run the Pipeline

**Option A — Via Airflow UI:**
1. Open http://localhost:8081
2. Enable the `aqi_ingestion` DAG → click **▶ Trigger**
3. Wait for it to complete, then enable and trigger `aqi_etl`

**Option B — Manual Python run (local development):**
```bash
# Install dependencies
pip install -r requirements.txt

# Copy env
cp .env.example .env

# Run ingestion for today
python -m ingestion.openaq_collector
python -m ingestion.openmeteo_collector
```

### 4. View the Dashboard

Open **http://localhost:8511** in your browser.

---

## 🧪 Running Tests

```bash
# Install test dependencies
pip install -r requirements.txt

# Run all tests
pytest tests/ -v

# Run with coverage
pytest tests/ --cov=etl --cov=ingestion --cov-report=term-missing
```

---

## 🗄️ Database Layers

| Schema | Purpose |
|---|---|
| `raw` | Exact API payloads — never modified |
| `staging` | Normalized (unit-converted, timestamp-standardized) |
| `cleaned` | Validated, deduplicated, quality-checked |
| `gold` | Aggregated hourly/daily AQI + pollutant contributions + weather join |
| `meta` | Ingestion run logs |

Connect to PostgreSQL:
```bash
docker exec -it aqi_postgres psql -U aqi_user -d aqi_db
```

---

## 📊 Data Sources

| Source | URL | Usage | API Key |
|---|---|---|---|
| **OpenAQ v3** | https://api.openaq.org/v3/ | PM2.5, PM10, NO2, O3, CO, SO2 | **Required** — register free at https://explore.openaq.org/ |
| **Open-Meteo** | https://archive-api.open-meteo.com/v1/archive | Weather variables | None required |

Both sources are **free, publicly accessible, and legally usable** per the project dataset rule.

---

## 🗺️ Target Cities

| City | Lat | Lon | Notes |
|---|---|---|---|
| Delhi | 28.6448 | 77.2167 | Highest AQI, most monitoring stations |
| Mumbai | 19.0760 | 72.8777 | Coastal, sea-breeze effect |
| Bengaluru | 12.9716 | 77.5946 | IT corridor, moderate AQI |
| Chennai | 13.0827 | 80.2707 | Coastal city |
| Kolkata | 22.5726 | 88.3639 | Industrial area |

---

## 📋 Ingestion Metadata

Every pipeline run writes to `data/logs/ingestion_log.csv`:

| Field | Description |
|---|---|
| `run_id` | UUID for this execution |
| `source` | `openaq:Delhi`, `openmeteo:Mumbai`, etc. |
| `extraction_date` | Date of data collected |
| `status` | `success` / `partial` / `failed` |
| `row_count` | Records successfully collected |
| `rejected_count` | Records rejected by quality checks |
| `error_message` | Details if status ≠ success |

---

## 🩹 Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| Dashboard shows **"Connection error … invalid wire type"** or a page hangs, usually on pages with tables | Another Streamlit app is listening on host port 8501, so the browser mixes two servers of different versions | The dashboard is published on **8511** for this reason. Check with `Get-NetTCPConnection -LocalPort 8501 -State Listen` and keep the dashboard on its own port |
| Home page warns **"Latest data is N days old"**, `aqi_ingestion` fails at `fill_gaps` or `ingest_openaq` | OpenAQ rejected the API key (HTTP 401) — keys can be revoked or expire | Create a new key at https://explore.openaq.org, set `OPENAQ_API_KEY` in `.env`, then `docker compose up -d --force-recreate airflow-webserver airflow-scheduler`. `fill_gaps` then back-fills every missed day automatically |
| A dashboard edit does not show up | Streamlit caches page scripts and the file watcher does not see Windows bind-mount changes (the polling watcher breaks the app here) | `docker restart aqi_streamlit` |
| `localhost:8080` (Airflow) not responding | Container left in `Created` state after Docker Desktop restarted | `docker start aqi_airflow_webserver` |
| After a reboot nothing runs | Docker Desktop is not started | Start Docker Desktop, then `docker compose up -d postgres mlflow api streamlit airflow-webserver airflow-scheduler` (leave out the one-shot `pipeline` service) |

---

## 🔧 Environment Variables

See `.env.example` for all variables. Key ones:

| Variable | Description |
|---|---|
| `POSTGRES_USER` | DB username |
| `POSTGRES_PASSWORD` | DB password (**change in production**) |
| `AIRFLOW_FERNET_KEY` | Airflow encryption key (generate with Python) |
| `OPENAQ_API_KEY` | **Required** — get a free key at https://explore.openaq.org/ |
| `TARGET_CITIES` | Comma-separated city list |
| `BACKFILL_DAYS` | Days of historical data to collect on first run |
| `MAX_REJECTION_RATE` | Pipeline fails if rejection % exceeds this |

---

## 📈 Dashboard Pages

| Page | Description |
|---|---|
| **Home** | Live AQI KPIs per city |
| **AQI Trend** | Multi-city daily AQI line chart + 7-day rolling average |
| **Pollutant Contribution** | Pie chart + stacked bar of AQI sub-index breakdown |
| **City Comparison** | Heatmap + ranked bar chart across all 5 cities |
| **Hourly & Monthly Patterns** | Hour×day heatmap + seasonal box plots |
| **Weather Impact** | Scatter plots + correlation matrix |

---

## 🤖 Part 2 — MLOps Extension

Predicts **next-day AQI per city** (plus the AQI category) from the gold tables, tracks every
experiment in MLflow, serves the champion model through FastAPI in Docker, and monitors it daily.

| Service | URL |
|---|---|
| **MLflow** (experiments + model registry) | http://localhost:5000 |
| **Prediction API** (Swagger UI at `/docs`) | http://localhost:8000 |
| Dashboard pages `AQI Forecast`, `Model Monitoring` | http://localhost:8511 |

```
ml/features.py     lag / rolling / pollutant / weather / calendar features (shared by train, API, monitoring)
ml/train.py        baselines + 3 regressors + 3 classifiers -> MLflow -> registry -> @champion promotion gate
ml/monitoring.py   PSI/KS drift, missing data, live forecast error, API latency/errors, retrain decision
api/main.py        FastAPI: GET /predict?city=, GET /health, POST /reload
dags/aqi_ml_monitoring_dag.py   daily: predict -> backfill actuals -> metrics -> retrain check
dags/aqi_ml_training_dag.py     weekly / triggered: train -> promote -> reload API
db/ml_schema.sql   schema `ml`: predictions, api_requests, monitoring_metrics
```

### Run it

```bash
docker compose up -d --build              # postgres, airflow, streamlit, mlflow, api
docker exec -i aqi_postgres psql -U aqi_user -d aqi_db < db/ml_schema.sql   # only for a pre-existing DB volume

# 1. History: the model needs months of data (~1 year recommended, covers a full winter)
docker compose run --rm pipeline python run_backfill.py 2025-10-01 2026-10-02

# 2. Train (logs every run to MLflow, registers the best regressor, promotes it to @champion)
docker compose exec airflow-scheduler python -m ml.train --promote
docker compose restart api                # or: curl -X POST localhost:8000/reload

# 3. Use it
curl "localhost:8000/predict?city=Delhi"
```

Then enable the `aqi_ml_monitoring` DAG in Airflow (daily). It stores live forecasts, scores them
once the actual AQI arrives, computes drift/error metrics and triggers `aqi_ml_training` when a
retraining criterion fires. See [docs/model_lifecycle.md](docs/model_lifecycle.md) for the promotion gate,
monitoring thresholds, retraining criteria and rollback procedure.

```bash
pytest tests/ -v          # feature leakage, PSI, retrain rules, API (API tests need fastapi: run in the api image)
```

---

## 📄 License

Academic project — Data Engineering & MLOps course, 2026.
