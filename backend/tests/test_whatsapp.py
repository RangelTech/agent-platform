"""Canal WhatsApp: conexão por integração, webhook e isolamento."""

import threading
import uuid

import psycopg
import pytest
import uvicorn
from app.config import settings
from app.wapi_client import extract_message
from fastapi import FastAPI, Request
from tests.conftest import auth

pytestmark = pytest.mark.integration


def _integration(client, token, channel="whatsapp", **extra):
    payload = {"name": f"wa-{uuid.uuid4().hex[:6]}", "channel": channel}
    payload.update(extra)
    r = client.post("/api/integrations", json=payload, headers=auth(token))
    assert r.status_code == 201, r.text
    return r.json()


def _fake_wapi(status_code=200):
    app = FastAPI()
    sent: list[dict] = []

    @app.get("/instance/status")
    async def status():
        return {"connected": True}

    @app.post("/message/send-text")
    async def send(request: Request):
        body = await request.json()
        sent.append(body)
        return {"messageId": "abc"}

    config = uvicorn.Config(app, host="127.0.0.1", port=0, log_level="error")
    server = uvicorn.Server(config)
    threading.Thread(target=server.run, daemon=True).start()
    while not server.started:
        pass
    return server, server.servers[0].sockets[0].getsockname()[1], sent


def test_extract_message_ignores_non_message_events():
    assert extract_message({"event": "message.status", "phone": "5511999", "text": "oi"}) is None
    assert extract_message({"fromMe": True, "phone": "5511999", "text": "oi"}) is None
    assert extract_message({"phone": "5511999@c.us", "text": ""}) is None


def test_extract_message_reads_the_common_envelopes():
    plain = extract_message({"phone": "5511999@c.us", "text": "olá"})
    assert plain == {"phone": "5511999", "text": "olá", "message_id": ""}

    nested = extract_message(
        {
            "messageId": "m1",
            "key": {"remoteJid": "5511888@s.whatsapp.net", "fromMe": False},
            "message": {"conversation": "quero um lanche"},
        }
    )
    assert nested["phone"] == "5511888"
    assert nested["text"] == "quero um lanche"
    assert nested["message_id"] == "m1"


def test_token_is_encrypted_and_never_returned(client, tenant_admin):
    integration = _integration(client, tenant_admin["token"])
    token = f"wapi-{uuid.uuid4().hex}"
    r = client.put(
        f"/api/integrations/{integration['id']}/whatsapp",
        json={"instance_id": "inst-1", "token": token},
        headers=auth(tenant_admin["token"]),
    )
    assert r.status_code == 200, r.text
    assert token not in r.text
    assert r.json()["has_token"] is True

    with psycopg.connect(settings.database_url) as conn:
        stored = conn.execute("SELECT token_encrypted FROM whatsapp_connections").fetchall()
    assert stored and all(token not in (s[0] or "") for s in stored)


def test_api_channel_integration_rejects_whatsapp_connection(client, tenant_admin):
    integration = _integration(client, tenant_admin["token"], channel="api")
    r = client.put(
        f"/api/integrations/{integration['id']}/whatsapp",
        json={"instance_id": "inst-1", "token": "x"},
        headers=auth(tenant_admin["token"]),
    )
    assert r.status_code == 400


def test_connection_test_hits_the_gateway(client, tenant_admin):
    integration = _integration(client, tenant_admin["token"])
    server, port, _ = _fake_wapi()
    try:
        client.put(
            f"/api/integrations/{integration['id']}/whatsapp",
            json={
                "instance_id": "inst-1",
                "token": "tok",
                "api_base": f"http://127.0.0.1:{port}",
            },
            headers=auth(tenant_admin["token"]),
        )
        r = client.post(
            f"/api/integrations/{integration['id']}/whatsapp/test",
            headers=auth(tenant_admin["token"]),
        )
    finally:
        server.should_exit = True
    assert r.status_code == 200
    assert r.json()["ok"] is True


def test_webhook_always_answers_200_and_logs_the_raw_body(client, tenant_admin):
    """Erro no webhook faria a W-API reenviar em loop: nunca devolver 5xx."""
    integration = _integration(client, tenant_admin["token"])

    unknown = client.post(f"/webhooks/whatsapp/{uuid.uuid4()}", json={"data": 1})
    assert unknown.status_code == 200
    assert unknown.json()["status"] == "ignored"

    broken = client.post(
        f"/webhooks/whatsapp/{integration['id']}",
        content=b"nao-e-json",
        headers={"Content-Type": "application/json"},
    )
    assert broken.status_code == 200

    with psycopg.connect(settings.database_url) as conn:
        rows = conn.execute(
            "SELECT status, raw_body FROM whatsapp_webhook_events ORDER BY created_at DESC LIMIT 2"
        ).fetchall()
    assert any("nao-e-json" in (r[1] or "") for r in rows)


def test_webhook_processes_a_message_and_answers_on_whatsapp(
    client, tenant_admin, fake_kernel, monkeypatch
):
    monkeypatch.setattr(settings, "kernel_url", fake_kernel)
    integration = _integration(client, tenant_admin["token"])
    server, port, sent = _fake_wapi()
    try:
        client.put(
            f"/api/integrations/{integration['id']}/whatsapp",
            json={
                "instance_id": "inst-1",
                "token": "tok",
                "api_base": f"http://127.0.0.1:{port}",
            },
            headers=auth(tenant_admin["token"]),
        )
        r = client.post(
            f"/webhooks/whatsapp/{integration['id']}",
            json={"phone": "5511977776666@c.us", "text": "quanto custa?"},
        )
        assert r.status_code == 200 and r.json()["status"] == "accepted"

        # O processamento roda em background; espera o envio chegar ao gateway.
        import time

        deadline = time.time() + 15
        while not sent and time.time() < deadline:
            time.sleep(0.2)
    finally:
        server.should_exit = True

    assert sent, "resposta não foi enviada ao WhatsApp"
    assert sent[0]["phone"] == "5511977776666"

    # A conversa aparece no Chat da empresa, marcada como canal WhatsApp.
    chats = client.get("/api/chats", headers=auth(tenant_admin["token"])).json()
    conversation = next(c for c in chats if c["channel"] == "whatsapp")
    assert conversation["external_contact"] == "5511977776666"
    messages = client.get(
        f"/api/chats/{conversation['id']}/messages", headers=auth(tenant_admin["token"])
    ).json()
    assert [m["role"] for m in messages] == ["user", "assistant"]
    assert messages[0]["content"] == "quanto custa?"


def test_whatsapp_conversations_are_tenant_isolated(client, master_token, tenant_admin):
    other = client.post(
        "/api/tenants",
        json={"name": "Outro WA", "tenant_key": f"wa-{uuid.uuid4().hex[:6]}"},
        headers=auth(master_token),
    ).json()
    with psycopg.connect(settings.database_url) as conn:
        conn.execute(
            """INSERT INTO chats (tenant_id, user_id, title, channel, external_contact)
               VALUES (%s, NULL, 'WhatsApp 551100000000', 'whatsapp', '551100000000')""",
            (other["id"],),
        )
        conn.commit()

    chats = client.get("/api/chats", headers=auth(tenant_admin["token"])).json()
    assert all(c.get("external_contact") != "551100000000" for c in chats)
