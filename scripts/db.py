import os

from sqlalchemy import create_engine
from sqlalchemy.engine import Engine


def get_engine() -> Engine:
    user = os.environ["POSTGRES_USER"]
    password = os.environ["POSTGRES_PASSWORD"]
    host = os.environ.get("AQI_DB_HOST", "postgres")
    port = os.environ.get("AQI_DB_PORT", "5432")
    name = os.environ["AQI_DB_NAME"]
    return create_engine(f"postgresql+psycopg2://{user}:{password}@{host}:{port}/{name}")
