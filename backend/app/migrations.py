"""Minimal SQL migration runner.

Migrations are plain SQL files in backend/migrations/, named NNNN_slug.sql.
Applied in filename order inside a transaction each; applied filenames are
recorded in schema_migrations. Runs automatically on backend boot.
"""

import logging
from pathlib import Path

from app.db import get_connection

logger = logging.getLogger(__name__)

MIGRATIONS_DIR = Path(__file__).resolve().parent.parent / "migrations"

_BOOTSTRAP = """
CREATE TABLE IF NOT EXISTS schema_migrations (
    filename TEXT PRIMARY KEY,
    applied_at TIMESTAMPTZ NOT NULL DEFAULT now()
)
"""


def run_migrations() -> list[str]:
    """Apply pending migrations. Returns the list of filenames applied."""
    applied: list[str] = []
    with get_connection() as conn:
        conn.execute(_BOOTSTRAP)
        done = {
            r["filename"]
            for r in conn.execute("SELECT filename FROM schema_migrations").fetchall()
        }
        for path in sorted(MIGRATIONS_DIR.glob("*.sql")):
            if path.name in done:
                continue
            logger.info("applying migration %s", path.name)
            conn.execute(path.read_text(encoding="utf-8"))
            conn.execute(
                "INSERT INTO schema_migrations (filename) VALUES (%s)", (path.name,)
            )
            applied.append(path.name)
    return applied
