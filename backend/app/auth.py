"""Session authentication: create, resolve and revoke sessions."""

from datetime import UTC, datetime, timedelta

from fastapi import Depends, HTTPException, Request

from app.config import settings
from app.db import get_connection
from app.permissions import has_permission
from app.security import hash_token, new_session_token, verify_password

_USER_QUERY = """
SELECT u.id, u.tenant_id, u.email, u.name, u.is_master, u.is_active,
       u.profile_id, p.permissions, t.is_active AS tenant_is_active
  FROM users u
  LEFT JOIN user_profiles p ON p.id = u.profile_id
  LEFT JOIN tenants t ON t.id = u.tenant_id
"""


def authenticate(email: str, password: str) -> str | None:
    """Validate credentials and open a session. Returns the plaintext token,
    or None when the credentials, the user or the tenant are not usable."""
    with get_connection() as conn:
        row = conn.execute(
            _USER_QUERY + " WHERE lower(u.email) = lower(%s)", (email,)
        ).fetchone()
        if row is None:
            # Waste the same work as a real check so timing does not reveal
            # whether the email exists.
            verify_password(password, "$2b$12$" + "x" * 53)
            return None
        pwd = conn.execute(
            "SELECT password_hash FROM users WHERE id = %s", (row["id"],)
        ).fetchone()
        if not verify_password(password, pwd["password_hash"]):
            return None
        if not row["is_active"]:
            return None
        # A deactivated tenant locks out its users; the master has no tenant.
        if row["tenant_id"] is not None and not row["tenant_is_active"]:
            return None

        token = new_session_token()
        conn.execute(
            """INSERT INTO sessions (token_hash, user_id, expires_at)
               VALUES (%s, %s, %s)""",
            (
                hash_token(token),
                row["id"],
                datetime.now(UTC) + timedelta(hours=settings.session_hours),
            ),
        )
        return token


def resolve_session(token: str) -> dict | None:
    """Return the user for a live session, refreshing its activity stamp.
    Expired or idle sessions are revoked on sight."""
    now = datetime.now(UTC)
    with get_connection() as conn:
        session = conn.execute(
            """SELECT token_hash, user_id, expires_at, last_activity_at, revoked_at
                 FROM sessions WHERE token_hash = %s""",
            (hash_token(token),),
        ).fetchone()
        if session is None or session["revoked_at"] is not None:
            return None

        idle_cutoff = now - timedelta(minutes=settings.session_idle_minutes)
        if session["expires_at"] <= now or session["last_activity_at"] <= idle_cutoff:
            conn.execute(
                "UPDATE sessions SET revoked_at = %s WHERE token_hash = %s",
                (now, session["token_hash"]),
            )
            return None

        user = conn.execute(
            _USER_QUERY + " WHERE u.id = %s", (session["user_id"],)
        ).fetchone()
        if user is None or not user["is_active"]:
            return None
        if user["tenant_id"] is not None and not user["tenant_is_active"]:
            return None

        conn.execute(
            "UPDATE sessions SET last_activity_at = %s WHERE token_hash = %s",
            (now, session["token_hash"]),
        )
        return dict(user)


def revoke_session(token: str) -> None:
    with get_connection() as conn:
        conn.execute(
            "UPDATE sessions SET revoked_at = now() WHERE token_hash = %s",
            (hash_token(token),),
        )


def _bearer_token(request: Request) -> str:
    header = request.headers.get("authorization", "")
    scheme, _, token = header.partition(" ")
    if scheme.lower() != "bearer" or not token:
        raise HTTPException(status_code=401, detail="Não autenticado")
    return token


def current_user(request: Request) -> dict:
    """FastAPI dependency: the authenticated user, or 401."""
    user = resolve_session(_bearer_token(request))
    if user is None:
        raise HTTPException(status_code=401, detail="Sessão inválida ou expirada")
    return user


def require(resource: str, action: str):
    """Dependency factory gating a route behind a permission."""

    def dependency(user: dict = Depends(current_user)) -> dict:
        if not has_permission(user, resource, action):
            raise HTTPException(status_code=403, detail="Permissão negada")
        return user

    return dependency
