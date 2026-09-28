"""Trilha de auditoria (critério de aceite 15): ator, dispositivo, sessão,
comando e resultado ficam registrados; isolamento por tenant."""

import uuid

import pytest

from tests.conftest import auth

pytestmark = pytest.mark.integration


def _pair(client, email, name="Notebook"):
    return client.post(
        "/api/hermes/devices/pair",
        json={"email": email, "password": "senha-forte-123", "device_name": name},
    ).json()


def test_pairing_and_revoking_are_audited(client, tenant_admin):
    device = _pair(client, tenant_admin["email"])
    client.post(
        f"/api/hermes/devices/{device['device']['id']}/revoke",
        headers=auth(tenant_admin["token"]),
    )

    log = client.get("/api/hermes/audit", headers=auth(tenant_admin["token"])).json()
    actions = [e["action"] for e in log]
    assert "hermes.device.paired" in actions
    assert "hermes.device.revoked" in actions
    paired_entry = next(e for e in log if e["action"] == "hermes.device.paired")
    assert paired_entry["resource_id"] == device["device"]["id"]
    assert paired_entry["result"] == "ok"


def test_command_and_approval_decisions_are_audited(client, tenant_admin):
    device = _pair(client, tenant_admin["email"])
    session = client.post(
        "/api/hermes/sessions",
        json={"external_session_id": f"ext-{uuid.uuid4().hex[:8]}", "status": "running"},
        headers={"Authorization": f"Bearer {device['credential']}"},
    ).json()
    command = client.post(
        f"/api/hermes/sessions/{session['id']}/commands",
        json={"idempotency_key": "k", "payload": {"text": "oi"}},
        headers=auth(tenant_admin["token"]),
    ).json()
    approval = client.post(
        "/api/hermes/approvals",
        json={"session_id": session["id"], "command_id": command["id"], "title": "x"},
        headers={"Authorization": f"Bearer {device['credential']}"},
    ).json()
    client.post(
        f"/api/hermes/approvals/{approval['id']}/decide",
        json={"decision": "approved"},
        headers=auth(tenant_admin["token"]),
    )

    log = client.get("/api/hermes/audit", headers=auth(tenant_admin["token"])).json()
    actions = [e["action"] for e in log]
    assert "hermes.command.created" in actions
    assert "hermes.approval.approved" in actions


def test_audit_log_is_tenant_isolated(client, tenant_admin, other_tenant_admin):
    _pair(client, other_tenant_admin["email"])
    mine = client.get("/api/hermes/audit", headers=auth(tenant_admin["token"])).json()
    assert all(e["tenant_id"] == tenant_admin["user"]["tenant_id"] for e in mine)
