import logging
import os
from contextlib import contextmanager
from typing import Generator

import psycopg2
import psycopg2.pool
from psycopg2.extensions import connection as PgConnection

logger = logging.getLogger(__name__)

_pool: psycopg2.pool.SimpleConnectionPool | None = None


def _conn_params() -> dict[str, str | int]:
    return {
        "host": os.environ.get("POSTGRES_HOST", "localhost"),
        "port": int(os.environ.get("POSTGRES_PORT", "5432")),
        "dbname": os.environ.get("POSTGRES_DB", "org_synapse"),
        "user": os.environ.get("POSTGRES_USER", "opb"),
        "password": os.environ.get("POSTGRES_PASSWORD", "changeme"),
    }


def get_pool() -> psycopg2.pool.SimpleConnectionPool:
    global _pool
    if _pool is None:
        params = _conn_params()
        logger.debug("Initializing DB pool → %s/%s", params["host"], params["dbname"])
        _pool = psycopg2.pool.SimpleConnectionPool(minconn=1, maxconn=10, **params)
    return _pool


@contextmanager
def get_conn() -> Generator[PgConnection, None, None]:
    """Yield a pooled connection; commit on success, rollback on error."""
    pool = get_pool()
    conn = pool.getconn()
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        pool.putconn(conn)


def close_pool() -> None:
    global _pool
    if _pool is not None:
        _pool.closeall()
        _pool = None
