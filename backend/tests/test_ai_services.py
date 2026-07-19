import uuid

import psycopg
import pytest
from app.config import settings
from tests.conftest import auth

pytestmark = pytest.mark.integration


def _create(client, token, tenant_id=None, **overrides):
    body = {
        "name": f"svc-{uuid.uuid4().hex[:6]}",
        "provider": "gemini",
        "model": "gemini-flash-latest",
        "api_key": "chave-super-secreta-123",
    }
    body.update(overrides)
    if tenant_id:
        body["tenant_id"] = tenant_id
    return client.post("/api/ai-services", json=body, headers=auth(token))


def test_create_and_list_never_expose_the_key(client, tenant_admin):
    r = _create(client, tenant_admin["token"])
    assert r.status_code == 201, r.text
    assert "chave-super-secreta-123" not in r.text
    assert r.json()["has_key"] is True

    listing = client.get("/api/ai-services", headers=auth(tenant_admin["token"]))
    assert "chave-super-secreta-123" not in listing.text


def test_key_is_encrypted_at_rest(client, tenant_admin):
    _create(client, tenant_admin["token"], name="cifrada")
    with psycopg.connect(settings.database_url) as conn:
        rows = conn.execute("SELECT api_key_encrypted FROM ai_services").fetchall()
    stored = " ".join(r[0] or "" for r in rows)
    assert "chave-super-secreta-123" not in stored
    assert stored.strip()  # something was stored — and it is ciphertext


def test_invalid_provider_is_rejected(client, tenant_admin):
    r = _create(client, tenant_admin["token"], provider="nao-existe")
    assert r.status_code == 400


def test_openai_compatible_requires_api_base(client, tenant_admin):
    r = _create(client, tenant_admin["token"], provider="openai-compatible")
    assert r.status_code == 400


def test_tenant_isolation_on_services(client, master_token, tenant_admin):
    key = f"iso-{uuid.uuid4().hex[:6]}"
    other = client.post(
        "/api/tenants",
        json={"name": "Iso", "tenant_key": key},
        headers=auth(master_token),
    ).json()
    _create(client, master_token, tenant_id=other["id"], name="do-outro")

    visible = client.get("/api/ai-services", headers=auth(tenant_admin["token"])).json()
    assert all(s["tenant_id"] != other["id"] for s in visible)


def test_chat_uses_the_tenant_service(client, tenant_admin, fake_kernel, monkeypatch):
    """With an active AI service, the model spec sent to the kernel must carry
    the decrypted key — proven via a capturing fake kernel."""
    captured = {}

    import json as _json
    import threading

    import uvicorn
    from fastapi import FastAPI
    from fastapi.responses import StreamingResponse

    app = FastAPI()

    @app.post("/v1/runs")
    async def runs(payload: dict):
        captured.update(payload)

        async def stream():
            yield 'event: done\ndata: {"text": "ok"}\n\n'

        return StreamingResponse(stream(), media_type="text/event-stream")

    config = uvicorn.Config(app, host="127.0.0.1", port=0, log_level="error")
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    while not server.started:
        pass
    port = server.servers[0].sockets[0].getsockname()[1]

    _create(client, tenant_admin["token"], name="pro-chat", api_key="a-chave-do-tenant")

    from app.config import settings as backend_settings

    backend_settings.kernel_url = f"http://127.0.0.1:{port}"
    email = f"c-{uuid.uuid4().hex[:6]}@acme.com"
    client.post(
        "/api/users",
        json={"email": email, "name": "C", "password": "senha-forte-123"},
        headers=auth(tenant_admin["token"]),
    )
    utoken = client.post(
        "/api/auth/login", json={"email": email, "password": "senha-forte-123"}
    ).json()["token"]

    with client.stream(
        "POST", "/api/chat/send", json={"message": "oi"}, headers=auth(utoken)
    ) as response:
        "".join(response.iter_text())

    server.should_exit = True
    assert captured["model"]["provider"] == "gemini"
    assert captured["model"]["api_key"] == "a-chave-do-tenant"
    assert _json.dumps(captured)  # sanity


def test_chat_without_service_falls_back_to_stub(client, master_token, fake_kernel):
    key = f"semsvc-{uuid.uuid4().hex[:6]}"
    t = client.post(
        "/api/tenants",
        json={"name": "SemSvc", "tenant_key": key},
        headers=auth(master_token),
    ).json()
    email = f"u-{uuid.uuid4().hex[:6]}@x.com"
    client.post(
        "/api/users",
        json={
            "email": email,
            "name": "U",
            "password": "senha-forte-123",
            "tenant_id": t["id"],
        },
        headers=auth(master_token),
    )
    utoken = client.post(
        "/api/auth/login", json={"email": email, "password": "senha-forte-123"}
    ).json()["token"]

    from app.config import settings as backend_settings

    backend_settings.kernel_url = fake_kernel
    with client.stream(
        "POST", "/api/chat/send", json={"message": "oi"}, headers=auth(utoken)
    ) as response:
        body = "".join(response.iter_text())
    assert "event: done" in body
