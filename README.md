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
               Streamlit Dashboard (http://localhost:8501)
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
git clone <https://github.com/justaguy1337/mlops.git>
cd mlops

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
| **Airflow Webserver** | http://localhost:8080 (admin/admin) |
| **Streamlit Dashboard** | http://localhost:8501 |
| **PostgreSQL** | localhost:5432 |

Wait ~2 minutes for Airflow to initialize.

### 3. Run the Pipeline

**Option A — Via Airflow UI:**
1. Open http://localhost:8080
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

Open **http://localhost:8501** in your browser.

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

## 📚 Part 2 (Coming Soon)

Part 2 will extend this pipeline with:
- AQI forecasting / classification models
- MLflow experiment tracking
- FastAPI + Docker model serving
- Feature drift and data quality monitoring

---

## 📄 License

Academic project — Data Engineering & MLOps course, 2026.
