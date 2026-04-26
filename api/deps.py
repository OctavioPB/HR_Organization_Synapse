"""FastAPI dependency providers."""

import os
from typing import Generator

import psycopg2
import psycopg2.extensions
import psycopg2.extras


def get_db() -> Generator[psycopg2.extensions.connection, None, None]:
    """Yield a psycopg2 connection; commit on success, rollback on exception.

    Usage in route handlers:
        def my_route(conn = Depends(get_db)):
            with conn.cursor() as cur:
                cur.execute(...)
    """
    conn = psycopg2.connect(
        host=os.environ.get("POSTGRES_HOST", "localhost"),
        port=int(os.environ.get("POSTGRES_PORT", "5432")),
        dbname=os.environ.get("POSTGRES_DB", "org_synapse"),
        user=os.environ.get("POSTGRES_USER", "opb"),
        password=os.environ.get("POSTGRES_PASSWORD", "changeme"),
        cursor_factory=psycopg2.extras.RealDictCursor,
    )
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
