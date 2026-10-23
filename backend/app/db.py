from contextlib import contextmanager

import psycopg
from psycopg.rows import dict_row

from app.config import settings

_pool = None


def _get_pool():
    """Pool por processo (so com DB_POOL_MAX>0). Atras do pgbouncer em modo transacao nao se
    usa prepared statement de servidor (prepare_threshold=None)."""
    global _pool
    if _pool is None:
        from psycopg_pool import ConnectionPool

        _pool = ConnectionPool(
            settings.database_url,
            min_size=2,
            max_size=settings.db_pool_max,
            timeout=10,
            max_idle=300,
            kwargs={
                "row_factory": dict_row,
                "connect_timeout": settings.db_connect_timeout,
                "prepare_threshold": None,
            },
            open=True,
        )
    return _pool


@contextmanager
def get_connection():
    """Yield a psycopg3 connection with dict rows. Commits on success,
    rolls back on error.

    connect_timeout is always set: without it a dead or unreachable database
    blocks the caller indefinitely (a boot with an unreachable Cloud SQL would
    hang the container instead of failing its health check)."""
    if settings.db_pool_max > 0:
        # o pool faz commit/rollback ao sair do bloco, como o caminho sem pool
        with _get_pool().connection() as pooled:
            yield pooled
        return
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
