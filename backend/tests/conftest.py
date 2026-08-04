import threading
import uuid

import psycopg
import pytest
import uvicorn
from app.config import settings
from app.migrations import run_migrations
from fastapi import FastAPI
from fastapi.responses import StreamingResponse
from fastapi.testclient import TestClient
from guardas import exigir_banco_descartavel


@pytest.fixture(scope="session", autouse=True)
def _recusar_banco_de_producao():
    """Primeira coisa da sessão, antes de qualquer fixture que escreva."""
    exigir_banco_descartavel(settings.database_url)


@pytest.fixture(scope="session", autouse=True)
def _isolate_local_storage(tmp_path_factory):
    """Keep uploads created by tests out of the repository tree."""
    settings.storage_backend = "local"
    settings.s3_bucket = ""
    settings.gcs_bucket = ""
    settings.storage_local_dir = str(tmp_path_factory.mktemp("backend-storage"))


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


def _fake_kernel_app() -> FastAPI:
    app = FastAPI()

    @app.post("/v1/runs")
    async def runs(payload: dict):
        async def stream():
            yield 'event: token\ndata: {"text": "olá"}\n\n'
            yield 'event: token\ndata: {"text": " mundo"}\n\n'
            yield 'event: done\ndata: {"text": "olá mundo"}\n\n'

        return StreamingResponse(stream(), media_type="text/event-stream")

    return app


@pytest.fixture(scope="session")
def fake_kernel():
    """Real HTTP server on a random port — the backend talks to it over the wire."""
    config = uvicorn.Config(
        _fake_kernel_app(), host="127.0.0.1", port=0, log_level="error"
    )
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    while not server.started:
        pass
    port = server.servers[0].sockets[0].getsockname()[1]
    yield f"http://127.0.0.1:{port}"
    server.should_exit = True
    thread.join(timeout=5)


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
