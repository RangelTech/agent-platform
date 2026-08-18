"""Automatic 9Router provisioning dispatched by `create_tenant` (infra-06).

The real script SSHes into the VPS and touches DNS/TLS — never run for real
in tests. These tests only prove the dispatch logic: the flag gate, the
background task updating `router_provisioning_status`, and the frontend-
visible fields on `/ai-router/status`. `subprocess.run` is monkeypatched so
no process is ever spawned.
"""

import types
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
    """With the flag on, a successful subprocess run flips pending -> ready."""
    monkeypatch.setattr(settings, "router_auto_provision_enabled", True)

    def fake_run(cmd, capture_output, text, timeout):
        return types.SimpleNamespace(returncode=0, stdout="ok", stderr="")

    monkeypatch.setattr("app.routes.tenants.subprocess.run", fake_run)

    tenant = _create_tenant(client, master_token)
    # BackgroundTasks finish before TestClient hands back the response, so
    # the row is already updated by the time we read it back here.
    row = client.get("/api/tenants", headers=auth(master_token)).json()
    updated = next(t for t in row if t["id"] == tenant["id"])
    assert updated["router_provisioning_status"] == "ready"


def test_background_task_marks_failed_on_nonzero_exit(client, master_token, monkeypatch):
    """A failing subprocess must not affect the tenant row besides the status
    fields — the tenant keeps existing (spec point 4)."""
    monkeypatch.setattr(settings, "router_auto_provision_enabled", True)

    def fake_run(cmd, capture_output, text, timeout):
        return types.SimpleNamespace(returncode=1, stdout="", stderr="ssh: connection refused")

    monkeypatch.setattr("app.routes.tenants.subprocess.run", fake_run)

    tenant = _create_tenant(client, master_token)
    row = client.get("/api/tenants", headers=auth(master_token)).json()
    updated = next(t for t in row if t["id"] == tenant["id"])
    assert updated["router_provisioning_status"] == "failed"
    assert "connection refused" in (updated["router_provisioning_error"] or "")
    assert updated["is_active"] is True  # tenant still exists and usable


def test_background_task_marks_failed_on_timeout(client, master_token, monkeypatch):
    monkeypatch.setattr(settings, "router_auto_provision_enabled", True)

    def fake_run(cmd, capture_output, text, timeout):
        import subprocess

        raise subprocess.TimeoutExpired(cmd, timeout)

    monkeypatch.setattr("app.routes.tenants.subprocess.run", fake_run)

    tenant = _create_tenant(client, master_token)
    row = client.get("/api/tenants", headers=auth(master_token)).json()
    updated = next(t for t in row if t["id"] == tenant["id"])
    assert updated["router_provisioning_status"] == "failed"
