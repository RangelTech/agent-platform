"""Chat gateway tests.

The kernel is faked with a local ASGI SSE app so these tests exercise the
backend's relay/persistence logic without needing the kernel process. The
true end-to-end path (backend + kernel + Postgres) is covered by compose
smoke and Playwright.
"""

import json

import pytest
from tests.conftest import auth

pytestmark = pytest.mark.integration


@pytest.fixture
def chat_user(client, master_token, tenant):
    """A regular tenant user able to chat."""
    import uuid as _uuid

    email = f"chatter-{_uuid.uuid4().hex[:8]}@acme.com"
    client.post(
        "/api/users",
        json={
            "email": email,
            "name": "Chatter",
            "password": "senha-forte-123",
            "tenant_id": tenant["id"],
        },
        headers=auth(master_token),
    )
    token = client.post(
        "/api/auth/login", json={"email": email, "password": "senha-forte-123"}
    ).json()["token"]
    return {"token": token}


def _send(client, token, message, chat_id=None, kernel_url=None):
    from app.config import settings

    if kernel_url:
        settings.kernel_url = kernel_url
    body = {"message": message}
    if chat_id:
        body["chat_id"] = chat_id
    with client.stream(
        "POST", "/api/chat/send", json=body, headers=auth(token)
    ) as response:
        text = "".join(response.iter_text())
    return response.status_code, text


def test_send_creates_chat_streams_and_persists(client, chat_user, fake_kernel):
    status, body = _send(client, chat_user["token"], "olá!", kernel_url=fake_kernel)
    assert status == 200
    assert "event: chat" in body
    assert body.count("event: token") == 2
    assert "event: done" in body

    chat_id = json.loads(
        next(
            line[6:]
            for line in body.splitlines()
            if line.startswith("data: ") and "chat_id" in line
        )
    )["chat_id"]

    chats = client.get("/api/chats", headers=auth(chat_user["token"])).json()
    assert any(c["id"] == chat_id for c in chats)

    messages = client.get(
        f"/api/chats/{chat_id}/messages", headers=auth(chat_user["token"])
    ).json()
    roles = [m["role"] for m in messages]
    assert roles == ["user", "assistant"]
    assert messages[1]["content"] == "olá mundo"


def test_followup_reuses_the_chat(client, chat_user, fake_kernel):
    _, body = _send(client, chat_user["token"], "primeira", kernel_url=fake_kernel)
    chat_id = json.loads(
        next(
            line[6:]
            for line in body.splitlines()
            if line.startswith("data: ") and "chat_id" in line
        )
    )["chat_id"]

    _send(client, chat_user["token"], "segunda", chat_id=chat_id, kernel_url=fake_kernel)
    messages = client.get(
        f"/api/chats/{chat_id}/messages", headers=auth(chat_user["token"])
    ).json()
    assert [m["role"] for m in messages] == ["user", "assistant", "user", "assistant"]


def test_users_cannot_read_each_others_chats(client, master_token, tenant, chat_user, fake_kernel):
    _, body = _send(client, chat_user["token"], "confidencial", kernel_url=fake_kernel)
    chat_id = json.loads(
        next(
            line[6:]
            for line in body.splitlines()
            if line.startswith("data: ") and "chat_id" in line
        )
    )["chat_id"]

    import uuid as _uuid

    email = f"outro-{_uuid.uuid4().hex[:8]}@acme.com"
    client.post(
        "/api/users",
        json={
            "email": email,
            "name": "Outro",
            "password": "senha-forte-123",
            "tenant_id": tenant["id"],
        },
        headers=auth(master_token),
    )
    other_token = client.post(
        "/api/auth/login", json={"email": email, "password": "senha-forte-123"}
    ).json()["token"]

    assert (
        client.get(
            f"/api/chats/{chat_id}/messages", headers=auth(other_token)
        ).status_code
        == 404
    )
    assert client.get("/api/chats", headers=auth(other_token)).json() == []


def test_master_cannot_chat(client, master_token):
    # _ensure_chat rejects before any streaming starts.
    status, _ = _send(client, master_token, "oi")
    assert status == 400


def test_kernel_down_yields_an_error_event(client, chat_user):
    status, body = _send(
        client, chat_user["token"], "oi", kernel_url="http://127.0.0.1:59999"
    )
    assert status == 200
    assert "event: error" in body


def test_multipart_send_stores_attachment_and_forwards_descriptor(
    client, master_token, fake_kernel
):
    """Multipart /api/chat/send: the upload lands in storage, the message row
    records it, and the kernel payload carries the descriptor."""
    import io
    import threading
    import uuid as _uuid

    import uvicorn
    from fastapi import FastAPI
    from fastapi.responses import StreamingResponse

    captured = {}
    app = FastAPI()

    @app.post("/v1/runs")
    async def runs(payload: dict):
        captured.update(payload)

        async def stream():
            yield 'event: done\ndata: {"text": "li o anexo"}\n\n'

        return StreamingResponse(stream(), media_type="text/event-stream")

    config = uvicorn.Config(app, host="127.0.0.1", port=0, log_level="error")
    server = uvicorn.Server(config)
    threading.Thread(target=server.run, daemon=True).start()
    while not server.started:
        pass
    port = server.servers[0].sockets[0].getsockname()[1]

    key = f"mm-{_uuid.uuid4().hex[:6]}"
    t = client.post(
        "/api/tenants",
        json={"name": "MM", "tenant_key": key},
        headers=auth(master_token),
    ).json()
    email = f"mm-{_uuid.uuid4().hex[:6]}@x.com"
    client.post(
        "/api/users",
        json={"email": email, "name": "MM", "password": "senha-forte-123", "tenant_id": t["id"]},
        headers=auth(master_token),
    )
    utoken = client.post(
        "/api/auth/login", json={"email": email, "password": "senha-forte-123"}
    ).json()["token"]

    from app.config import settings as backend_settings

    backend_settings.kernel_url = f"http://127.0.0.1:{port}"
    with client.stream(
        "POST",
        "/api/chat/send",
        data={"message": "leia isso"},
        files={"files": ("nota.txt", io.BytesIO(b"conteudo da nota"), "text/plain")},
        headers=auth(utoken),
    ) as response:
        body = "".join(response.iter_text())
    server.should_exit = True

    assert "event: done" in body
    assert len(captured["attachments"]) == 1
    att = captured["attachments"][0]
    assert att["kind"] == "file" and att["name"] == "nota.txt"
    from app.artifacts_io import load_bytes

    assert load_bytes(att["storage_path"]) == b"conteudo da nota"

    chats = client.get("/api/chats", headers=auth(utoken)).json()
    messages = client.get(f"/api/chats/{chats[0]['id']}/messages", headers=auth(utoken)).json()
    assert messages[0]["attachments"] == [{"kind": "file", "name": "nota.txt"}]
