"""Automatic LiteLLM Team provisioning dispatched by `create_tenant`
(infra-06, migrated off 9Router in infra-04 — achado real 24/08/2026).

The real path calls `litellm_client.create_team`/`add_model_to_team`/
`generate_key` over HTTP — never hit a real LiteLLM in tests. These tests
only prove the dispatch logic: the flag gate, the background task updating
`router_provisioning_status`, and the frontend-visible fields on
`/ai-router/status`. `litellm_client`'s functions are monkeypatched so no
HTTP call is ever made.
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


async def _fake_create_team(base_url, master_key, *, team_alias):
    return {"team_id": f"team-{team_alias}"}


async def _fake_add_model_to_team(base_url, master_key, *, team_id, model_name):
    return None


async def _fake_generate_key(base_url, master_key, *, team_id, key_alias):
    return f"sk-fake-{key_alias}"


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
    """With the flag on, a successful LiteLLM provisioning flips pending -> ready."""
    monkeypatch.setattr(settings, "router_auto_provision_enabled", True)
    monkeypatch.setattr("app.routes.tenants.resolver_segredo", lambda nome, padrao="": "fake")
    monkeypatch.setattr("app.routes.tenants.litellm_client.create_team", _fake_create_team)
    monkeypatch.setattr("app.routes.tenants.litellm_client.add_model_to_team", _fake_add_model_to_team)
    monkeypatch.setattr("app.routes.tenants.litellm_client.generate_key", _fake_generate_key)

    tenant = _create_tenant(client, master_token)
    # BackgroundTasks finish before TestClient hands back the response, so
    # the row is already updated by the time we read it back here.
    row = client.get("/api/tenants", headers=auth(master_token)).json()
    updated = next(t for t in row["items"] if t["id"] == tenant["id"])
    assert updated["router_provisioning_status"] == "ready"


def test_background_task_marks_failed_on_error(client, master_token, monkeypatch):
    """A failing provisioning call must not affect the tenant row besides the
    status fields — the tenant keeps existing (spec point 4)."""
    monkeypatch.setattr(settings, "router_auto_provision_enabled", True)
    monkeypatch.setattr("app.routes.tenants.resolver_segredo", lambda nome, padrao="": "fake")

    async def _boom(*args, **kwargs):
        raise RuntimeError("litellm indisponível: connection refused")

    monkeypatch.setattr("app.routes.tenants.litellm_client.create_team", _boom)

    tenant = _create_tenant(client, master_token)
    row = client.get("/api/tenants", headers=auth(master_token)).json()
    updated = next(t for t in row["items"] if t["id"] == tenant["id"])
    assert updated["router_provisioning_status"] == "failed"
    assert "connection refused" in (updated["router_provisioning_error"] or "")
    assert updated["is_active"] is True  # tenant still exists and usable


def test_background_task_marks_failed_on_timeout(client, master_token, monkeypatch):
    monkeypatch.setattr(settings, "router_auto_provision_enabled", True)
    monkeypatch.setattr(settings, "router_provision_timeout_seconds", 0)
    monkeypatch.setattr("app.routes.tenants.resolver_segredo", lambda nome, padrao="": "fake")

    async def _slow(*args, **kwargs):
        import asyncio

        await asyncio.sleep(1)
        return {"team_id": "x"}

    monkeypatch.setattr("app.routes.tenants.litellm_client.create_team", _slow)

    tenant = _create_tenant(client, master_token)
    row = client.get("/api/tenants", headers=auth(master_token)).json()
    updated = next(t for t in row["items"] if t["id"] == tenant["id"])
    assert updated["router_provisioning_status"] == "failed"
