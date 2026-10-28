FROM python:3.12-slim

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends curl \
    && rm -rf /var/lib/apt/lists/*

COPY requirements-ml.txt .
RUN pip install --no-cache-dir -r requirements-ml.txt \
    fastapi==0.115.0 "uvicorn[standard]==0.30.6" \
    pandas==2.1.4 numpy==1.26.4 SQLAlchemy==2.0.31 psycopg2-binary==2.9.9 python-dotenv==1.0.1 \
    requests tenacity==8.5.0

COPY api/ ./api/
COPY ml/ ./ml/
COPY db/ ./db/
COPY etl/ ./etl/
COPY ingestion/ ./ingestion/

ENV PYTHONPATH=/app
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=40s \
    CMD curl --fail http://localhost:8000/health || exit 1

CMD ["uvicorn", "api.main:app", "--host", "0.0.0.0", "--port", "8000"]
