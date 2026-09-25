"""
db/db_utils.py
──────────────
SQLAlchemy database utilities for the AQI pipeline.
Provides a singleton engine, session factory, and helper functions
for upsert operations across all pipeline layers.
"""

from __future__ import annotations

import os
import math
import logging
from contextlib import contextmanager
from typing import Generator

import pandas as pd
from dotenv import load_dotenv
from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import sessionmaker, Session
from sqlalchemy.exc import SQLAlchemyError

load_dotenv()
logger = logging.getLogger(__name__)

# ── Connection string ─────────────────────────────────────────────────────────

def _build_dsn() -> str:
    """Build PostgreSQL DSN from environment variables."""
    user = os.getenv("POSTGRES_USER", "aqi_user")
    password = os.getenv("POSTGRES_PASSWORD", "aqi_secret_password_change_me")
    host = os.getenv("POSTGRES_HOST", "localhost")
    port = os.getenv("POSTGRES_PORT", "5432")
    db = os.getenv("POSTGRES_DB", "aqi_db")
    return f"postgresql+psycopg2://{user}:{password}@{host}:{port}/{db}"


# ── Singleton engine ──────────────────────────────────────────────────────────

_engine: Engine | None = None


def get_engine() -> Engine:
    """Return (and lazily create) the singleton SQLAlchemy engine."""
    global _engine
    if _engine is None:
        dsn = _build_dsn()
        _engine = create_engine(
            dsn,
            pool_size=5,
            max_overflow=10,
            pool_pre_ping=True,
            echo=False,
        )
        logger.info("Database engine created: %s", dsn.split("@")[-1])
    return _engine


# ── Session factory ───────────────────────────────────────────────────────────

def get_session_factory() -> sessionmaker:
    return sessionmaker(bind=get_engine(), autocommit=False, autoflush=False)


@contextmanager
def get_session() -> Generator[Session, None, None]:
    """Context manager that provides a transactional database session."""
    SessionLocal = get_session_factory()
    session = SessionLocal()
    try:
        yield session
        session.commit()
    except SQLAlchemyError as exc:
        session.rollback()
        logger.error("Database error — rolling back: %s", exc)
        raise
    finally:
        session.close()


# ── Convenience helpers ───────────────────────────────────────────────────────

def execute_sql(sql: str, params: dict | None = None) -> None:
    """Execute a raw SQL statement (DDL or DML)."""
    engine = get_engine()
    with engine.begin() as conn:
        conn.execute(text(sql), params or {})


def df_to_table(
    df: pd.DataFrame,
    table: str,
    schema: str,
    if_exists: str = "append",
    index: bool = False,
) -> int:
    """
    Write a DataFrame to a PostgreSQL table.

    Parameters
    ----------
    df        : DataFrame to write
    table     : target table name
    schema    : target schema ('raw', 'staging', 'cleaned', 'gold')
    if_exists : 'append' (default) | 'replace' | 'fail'
    index     : whether to write DataFrame index as a column

    Returns
    -------
    int : number of rows written
    """
    if df.empty:
        logger.warning("df_to_table called with empty DataFrame for %s.%s", schema, table)
        return 0
    engine = get_engine()
    df.to_sql(
        name=table,
        con=engine,
        schema=schema,
        if_exists=if_exists,
        index=index,
        method="multi",
        chunksize=1000,
    )
    logger.info("Wrote %d rows to %s.%s", len(df), schema, table)
    return len(df)


def read_sql(sql: str, params: dict | None = None) -> pd.DataFrame:
    """Execute a SELECT query and return results as a DataFrame."""
    engine = get_engine()
    return pd.read_sql(text(sql), con=engine, params=params or {})


def upsert_dataframe(
    df: pd.DataFrame,
    table: str,
    schema: str,
    conflict_columns: list[str],
    update_columns: list[str],
) -> int:
    """
    PostgreSQL INSERT ... ON CONFLICT DO UPDATE (upsert) for a DataFrame.

    Parameters
    ----------
    df               : data to upsert
    table            : target table
    schema           : target schema
    conflict_columns : columns forming the unique constraint
    update_columns   : columns to update on conflict

    Returns
    -------
    int : number of rows upserted
    """
    if df.empty:
        return 0

    engine = get_engine()
    full_table = f"{schema}.{table}"
    columns = list(df.columns)
    col_names = ", ".join(columns)
    placeholders = ", ".join([f":{c}" for c in columns])
    conflict_cols = ", ".join(conflict_columns)
    update_set = ", ".join([f"{c} = EXCLUDED.{c}" for c in update_columns])

    sql = f"""
        INSERT INTO {full_table} ({col_names})
        VALUES ({placeholders})
        ON CONFLICT ({conflict_cols})
        DO UPDATE SET {update_set}
    """

    # pandas keeps NaN (not None) through df.where()/fillna() for numeric
    # columns, and NaN sent to an integer/numeric SQL column errors out —
    # scrub per-record instead.
    records = df.to_dict(orient="records")
    for record in records:
        for key, value in record.items():
            if isinstance(value, float) and math.isnan(value):
                record[key] = None

    with engine.begin() as conn:
        conn.execute(text(sql), records)

    logger.info("Upserted %d rows into %s", len(df), full_table)
    return len(df)


def test_connection() -> bool:
    """Return True if the database is reachable."""
    try:
        with get_engine().connect() as conn:
            conn.execute(text("SELECT 1"))
        return True
    except SQLAlchemyError as exc:
        logger.error("Connection test failed: %s", exc)
        return False
