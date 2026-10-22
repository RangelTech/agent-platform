"""Provisionamento do tenant no gateway, disparado por `create_tenant`.

Os testes nunca chamam um gateway real: provam apenas o despacho, o estado
persistido e a ausência de efeito colateral quando a feature está desligada.
"""

import uuid

import pytest
from app.config import settings
from tests.conftest import auth

pytestmark = pytest.mark.integration


def _create_tenant(client, master_token, **overrides):
    key = f"prov-{uuid.uuid4().hex[:8]}"
    payload = {"name": "Provisiona Ltda", "tenant_key": key, **overrides}
    r = client.post("/api/tenants", json=payload, headers=auth(master_token))
    assert r.status_code == 201, r.text
    return r.json()


async def _fake_provision_gateway(base_url, admin_token, *, tenant_id, name):
    return f"sk-fake-{tenant_id}-bridge", f"sk-fake-{tenant_id}-ai-assist"


def test_tenant_is_created_with_pending_status_by_default(client, master_token):
    """Flag off (the test/dev default): tenant creation must not be blocked
    or altered by provisioning — status stays 'pending', nothing dispatched
    for real."""
    assert settings.router_auto_provision_enabled is False
    tenant = _create_tenant(client, master_token)
    assert tenant["router_provisioning_status"] == "pending"
    assert tenant["router_provisioning_error"] is None

    status = client.get(
        "/api/ai-router/status",
        headers=auth(
            client.post(
                "/api/auth/login",
                json={"email": settings.master_email, "password": settings.master_password},
            ).json()["token"]
        ),
    )
    # master has no tenant_id, so this just proves the route still answers;
    # the real per-tenant assertion is the direct DB-backed field above.
    assert status.status_code == 200


def test_background_task_marks_ready_on_success(client, master_token, monkeypatch):
    """Com a flag ligada, o gateway pronto muda pending -> ready."""
    monkeypatch.setattr(settings, "router_auto_provision_enabled", True)
    monkeypatch.setattr("app.router_provisioning.resolver_segredo", lambda nome, padrao="": "fake")
    monkeypatch.setattr(
        "app.router_provisioning.gateway_client.provision_tenant", _fake_provision_gateway
    )

    tenant = _create_tenant(client, master_token)
    # BackgroundTasks finish before TestClient hands back the response, so
    # the row is already updated by the time we read it back here.
    row = client.get("/api/tenants", headers=auth(master_token)).json()
    updated = next(t for t in row["items"] if t["id"] == tenant["id"])
    assert updated["router_provisioning_status"] == "ready"


def test_background_task_marks_failed_on_error(client, master_token, monkeypatch):
    """Uma falha no Gateway não altera a empresa além do
    status fields — the tenant keeps existing (spec point 4)."""
    monkeypatch.setattr(settings, "router_auto_provision_enabled", True)
    monkeypatch.setattr("app.router_provisioning.resolver_segredo", lambda nome, padrao="": "fake")

    async def _boom(*args, **kwargs):
        raise RuntimeError("gateway indisponível: connection refused")

    monkeypatch.setattr("app.router_provisioning.gateway_client.provision_tenant", _boom)

    tenant = _create_tenant(client, master_token)
    row = client.get("/api/tenants", headers=auth(master_token)).json()
    updated = next(t for t in row["items"] if t["id"] == tenant["id"])
    assert updated["router_provisioning_status"] == "failed"
    assert "connection refused" in (updated["router_provisioning_error"] or "")
    assert updated["is_active"] is True  # tenant still exists and usable


def test_background_task_marks_failed_on_timeout(client, master_token, monkeypatch):
    monkeypatch.setattr(settings, "router_auto_provision_enabled", True)
    monkeypatch.setattr(settings, "router_provision_timeout_seconds", 0)
    monkeypatch.setattr("app.router_provisioning.resolver_segredo", lambda nome, padrao="": "fake")

    async def _slow(*args, **kwargs):
        import asyncio

        await asyncio.sleep(1)
        return "bridge", "assist"

    monkeypatch.setattr("app.router_provisioning.gateway_client.provision_tenant", _slow)

    tenant = _create_tenant(client, master_token)
    row = client.get("/api/tenants", headers=auth(master_token)).json()
    updated = next(t for t in row["items"] if t["id"] == tenant["id"])
    assert updated["router_provisioning_status"] == "failed"
