import uuid

import psycopg
import pytest
from app.config import settings
from app.migrations import run_migrations
from fastapi.testclient import TestClient


@pytest.fixture(scope="session")
def _schema():
    """Apply migrations once for the whole integration session."""
    run_migrations()


@pytest.fixture
def client(_schema):
    """Client with the API mounted and the master seeded.

    Identity tables are truncated between tests so ordering never matters.
    """
    with psycopg.connect(
        settings.database_url, connect_timeout=settings.db_connect_timeout
    ) as conn:
        conn.execute("TRUNCATE tenants, users, user_profiles, sessions CASCADE")
        conn.commit()

    from app.bootstrap import bootstrap_master
    from app.main import app

    bootstrap_master()
    return TestClient(app)


@pytest.fixture
def master_token(client):
    r = client.post(
        "/api/auth/login",
        json={"email": settings.master_email, "password": settings.master_password},
    )
    assert r.status_code == 200, r.text
    return r.json()["token"]


def auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
def tenant(client, master_token):
    key = f"acme-{uuid.uuid4().hex[:8]}"
    r = client.post(
        "/api/tenants",
        json={"name": "ACME", "tenant_key": key},
        headers=auth(master_token),
    )
    assert r.status_code == 201, r.text
    return r.json()


@pytest.fixture
def tenant_admin(client, master_token, tenant):
    """An admin user inside `tenant`, plus their token."""
    profiles = client.get("/api/user-profiles", headers=auth(master_token)).json()
    admin_profile = next(
        p
        for p in profiles
        if p["tenant_id"] == tenant["id"] and p["name"] == "Administrador"
    )
    email = f"admin-{uuid.uuid4().hex[:8]}@acme.com"
    r = client.post(
        "/api/users",
        json={
            "email": email,
            "name": "Admin ACME",
            "password": "senha-forte-123",
            "profile_id": admin_profile["id"],
            "tenant_id": tenant["id"],
        },
        headers=auth(master_token),
    )
    assert r.status_code == 201, r.text
    token = client.post(
        "/api/auth/login", json={"email": email, "password": "senha-forte-123"}
    ).json()["token"]
    return {"user": r.json(), "token": token, "email": email}
