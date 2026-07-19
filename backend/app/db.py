from contextlib import contextmanager

import psycopg
from psycopg.rows import dict_row

from app.config import settings


@contextmanager
def get_connection():
    """Yield a psycopg3 connection with dict rows. Commits on success,
    rolls back on error.

    connect_timeout is always set: without it a dead or unreachable database
    blocks the caller indefinitely (a boot with an unreachable Cloud SQL would
    hang the container instead of failing its health check)."""
    conn = psycopg.connect(
        settings.database_url,
        row_factory=dict_row,
        connect_timeout=settings.db_connect_timeout,
    )
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
