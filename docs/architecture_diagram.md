# AQI Pipeline — Architecture Diagram

## High-Level Architecture

```mermaid
flowchart TD
    subgraph Sources["📡 Data Sources (Free Public APIs)"]
        A1["OpenAQ v3 API\nPM2.5, PM10, NO2,\nO3, CO, SO2"]
        A2["Open-Meteo Archive API\nTemp, Humidity, Wind,\nPrecipitation, Pressure"]
    end

    subgraph Ingestion["🐍 Python Collectors (Layer 1)"]
        B1["openaq_collector.py\n• City bounding box discovery\n• Paginated measurements fetch\n• Retry with exponential backoff"]
        B2["openmeteo_collector.py\n• Hourly weather for 5 cities\n• Free archive endpoint"]
        B3["ingestion_log.py\n• Run metadata (CSV + DB)\n• Error log"]
    end

    subgraph Landing["📂 Raw Landing Zone (Layer 2)"]
        C1["data/raw/openaq/YYYY-MM-DD/\nlocation_<id>.json"]
        C2["data/raw/openmeteo/YYYY-MM-DD/\n<City>.json"]
        C3["data/logs/\ningestion_log.csv\nerror_log.csv"]
    end

    subgraph Orchestration["⚙️ Apache Airflow (Layer 3)"]
        D1["aqi_ingestion DAG\n02:00 UTC daily\ncheck_db → ingest_openaq + ingest_openmeteo → log_summary"]
        D2["aqi_etl DAG\n04:00 UTC daily\nload_raw → validate → quality_gate → build_gold → detect_gaps"]
    end

    subgraph ETL["🔄 ETL Modules"]
        E1["normalize.py\nUTC→IST timestamps\nUnit conversion ppb→µg/m³\nStation name standardization"]
        E2["validate.py\nEPA range checks\nDeduplication\nGap detection"]
        E3["aqi_calculator.py\nUS EPA breakpoint formula\nSub-index per pollutant\nOverall AQI = max sub-index"]
        E4["gold_layer.py\nHourly/daily aggregation\nPollutant contribution\nWeather join"]
    end

    subgraph DB["🗄️ PostgreSQL Database (Layer 4)"]
        F1["raw.*\nExact API payloads\n(read-only audit trail)"]
        F2["staging.*\nNormalized, not validated"]
        F3["cleaned.*\nValidated + deduplicated\nrejected_records table"]
        F4["gold.*\nhourly_station_aqi\ndaily_city_aqi\npollutant_summary\nweather_aqi_daily\ndim_stations"]
        F5["meta.ingestion_log\nRun tracking"]
    end

    subgraph Dashboard["📊 Streamlit Dashboard (Layer 5)"]
        G1["Page 1: AQI Trend\nMulti-city line chart + rolling avg"]
        G2["Page 2: Pollutant Contribution\nPie + stacked bar"]
        G3["Page 3: City Comparison\nHeatmap + ranking"]
        G4["Page 4: Hourly & Monthly Patterns\nHour×day heatmap + box plots"]
        G5["Page 5: Weather Impact\nScatter plots + correlation matrix"]
    end

    subgraph Part2["🤖 Part 2 — MLOps (implemented)"]
        H1["ml/features.py\nLag, rolling, pollutant, weather,\ncalendar features (shared by train/API/monitoring)"]
        H2["ml/train.py + MLflow :5000\nBaselines, 3 regressors (+delta variants),\n3 classifiers; chronological split"]
        H3["MLflow Model Registry\naqi_next_day versions\n@champion alias + promotion gate"]
        H4["FastAPI + Docker :8000\n/predict /health /reload"]
        H5["aqi_ml_monitoring DAG (05:00 UTC)\npredict → score → PSI/KS drift, errors,\nlatency → retrain check"]
        H6["aqi_ml_training DAG\nweekly or triggered:\ntrain → promote → reload API"]
        H7["ml.* tables\npredictions, api_requests,\nmonitoring_metrics"]
        G6["Pages 6-7: AQI Forecast,\nModel Monitoring"]
    end

    A1 --> B1
    A2 --> B2
    B1 --> C1
    B2 --> C2
    B1 --> C3
    B2 --> C3
    B3 --> C3

    C1 --> D1
    C2 --> D1
    D1 --> D2
    D2 --> E1
    E1 --> E2
    E2 --> E3
    E3 --> E4
    E4 --> F4

    D2 --> F1
    E1 --> F2
    E2 --> F3
    D1 --> F5

    F4 --> G1
    F4 --> G2
    F4 --> G3
    F4 --> G4
    F4 --> G5

    F4 --> H1
    H1 --> H2
    H2 --> H3
    H3 --> H4
    H4 --> H5
    H5 --> H7
    H5 -->|retrain criteria met| H6
    H6 --> H2
    H6 -->|POST /reload| H4
    H7 --> G6
    H4 --> G6

    style Sources fill:#1a1a2e,stroke:#4cc9f0,color:#e6edf3
    style Ingestion fill:#16213e,stroke:#f72585,color:#e6edf3
    style Landing fill:#0f3460,stroke:#4cc9f0,color:#e6edf3
    style Orchestration fill:#1a1a2e,stroke:#7209b7,color:#e6edf3
    style ETL fill:#16213e,stroke:#4361ee,color:#e6edf3
    style DB fill:#0f3460,stroke:#4cc9f0,color:#e6edf3
    style Dashboard fill:#1a1a2e,stroke:#00e400,color:#e6edf3
    style Part2 fill:#16213e,stroke:#f4a261,color:#e6edf3
```

---

## Layer Descriptions

| Layer | Technology | Purpose |
|---|---|---|
| **1. Source** | OpenAQ v3 API, Open-Meteo | Free public air quality and weather data |
| **2. Ingestion** | Python `requests`, `tenacity` | REST API collectors with retry/backoff |
| **3. Raw / Staging** | JSON files + PostgreSQL `raw.*` + `staging.*` | Immutable raw copies, normalized data |
| **4. Transformation** | ETL modules + Apache Airflow | Quality checks, AQI calculation, aggregation |
| **5. Storage** | PostgreSQL `cleaned.*` + `gold.*` | Validated, analysis-ready tables |
| **6. Analytics** | Streamlit + Plotly | Interactive 5-page dashboard |
| **7. MLOps (Part 2)** | scikit-learn, MLflow, FastAPI, Docker, Airflow | Next-day AQI model: training, registry, serving, drift/error monitoring, retraining (see [model_lifecycle.md](model_lifecycle.md)) |

---

## Pipeline Flow (Sequential)

```
[02:00 UTC] aqi_ingestion DAG
    1. check_db_connection        → verify PostgreSQL reachable
    2. ingest_openaq (parallel)   → fetch & save raw JSON
       ingest_openmeteo           → fetch & save raw JSON
    3. log_ingestion_summary      → write to meta.ingestion_log

[04:00 UTC] aqi_etl DAG
    1. load_raw_to_staging        → normalize & load staging.*
    2. validate_and_clean         → quality checks → cleaned.*
    3. quality_gate               → fail if rejection rate > 20%
         ├── [pass] build_gold_layer → gold.* tables
         └── [fail] quality_gate_failed (alert)
    4. identify_data_gaps         → log missing periods
```

---

## Data Flow Diagram

```
Raw Record (OpenAQ JSON)
    │
    ▼ normalize.py
    │  - UTC → IST conversion
    │  - Station name: "anand vihar" → "Anand Vihar"
    │  - Unit: 45 ppb NO2 → 86.04 µg/m³
    ▼
staging.measurements
    │
    ▼ validate.py
    │  - Range check: 0 ≤ PM2.5 ≤ 1000 µg/m³
    │  - Dedup: (location_id, parameter, measured_at_utc)
    │  - Gap detection: flag gaps > 2 hours
    ▼
cleaned.air_quality
    │
    ▼ aqi_calculator.py
    │  - PM2.5 = 55.4 µg/m³ → sub-index = 100
    │  - PM10 = 154 µg/m³ → sub-index = 100
    │  - NO2 = 86 µg/m³ → sub-index = 43
    │  - Overall AQI = max(100, 100, 43, ...) = 100 → "Moderate"
    ▼
gold.hourly_station_aqi → gold.daily_city_aqi
                                   │
                    ┌──────────────┴──────────────┐
                    ▼                             ▼
         gold.pollutant_summary      gold.weather_aqi_daily
         (contribution %)            (joined with weather)
```
