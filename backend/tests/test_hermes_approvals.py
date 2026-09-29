"""Aprovações remotas: o dispositivo solicita, o usuário decide (seção 6.4)."""

import uuid

import pytest
from tests.conftest import auth

pytestmark = pytest.mark.integration


def _pair(client, email, name="Notebook"):
    return client.post(
        "/api/hermes/devices/pair",
        json={"email": email, "password": "senha-forte-123", "device_name": name},
    ).json()


def _device_auth(credential):
    return {"Authorization": f"Bearer {credential}"}


def _session(client, credential):
    return client.post(
        "/api/hermes/sessions",
        json={"external_session_id": f"ext-{uuid.uuid4().hex[:8]}", "status": "running"},
        headers=_device_auth(credential),
    ).json()


def test_device_requests_approval_and_session_moves_to_waiting(client, tenant_admin):
    device = _pair(client, tenant_admin["email"])
    session = _session(client, device["credential"])
    r = client.post(
        "/api/hermes/approvals",
        json={"session_id": session["id"], "title": "Executar rm -rf build/"},
        headers=_device_auth(device["credential"]),
    )
    assert r.status_code == 201
    assert r.json()["status"] == "pending"

    updated_session = client.get(
        f"/api/hermes/sessions/{session['id']}", headers=auth(tenant_admin["token"])
    ).json()
    assert updated_session["status"] == "waiting_approval"


def test_user_approves_and_device_sees_the_decision(client, tenant_admin):
    device = _pair(client, tenant_admin["email"])
    session = _session(client, device["credential"])
    approval = client.post(
        "/api/hermes/approvals",
        json={"session_id": session["id"], "title": "Rodar migração"},
        headers=_device_auth(device["credential"]),
    ).json()

    decided = client.post(
        f"/api/hermes/approvals/{approval['id']}/decide",
        json={"decision": "approved", "justification": "confirmado com o time"},
        headers=auth(tenant_admin["token"]),
    )
    assert decided.status_code == 200
    assert decided.json()["status"] == "approved"

    polled = client.get(
        f"/api/hermes/approvals/{approval['id']}", headers=_device_auth(device["credential"])
    )
    assert polled.status_code == 200
    assert polled.json()["status"] == "approved"


def test_cannot_decide_an_already_decided_approval(client, tenant_admin):
    device = _pair(client, tenant_admin["email"])
    session = _session(client, device["credential"])
    approval = client.post(
        "/api/hermes/approvals",
        json={"session_id": session["id"], "title": "x"},
        headers=_device_auth(device["credential"]),
    ).json()
    client.post(
        f"/api/hermes/approvals/{approval['id']}/decide",
        json={"decision": "rejected"},
        headers=auth(tenant_admin["token"]),
    )
    again = client.post(
        f"/api/hermes/approvals/{approval['id']}/decide",
        json={"decision": "approved"},
        headers=auth(tenant_admin["token"]),
    )
    assert again.status_code == 409


def test_approvals_are_tenant_isolated(client, tenant_admin, other_tenant_admin):
    device = _pair(client, other_tenant_admin["email"])
    session = _session(client, device["credential"])
    approval = client.post(
        "/api/hermes/approvals",
        json={"session_id": session["id"], "title": "x"},
        headers=_device_auth(device["credential"]),
    ).json()

    leaked = client.get("/api/hermes/approvals", headers=auth(tenant_admin["token"])).json()
    assert approval["id"] not in [a["id"] for a in leaked]

    r = client.post(
        f"/api/hermes/approvals/{approval['id']}/decide",
        json={"decision": "approved"},
        headers=auth(tenant_admin["token"]),
    )
    assert r.status_code == 404
