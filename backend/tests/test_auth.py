import uuid
from datetime import UTC, datetime, timedelta

import psycopg
import pytest
from app.config import settings
from app.security import hash_token
from tests.conftest import auth

pytestmark = pytest.mark.integration


def test_login_returns_a_token_and_me_describes_the_user(client, master_token):
    r = client.get("/api/auth/me", headers=auth(master_token))
    assert r.status_code == 200
    body = r.json()
    assert body["email"] == settings.master_email
    assert body["is_master"] is True


def test_login_with_wrong_password_is_rejected(client):
    r = client.post(
        "/api/auth/login",
        json={"email": settings.master_email, "password": "errada"},
    )
    assert r.status_code == 401


def test_login_with_unknown_email_is_rejected(client):
    r = client.post(
        "/api/auth/login",
        json={"email": "ninguem@nowhere.com", "password": "seja-o-que-for"},
    )
    assert r.status_code == 401


def test_request_without_token_is_unauthorized(client):
    assert client.get("/api/auth/me").status_code == 401


def test_garbage_token_is_unauthorized(client):
    assert client.get("/api/auth/me", headers=auth("nao-e-um-token")).status_code == 401


def test_logout_revokes_the_session(client, master_token):
    assert client.post("/api/auth/logout", headers=auth(master_token)).status_code == 200
    assert client.get("/api/auth/me", headers=auth(master_token)).status_code == 401


def _age_session(token: str, *, expires_at=None, last_activity_at=None):
    sets, values = [], []
    if expires_at is not None:
        sets.append("expires_at = %s")
        values.append(expires_at)
    if last_activity_at is not None:
        sets.append("last_activity_at = %s")
        values.append(last_activity_at)
    values.append(hash_token(token))
    with psycopg.connect(settings.database_url) as conn:
        conn.execute(
            f"UPDATE sessions SET {', '.join(sets)} WHERE token_hash = %s", tuple(values)
        )
        conn.commit()


def test_expired_session_is_rejected(client, master_token):
    _age_session(master_token, expires_at=datetime.now(UTC) - timedelta(minutes=1))
    assert client.get("/api/auth/me", headers=auth(master_token)).status_code == 401


def test_idle_session_is_rejected(client, master_token):
    idle = datetime.now(UTC) - timedelta(
        minutes=settings.session_idle_minutes + 5
    )
    _age_session(master_token, last_activity_at=idle)
    assert client.get("/api/auth/me", headers=auth(master_token)).status_code == 401


def test_deactivated_user_cannot_use_an_open_session(client, master_token, tenant_admin):
    client.put(
        f"/api/users/{tenant_admin['user']['id']}",
        json={"is_active": False},
        headers=auth(master_token),
    )
    assert client.get("/api/auth/me", headers=auth(tenant_admin["token"])).status_code == 401


def test_deactivating_a_tenant_locks_out_its_users(client, master_token, tenant, tenant_admin):
    client.put(
        f"/api/tenants/{tenant['id']}",
        json={"is_active": False},
        headers=auth(master_token),
    )
    assert client.get("/api/auth/me", headers=auth(tenant_admin["token"])).status_code == 401
    assert (
        client.post(
            "/api/auth/login",
            json={"email": tenant_admin["email"], "password": "senha-forte-123"},
        ).status_code
        == 401
    )


def test_session_tokens_are_not_stored_in_plaintext(client, master_token):
    with psycopg.connect(settings.database_url) as conn:
        rows = conn.execute("SELECT token_hash FROM sessions").fetchall()
    stored = {r[0] for r in rows}
    assert master_token not in stored
    assert hash_token(master_token) in stored


def test_password_hashes_are_never_returned(client, master_token, tenant):
    email = f"u-{uuid.uuid4().hex[:8]}@acme.com"
    r = client.post(
        "/api/users",
        json={
            "email": email,
            "name": "Fulano",
            "password": "senha-forte-123",
            "tenant_id": tenant["id"],
        },
        headers=auth(master_token),
    )
    assert "password" not in r.text and "hash" not in r.text
