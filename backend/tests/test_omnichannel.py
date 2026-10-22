"""Camada omnichannel: provisionamento e SSO delegados à ponte."""

import threading

import pytest
import uvicorn
from app.config import settings
from fastapi import FastAPI, Request
from tests.conftest import auth

pytestmark = pytest.mark.integration


def _fake_bridge():
    app = FastAPI()
    seen: dict = {}

    @app.post("/admin/tenants")
    async def tenants(request: Request):
        seen["tenant"] = await request.json()
        seen["auth"] = request.headers.get("authorization")
        return {"status": "ok", "chatwoot_account_id": 4242, "created": True}

    @app.post("/admin/users")
    async def users(request: Request):
        seen["user"] = await request.json()
        return {"status": "ok", "chatwoot_user_id": 77, "created": True}

    @app.post("/admin/ai-config")
    async def ai_config(request: Request):
        seen["ai_config"] = await request.json()
        return {"status": "ok", "degraded": False, "autopilot": True}

    @app.get("/admin/ai-config/{tenant_id}")
    async def ai_config_list(tenant_id: str):
        return {"default": None, "inboxes": [{"chatwoot_inbox_id": 12, "name": "WhatsApp"}]}

    @app.get("/admin/sso/{tenant_id}/{user_id}")
    async def sso(tenant_id: str, user_id: str):
        seen["sso"] = (tenant_id, user_id)
        return {"url": "https://chatwoot.example/sso/abc"}

    config = uvicorn.Config(app, host="127.0.0.1", port=0, log_level="error")
    server = uvicorn.Server(config)
    threading.Thread(target=server.run, daemon=True).start()
    while not server.started:
        pass
    return server, server.servers[0].sockets[0].getsockname()[1], seen


def test_status_reports_when_the_layer_is_off(client, tenant_admin, monkeypatch):
    monkeypatch.setattr(settings, "bridge_url", "")
    r = client.get("/api/omnichannel/status", headers=auth(tenant_admin["token"]))
    assert r.status_code == 200
    assert r.json()["configured"] is False


def test_inbox_show_progress_defaults_off_persists_and_is_not_reset(
    client, tenant_admin, monkeypatch
):
    server, port, seen = _fake_bridge()
    monkeypatch.setattr(settings, "bridge_url", f"http://127.0.0.1:{port}")
    monkeypatch.setattr(settings, "bridge_admin_token", "token-ponte")
    h = auth(tenant_admin["token"])
    try:
        r = client.put("/api/omnichannel/inboxes/12/ia", json={"autopilot": True}, headers=h)
        assert r.status_code == 200, r.text
        assert r.json()["show_progress"] is False  # padrão: corta tudo, só a resposta final
        assert seen["ai_config"]["show_progress"] is False

        r = client.put(
            "/api/omnichannel/inboxes/12/ia",
            json={"autopilot": True, "show_progress": True},
            headers=h,
        )
        assert r.json()["show_progress"] is True and seen["ai_config"]["show_progress"] is True
        listed = client.get("/api/omnichannel/inboxes", headers=h).json()
        assert listed["inboxes"][0]["ai"]["show_progress"] is True

        # salvar de novo sem o campo não derruba a permissão
        r = client.put("/api/omnichannel/inboxes/12/ia", json={"autopilot": False}, headers=h)
        assert r.json()["show_progress"] is True

        r = client.put(
            "/api/omnichannel/inboxes/12/ia", json={"show_progress": False}, headers=h
        )
        assert r.json()["show_progress"] is False
    finally:
        server.should_exit = True
