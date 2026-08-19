"""Public API: key auth, modes (sync/stream/webhook), session continuity,
rate limiting and tenant scoping."""

import base64
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
def kernel_with_artifact():
    """Fake kernel whose run emits an `artifact` event (kind=image) — this is
    the shape `generate_pix_charge`/`generate_chart` produce for real, and the
    thing `/v1/messages` used to silently drop before artifacts were plumbed
    through to machine-to-machine callers (the Chatwoot bridge included)."""
    captured = {}
    app = FastAPI()

    @app.post("/v1/runs")
    async def runs(payload: dict):
        captured["last"] = payload

        async def stream():
            yield (
                'event: artifact\ndata: {"artifact_id": "art-1", '
                '"kind": "image", "title": "QR Code PIX"}\n\n'
            )
            yield 'event: done\ndata: {"text": "aqui está o código copia-e-cola"}\n\n'

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


def test_audio_attachment_reaches_the_kernel_with_a_transcription_spec(
    client, integration, capturing_kernel
):
    """The Chatwoot bridge path: a voice note with no caption, sent as base64.

    This is the plumbing this ticket adds — `PublicMessageIn.attachments` ->
    `_store_public_attachments` (persists via the same `app.attachments`
    module `routes/chats.py` uses) -> kernel payload carries `attachments` +
    `transcription`, exactly like the frontend chat's multipart path already
    does. We don't assert on the transcribed text here (that's `attachments.py`
    /`transcribe()` inside the kernel, covered by the kernel's own tests) —
    only that the bridge's audio-only message actually reaches the kernel
    with something to transcribe, instead of vanishing as empty text.
    """
    audio_bytes = b"ID3 fake audio bytes for a test ogg/opus voice note"
    headers = {"Authorization": f"Bearer {integration['api_key']}"}

    r = client.post(
        "/v1/messages",
        json={
            "external_session_id": "whatsapp-5511999999999",
            "attachments": [
                {
                    "kind": "audio",
                    "name": "nota-de-voz.ogg",
                    "content_type": "audio/ogg",
                    "data_base64": base64.b64encode(audio_bytes).decode(),
                }
            ],
        },
        headers=headers,
    )
    assert r.status_code == 200, r.text
    assert r.json()["reply"] == "resposta da api"

    sent = capturing_kernel["last"]
    assert sent["message"]  # placeholder text, never empty -> RunRequest requires it
    assert len(sent["attachments"]) == 1
    attachment = sent["attachments"][0]
    assert attachment["kind"] == "audio"
    assert attachment["name"] == "nota-de-voz.ogg"
    assert attachment["content_type"] == "audio/ogg"
    assert attachment["storage_path"]  # persisted, kernel loads it back by path

    # No real ai_services configured for this tenant -> falls back to the
    # test-only stub spec (see `app.attachments.transcription_spec`), same
    # fallback the frontend path uses under pytest.
    assert sent["transcription"]["provider"] == "stub"


def test_attachment_with_invalid_base64_is_a_client_error(client, integration, capturing_kernel):
    headers = {"Authorization": f"Bearer {integration['api_key']}"}
    r = client.post(
        "/v1/messages",
        json={
            "attachments": [
                {
                    "kind": "audio",
                    "name": "x.ogg",
                    "content_type": "audio/ogg",
                    "data_base64": "***not-base64***",
                }
            ]
        },
        headers=headers,
    )
    assert r.status_code == 400


def test_text_message_still_required_without_attachments(client, integration, capturing_kernel):
    headers = {"Authorization": f"Bearer {integration['api_key']}"}
    r = client.post("/v1/messages", json={"message": "   "}, headers=headers)
    assert r.status_code == 422


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


def test_sync_mode_surfaces_artifacts(client, integration, kernel_with_artifact):
    """Root-cause test for the PIX/chart delivery gap: before this, `/v1/messages`
    threw away every `artifact` SSE event and only kept `done` text, so a
    machine-to-machine caller (the Chatwoot bridge) never even knew an image
    (chart PNG, PIX QR code) had been produced."""
    r = client.post(
        "/v1/messages",
        json={"message": "gera o pix"},
        headers={"Authorization": f"Bearer {integration['api_key']}"},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["reply"] == "aqui está o código copia-e-cola"
    assert body["artifacts"] == [
        {"artifact_id": "art-1", "kind": "image", "title": "QR Code PIX"}
    ]


def test_webhook_mode_carries_artifacts_too(client, tenant_admin, kernel_with_artifact):
    received = {}
    hook = FastAPI()

    @hook.post("/callback")
    async def callback(request: Request):
        received["body"] = await request.body()
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
            "name": f"hook-art-{uuid.uuid4().hex[:6]}",
            "webhook_url": f"http://127.0.0.1:{port}/callback",
        },
        headers=auth(tenant_admin["token"]),
    ).json()

    r = client.post(
        "/v1/messages",
        json={"message": "gera o pix", "mode": "webhook"},
        headers={"Authorization": f"Bearer {created['api_key']}"},
    )
    assert r.status_code == 200

    deadline = time.time() + 10
    while "body" not in received and time.time() < deadline:
        time.sleep(0.2)
    server.should_exit = True
    assert "body" in received

    payload = json.loads(received["body"])
    assert payload["artifacts"][0]["kind"] == "image"


def test_artifact_endpoint_serves_bytes_scoped_to_the_owning_tenant(
    client, tenant_admin, integration
):
    """`GET /v1/artifacts/{id}` is the piece that lets the bridge (bearer key,
    no user session) download the image bytes `/api/artifacts/*` would have
    required a logged-in session for."""
    import psycopg
    from app.config import settings

    fake_path = f"/tmp/does-not-need-to-exist-{uuid.uuid4().hex}.png"
    with psycopg.connect(settings.database_url) as conn:
        row = conn.execute(
            """INSERT INTO artifacts
                   (tenant_id, chat_id, agent_name, kind, title, storage_path, content_type)
               VALUES (%s, 'chat-1', 'agente', 'image', 'QR Code PIX', %s, 'image/png')
               RETURNING id""",
            (tenant_admin["user"]["tenant_id"], fake_path),
        ).fetchone()
        conn.commit()
    artifact_id = str(row[0])

    import app.artifacts_io as artifacts_io

    original = artifacts_io.load_bytes
    artifacts_io.load_bytes = lambda _path: b"\x89PNGfakebytes"
    try:
        r = client.get(
            f"/v1/artifacts/{artifact_id}",
            headers={"Authorization": f"Bearer {integration['api_key']}"},
        )
    finally:
        artifacts_io.load_bytes = original
    assert r.status_code == 200
    assert r.content == b"\x89PNGfakebytes"
    assert r.headers["content-type"] == "image/png"


def test_artifact_endpoint_rejects_a_foreign_tenants_integration(
    client, master_token, tenant_admin, integration
):
    other_tenant = client.post(
        "/api/tenants",
        json={"name": "Outro Tenant", "tenant_key": f"outro-{uuid.uuid4().hex[:6]}"},
        headers=auth(master_token),
    ).json()

    import psycopg
    from app.config import settings

    with psycopg.connect(settings.database_url) as conn:
        row = conn.execute(
            """INSERT INTO artifacts
                   (tenant_id, chat_id, agent_name, kind, title, storage_path, content_type)
               VALUES (%s, 'chat-1', 'agente', 'image', 'QR Code do outro', %s, 'image/png')
               RETURNING id""",
            (other_tenant["id"], f"/tmp/x-{uuid.uuid4().hex}.png"),
        ).fetchone()
        conn.commit()
    artifact_id = str(row[0])

    r = client.get(
        f"/v1/artifacts/{artifact_id}",
        headers={"Authorization": f"Bearer {integration['api_key']}"},
    )
    assert r.status_code == 404
