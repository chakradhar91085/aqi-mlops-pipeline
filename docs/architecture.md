# Architecture — AQI Analysis & Prediction (Part 1)

## Pipeline flow

```mermaid
flowchart TB
    subgraph Sources["Source layer"]
        OpenAQ["OpenAQ API v3\n(pollutants, per-sensor)"]
        Meteo["Open-Meteo Archive API\n(weather, no key)"]
    end

    subgraph Ingestion["Ingestion layer — Python (requests)"]
        FetchAQ["fetch_openaq.py"]
        FetchWX["fetch_openmeteo.py"]
    end

    subgraph RawZone["Raw / landing layer — filesystem"]
        RawAQ["data/raw/openaq/{date}_{location}.json"]
        RawWX["data/raw/openmeteo/{date}.json"]
    end

    subgraph Transform["Transformation layer — Airflow tasks"]
        Validate["validate_and_stage.py\nrange checks, reject log"]
        Clean["transform_clean.py\ndedupe, unit normalize, weather join"]
        Mart["build_mart.py\ndaily aggregates, AQI category"]
    end

    subgraph Storage["Storage layer — PostgreSQL (aqi_db)"]
        Staging[("staging schema\nair_quality_observations\nweather_observations\ningestion_log")]
        CleanDB[("clean schema\nair_quality\nrejected_records")]
        MartDB[("mart schema\nstation_daily_pollutant\ncity_daily_aqi")]
    end

    subgraph Analytics["Analytics layer"]
        Dashboard["Streamlit dashboard\ndashboards/app.py"]
    end

    OpenAQ --> FetchAQ --> RawAQ --> Validate
    Meteo --> FetchWX --> RawWX --> Validate
    Validate -->|valid rows| Staging
    Validate -->|invalid rows| CleanDB
    Staging --> Clean --> CleanDB
    CleanDB --> Mart --> MartDB
    MartDB --> Dashboard
```

Orchestration: Apache Airflow (`LocalExecutor`), DAG `aqi_ingestion_pipeline`, schedule `@daily`.
Task dependency graph: `[fetch_openaq, fetch_openmeteo] >> validate_and_stage >> transform_clean >> build_mart`.

## Deployment view

```mermaid
flowchart LR
    subgraph Host["Windows laptop"]
        subgraph Compose["Docker Compose network"]
            PG[("Postgres 16\ncontainer\nport 5433->5432")]
            SCHED["Airflow scheduler\ncontainer"]
            WEB["Airflow webserver\ncontainer\nport 8080"]
        end
        ST["Streamlit dashboard\n(run natively, not\ncontainerized in Part 1)"]
        NativePG[("Native PostgreSQL\ninstall, port 5432\n(unused by this project,\nkept free of conflicts)")]
    end

    SCHED <--> PG
    WEB <--> PG
    ST -->|localhost:5433| PG
```

Postgres and Airflow run in Docker (single `docker-compose.yml`, shared network) so the whole pipeline reproduces with one `docker compose up --build`. The container's Postgres port is published on **5433**, not 5432, because a native PostgreSQL install already occupies 5432 on this machine — internal Airflow↔Postgres traffic is unaffected since it happens over the Docker network by service name (`postgres:5432`). Docker was deliberately *not* used for Part 1's dashboard — only Part 2's FastAPI inference service is a Docker requirement per the assignment; Part 1's Streamlit app runs natively against the published Postgres port.

## Layer-by-layer summary

| Layer | Technology | What it does |
|---|---|---|
| Source | OpenAQ v3, Open-Meteo Archive API | External public data |
| Ingestion | Python `requests`, orchestrated by Airflow | Pulls raw JSON, no transformation |
| Raw/landing | Filesystem (`data/raw/`) | Immutable copy of each day's API response, one file per source per date |
| Staging | Postgres `staging` schema | Structured but unvalidated/unnormalized rows, plus a run-level ingestion log |
| Transformation | Python (pandas) inside Airflow tasks | Range validation, dedup, unit normalization, weather join |
| Storage (clean) | Postgres `clean` schema | Deduplicated, unit-normalized fact table + rejected-record log |
| Storage (mart/gold) | Postgres `mart` schema | Daily aggregates by station/pollutant and by city |
| Analytics | Streamlit | Reads directly from `mart` tables, 5 views (trend, category mix, pollutant contribution, city comparison, station detail) |
| MLOps (Part 2, out of scope here) | MLflow, FastAPI, Docker | Not implemented in Part 1 by design |
