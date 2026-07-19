"""Boot-time seeding: the global master profile and the master user."""

import logging

from psycopg.types.json import Json

from app.config import settings
from app.db import get_connection
from app.permissions import RESOURCES
from app.security import hash_password

logger = logging.getLogger(__name__)

MASTER_PROFILE = "Master"


def bootstrap_master() -> None:
    """Create the master profile and user if absent. Never touches an existing
    master — the password setting is a first-run seed, not a reset."""
    with get_connection() as conn:
        profile = conn.execute(
            "SELECT id FROM user_profiles WHERE tenant_id IS NULL AND name = %s",
            (MASTER_PROFILE,),
        ).fetchone()
        if profile is None:
            profile = conn.execute(
                """INSERT INTO user_profiles (tenant_id, name, permissions)
                   VALUES (NULL, %s, %s) RETURNING id""",
                (
                    MASTER_PROFILE,
                    Json({r: ["view", "create", "edit", "delete"] for r in RESOURCES}),
                ),
            ).fetchone()

        exists = conn.execute(
            "SELECT 1 FROM users WHERE is_master = TRUE LIMIT 1"
        ).fetchone()
        if exists:
            return

        conn.execute(
            """INSERT INTO users (tenant_id, profile_id, email, name,
                                  password_hash, is_master)
               VALUES (NULL, %s, %s, %s, %s, TRUE)""",
            (
                profile["id"],
                settings.master_email,
                settings.master_name,
                hash_password(settings.master_password),
            ),
        )
        logger.info("master user created: %s", settings.master_email)
