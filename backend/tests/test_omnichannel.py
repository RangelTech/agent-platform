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


def test_provision_creates_the_account_and_mirrors_the_user(client, tenant_admin, monkeypatch):
    server, port, seen = _fake_bridge()
    monkeypatch.setattr(settings, "bridge_url", f"http://127.0.0.1:{port}")
    monkeypatch.setattr(settings, "bridge_admin_token", "token-ponte")
    try:
        r = client.post("/api/omnichannel/provision", json={}, headers=auth(tenant_admin["token"]))
        status = client.get("/api/omnichannel/status", headers=auth(tenant_admin["token"]))
    finally:
        server.should_exit = True

    assert r.status_code == 200, r.text
    assert r.json()["chatwoot_account_id"] == 4242
    assert status.json()["chatwoot_account_id"] == 4242
    assert seen["auth"] == "Bearer token-ponte"
    # O primeiro usuário precisa entrar como administrador: é ele que a ponte
    # usa depois para criar inbox e operar a conta.
    assert seen["user"]["role"] == "administrator"
    assert seen["user"]["email"] == tenant_admin["email"]


def test_sso_delegates_to_the_bridge(client, tenant_admin, monkeypatch):
    server, port, seen = _fake_bridge()
    monkeypatch.setattr(settings, "bridge_url", f"http://127.0.0.1:{port}")
    monkeypatch.setattr(settings, "bridge_admin_token", "token-ponte")
    try:
        r = client.get("/api/omnichannel/sso", headers=auth(tenant_admin["token"]))
    finally:
        server.should_exit = True

    assert r.status_code == 200
    assert r.json()["url"] == "https://chatwoot.example/sso/abc"
    assert seen["sso"] == (tenant_admin["user"]["tenant_id"], tenant_admin["user"]["id"])


def test_master_has_no_operational_inbox(client, master_token, monkeypatch):
    monkeypatch.setattr(settings, "bridge_url", "http://127.0.0.1:1")
    monkeypatch.setattr(settings, "bridge_admin_token", "x")
    r = client.get("/api/omnichannel/sso", headers=auth(master_token))
    assert r.status_code == 400
