"""Comandos remotos: criação idempotente, reivindicação atômica, máquina de
estados e isolamento entre dispositivos/tenants (seção 6.4/8.3)."""

import uuid

import psycopg
import pytest
from app.config import settings
from tests.conftest import auth

pytestmark = pytest.mark.integration


def _pair(client, email, name="Notebook"):
    return client.post(
        "/api/hermes/devices/pair",
        json={"email": email, "password": "senha-forte-123", "device_name": name},
    ).json()


def _device_auth(credential):
    return {"Authorization": f"Bearer {credential}"}


def _session(client, credential, external_id=None):
    return client.post(
        "/api/hermes/sessions",
        json={
            "external_session_id": external_id or f"ext-{uuid.uuid4().hex[:8]}",
            "status": "idle",
        },
        headers=_device_auth(credential),
    ).json()


def test_create_command_with_repeated_idempotency_key_returns_the_same_command(
    client, tenant_admin
):
    device = _pair(client, tenant_admin["email"])
    session = _session(client, device["credential"])
    body = {"idempotency_key": "retry-1", "payload": {"text": "faça a revisão"}}

    url = f"/api/hermes/sessions/{session['id']}/commands"
    first = client.post(url, json=body, headers=auth(tenant_admin["token"]))
    second = client.post(url, json=body, headers=auth(tenant_admin["token"]))
    assert first.status_code == 201
    assert second.status_code == 201
    assert first.json()["id"] == second.json()["id"]


def test_creating_a_command_notifies_the_devices_channel(client, tenant_admin):
    """O Hermes Relay (Fase B) escuta este canal pra avisar o dispositivo na
    hora em vez de esperar o poll -- ver hermes_commands.py:create_command.
    Só dispara depois que a transação de fato comita (não em rollback)."""
    device = _pair(client, tenant_admin["email"])
    session = _session(client, device["credential"])

    listener = psycopg.connect(settings.database_url, autocommit=True)
    listener.execute("LISTEN hermes_commands")
    try:
        r = client.post(
            f"/api/hermes/sessions/{session['id']}/commands",
            json={"idempotency_key": "notify-1", "payload": {"text": "oi"}},
            headers=auth(tenant_admin["token"]),
        )
        assert r.status_code == 201

        notified_device_ids = []
        gen = listener.notifies(timeout=5)
        for note in gen:
            notified_device_ids.append(note.payload)
            break
        assert notified_device_ids == [device["device"]["id"]]
    finally:
        listener.close()


def test_a_retry_of_an_existing_command_does_not_notify_again(client, tenant_admin):
    device = _pair(client, tenant_admin["email"])
    session = _session(client, device["credential"])
    body = {"idempotency_key": "notify-retry-1", "payload": {"text": "oi"}}
    url = f"/api/hermes/sessions/{session['id']}/commands"
    client.post(url, json=body, headers=auth(tenant_admin["token"]))

    listener = psycopg.connect(settings.database_url, autocommit=True)
    listener.execute("LISTEN hermes_commands")
    try:
        r = client.post(url, json=body, headers=auth(tenant_admin["token"]))
        assert r.status_code == 201

        got_one = False
        for _note in listener.notifies(timeout=1):
            got_one = True
            break
        assert got_one is False
    finally:
        listener.close()


def test_cannot_command_an_ended_session(client, tenant_admin):
    device = _pair(client, tenant_admin["email"])
    session = client.post(
        "/api/hermes/sessions",
        json={"external_session_id": "s", "status": "ended"},
        headers=_device_auth(device["credential"]),
    ).json()
    r = client.post(
        f"/api/hermes/sessions/{session['id']}/commands",
        json={"idempotency_key": "k", "payload": {}},
        headers=auth(tenant_admin["token"]),
    )
    assert r.status_code == 409


def test_pending_commands_claim_is_atomic_and_scoped_per_device(client, tenant_admin):
    device_a = _pair(client, tenant_admin["email"], name="A")
    device_b = _pair(client, tenant_admin["email"], name="B")
    session_a = _session(client, device_a["credential"])

    client.post(
        f"/api/hermes/sessions/{session_a['id']}/commands",
        json={"idempotency_key": "k1", "payload": {"text": "oi"}},
        headers=auth(tenant_admin["token"]),
    )

    for_a = client.get(
        "/api/hermes/commands/pending", headers=_device_auth(device_a["credential"])
    ).json()
    for_b = client.get(
        "/api/hermes/commands/pending", headers=_device_auth(device_b["credential"])
    ).json()
    assert len(for_a) == 1
    assert for_a[0]["status"] == "delivered"
    assert for_b == []

    # Uma segunda chamada do mesmo dispositivo não vê o comando de novo — já
    # foi reivindicado (claim atômico via SKIP LOCKED).
    again = client.get(
        "/api/hermes/commands/pending", headers=_device_auth(device_a["credential"])
    ).json()
    assert again == []


def test_command_state_machine_rejects_invalid_transitions(client, tenant_admin):
    device = _pair(client, tenant_admin["email"])
    session = _session(client, device["credential"])
    command = client.post(
        f"/api/hermes/sessions/{session['id']}/commands",
        json={"idempotency_key": "k", "payload": {}},
        headers=auth(tenant_admin["token"]),
    ).json()

    # Ainda "queued": não pode pular direto para "completed".
    bad = client.post(
        f"/api/hermes/commands/{command['id']}/transition",
        json={"status": "completed"},
        headers=_device_auth(device["credential"]),
    )
    assert bad.status_code == 409

    client.get("/api/hermes/commands/pending", headers=_device_auth(device["credential"]))
    ok = client.post(
        f"/api/hermes/commands/{command['id']}/transition",
        json={"status": "accepted"},
        headers=_device_auth(device["credential"]),
    )
    assert ok.status_code == 200
    assert ok.json()["status"] == "accepted"

    running = client.post(
        f"/api/hermes/commands/{command['id']}/transition",
        json={"status": "running"},
        headers=_device_auth(device["credential"]),
    )
    assert running.status_code == 200

    done = client.post(
        f"/api/hermes/commands/{command['id']}/transition",
        json={"status": "completed", "result": {"summary": "ok"}},
        headers=_device_auth(device["credential"]),
    )
    assert done.status_code == 200
    assert done.json()["status"] == "completed"
    assert done.json()["completed_at"] is not None


def test_another_device_cannot_transition_a_command_it_does_not_own(client, tenant_admin):
    device_a = _pair(client, tenant_admin["email"], name="A")
    device_b = _pair(client, tenant_admin["email"], name="B")
    session_a = _session(client, device_a["credential"])
    command = client.post(
        f"/api/hermes/sessions/{session_a['id']}/commands",
        json={"idempotency_key": "k", "payload": {}},
        headers=auth(tenant_admin["token"]),
    ).json()

    r = client.post(
        f"/api/hermes/commands/{command['id']}/transition",
        json={"status": "accepted"},
        headers=_device_auth(device_b["credential"]),
    )
    assert r.status_code == 404


def test_cancel_active_command_but_not_a_terminal_one(client, tenant_admin):
    device = _pair(client, tenant_admin["email"])
    session = _session(client, device["credential"])
    command = client.post(
        f"/api/hermes/sessions/{session['id']}/commands",
        json={"idempotency_key": "k", "payload": {}},
        headers=auth(tenant_admin["token"]),
    ).json()

    cancelled = client.post(
        f"/api/hermes/commands/{command['id']}/cancel", headers=auth(tenant_admin["token"])
    )
    assert cancelled.status_code == 200
    assert cancelled.json()["status"] == "cancelled"

    again = client.post(
        f"/api/hermes/commands/{command['id']}/cancel", headers=auth(tenant_admin["token"])
    )
    assert again.status_code == 409


def test_commands_are_tenant_isolated(client, tenant_admin, other_tenant_admin):
    device = _pair(client, other_tenant_admin["email"])
    session = _session(client, device["credential"])
    command = client.post(
        f"/api/hermes/sessions/{session['id']}/commands",
        json={"idempotency_key": "k", "payload": {}},
        headers=auth(other_tenant_admin["token"]),
    ).json()

    r = client.post(
        f"/api/hermes/commands/{command['id']}/cancel", headers=auth(tenant_admin["token"])
    )
    assert r.status_code == 404

    leaked = client.get(
        f"/api/hermes/sessions/{session['id']}/commands", headers=auth(tenant_admin["token"])
    )
    assert leaked.status_code == 404
