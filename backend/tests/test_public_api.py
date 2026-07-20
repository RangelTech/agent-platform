"""Public API: key auth, modes (sync/stream/webhook), session continuity,
rate limiting and tenant scoping."""

import hashlib
import hmac
import json
import threading
import time
import uuid

import pytest
import uvicorn
from fastapi import FastAPI, Request
from fastapi.responses import StreamingResponse
from tests.conftest import auth

pytestmark = pytest.mark.integration


@pytest.fixture
def capturing_kernel():
    """Fake kernel that records thread_ids and replies deterministically."""
    captured = {"threads": []}
    app = FastAPI()

    @app.post("/v1/runs")
    async def runs(payload: dict):
        captured["threads"].append(payload["thread_id"])
        captured["last"] = payload

        async def stream():
            yield 'event: token\ndata: {"text": "resposta"}\n\n'
            yield 'event: done\ndata: {"text": "resposta da api"}\n\n'

        return StreamingResponse(stream(), media_type="text/event-stream")

    config = uvicorn.Config(app, host="127.0.0.1", port=0, log_level="error")
    server = uvicorn.Server(config)
    threading.Thread(target=server.run, daemon=True).start()
    while not server.started:
        pass
    port = server.servers[0].sockets[0].getsockname()[1]

    from app.config import settings as backend_settings

    backend_settings.kernel_url = f"http://127.0.0.1:{port}"
    yield captured
    server.should_exit = True


@pytest.fixture
def integration(client, tenant_admin):
    r = client.post(
        "/api/integrations",
        json={"name": f"erp-{uuid.uuid4().hex[:6]}", "rate_limit_per_minute": 60},
        headers=auth(tenant_admin["token"]),
    )
    assert r.status_code == 201, r.text
    return r.json()


def test_key_is_shown_once_and_hashed(client, tenant_admin, integration):
    assert integration["api_key"].startswith("ap_")
    listing = client.get("/api/integrations", headers=auth(tenant_admin["token"])).json()
    assert all("api_key" not in i for i in listing)

    import psycopg
    from app.config import settings

    with psycopg.connect(settings.database_url) as conn:
        stored = conn.execute("SELECT api_key_hash FROM integrations").fetchall()
    assert all(integration["api_key"] not in (s[0] or "") for s in stored)


def test_sync_mode_and_session_continuity(client, integration, capturing_kernel):
    headers = {"Authorization": f"Bearer {integration['api_key']}"}
    r1 = client.post(
        "/v1/messages",
        json={"message": "olá", "external_session_id": "cliente-42"},
        headers=headers,
    )
    assert r1.status_code == 200
    assert r1.json()["reply"] == "resposta da api"

    client.post(
        "/v1/messages",
        json={"message": "continua", "external_session_id": "cliente-42"},
        headers=headers,
    )
    client.post(
        "/v1/messages",
        json={"message": "outra sessão", "external_session_id": "cliente-99"},
        headers=headers,
    )
    threads = capturing_kernel["threads"]
    assert threads[0] == threads[1]  # same external session -> same thread
    assert threads[2] != threads[0]


def test_wrong_and_revoked_keys_are_rejected(client, tenant_admin, integration, capturing_kernel):
    assert (
        client.post(
            "/v1/messages",
            json={"message": "x"},
            headers={"Authorization": "Bearer ap_chave-falsa"},
        ).status_code
        == 401
    )
    client.post(
        f"/api/integrations/{integration['id']}/revoke",
        headers=auth(tenant_admin["token"]),
    )
    assert (
        client.post(
            "/v1/messages",
            json={"message": "x"},
            headers={"Authorization": f"Bearer {integration['api_key']}"},
        ).status_code
        == 401
    )


def test_stream_mode_passes_sse_through(client, integration, capturing_kernel):
    with client.stream(
        "POST",
        "/v1/messages",
        json={"message": "oi", "mode": "stream"},
        headers={"Authorization": f"Bearer {integration['api_key']}"},
    ) as response:
        body = "".join(response.iter_text())
    assert "event: token" in body and "event: done" in body


def test_webhook_mode_delivers_signed_callback(client, tenant_admin, capturing_kernel):
    received = {}
    hook = FastAPI()

    @hook.post("/callback")
    async def callback(request: Request):
        received["body"] = await request.body()
        received["signature"] = request.headers.get("x-agent-signature", "")
        return {"ok": True}

    config = uvicorn.Config(hook, host="127.0.0.1", port=0, log_level="error")
    server = uvicorn.Server(config)
    threading.Thread(target=server.run, daemon=True).start()
    while not server.started:
        pass
    port = server.servers[0].sockets[0].getsockname()[1]

    created = client.post(
        "/api/integrations",
        json={
            "name": f"hook-{uuid.uuid4().hex[:6]}",
            "webhook_url": f"http://127.0.0.1:{port}/callback",
        },
        headers=auth(tenant_admin["token"]),
    ).json()

    r = client.post(
        "/v1/messages",
        json={"message": "processa aí", "mode": "webhook"},
        headers={"Authorization": f"Bearer {created['api_key']}"},
    )
    assert r.status_code == 200 and r.json()["status"] == "accepted"

    deadline = time.time() + 10
    while "body" not in received and time.time() < deadline:
        time.sleep(0.2)
    server.should_exit = True
    assert "body" in received, "webhook não recebido"

    payload = json.loads(received["body"])
    assert payload["reply"] == "resposta da api"
    expected = hmac.new(
        created["webhook_secret"].encode(), received["body"], hashlib.sha256
    ).hexdigest()
    assert received["signature"] == f"sha256={expected}"


def test_rate_limit_trips(client, tenant_admin, capturing_kernel):
    created = client.post(
        "/api/integrations",
        json={"name": f"rl-{uuid.uuid4().hex[:6]}", "rate_limit_per_minute": 2},
        headers=auth(tenant_admin["token"]),
    ).json()
    headers = {"Authorization": f"Bearer {created['api_key']}"}
    assert client.post("/v1/messages", json={"message": "1"}, headers=headers).status_code == 200
    assert client.post("/v1/messages", json={"message": "2"}, headers=headers).status_code == 200
    assert client.post("/v1/messages", json={"message": "3"}, headers=headers).status_code == 429


def test_template_override_must_belong_to_the_tenant(
    client, master_token, integration, capturing_kernel
):
    other = client.post(
        "/api/tenants",
        json={"name": "PX", "tenant_key": f"px-{uuid.uuid4().hex[:6]}"},
        headers=auth(master_token),
    ).json()
    foreign_tpl = client.post(
        "/api/templates",
        json={"name": "deles", "description": "", "tenant_id": other["id"]},
        headers=auth(master_token),
    ).json()

    r = client.post(
        "/v1/messages",
        json={"message": "oi", "template_id": foreign_tpl["id"]},
        headers={"Authorization": f"Bearer {integration['api_key']}"},
    )
    assert r.status_code == 404
