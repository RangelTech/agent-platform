"""Sessões Hermes: upsert idempotente do dispositivo, consulta do usuário,
isolamento entre dispositivos e tenants (seção 6/7.4/7.7)."""

import uuid

import pytest

from tests.conftest import auth

pytestmark = pytest.mark.integration


def _pair(client, email, name="Notebook"):
    r = client.post(
        "/api/hermes/devices/pair",
        json={"email": email, "password": "senha-forte-123", "device_name": name},
    )
    assert r.status_code == 201, r.text
    return r.json()


def _device_auth(credential):
    return {"Authorization": f"Bearer {credential}"}


def test_upsert_creates_then_updates_the_same_session(client, tenant_admin):
    device = _pair(client, tenant_admin["email"])
    h = _device_auth(device["credential"])
    external_id = f"ext-{uuid.uuid4().hex[:8]}"

    first = client.post(
        "/api/hermes/sessions",
        json={"external_session_id": external_id, "title": "Revisar auth", "status": "idle"},
        headers=h,
    )
    assert first.status_code == 201, first.text
    session_id = first.json()["id"]

    second = client.post(
        "/api/hermes/sessions",
        json={"external_session_id": external_id, "title": "Revisar auth", "status": "running"},
        headers=h,
    )
    assert second.status_code == 201
    assert second.json()["id"] == session_id
    assert second.json()["status"] == "running"

    all_sessions = client.get("/api/hermes/sessions", headers=auth(tenant_admin["token"])).json()
    assert len([s for s in all_sessions if s["id"] == session_id]) == 1


def test_upsert_requires_device_credential(client):
    r = client.post(
        "/api/hermes/sessions", json={"external_session_id": "x", "status": "idle"}
    )
    assert r.status_code == 401


def test_two_chats_on_the_same_device_stay_independent(client, tenant_admin):
    device = _pair(client, tenant_admin["email"])
    h = _device_auth(device["credential"])

    a = client.post(
        "/api/hermes/sessions",
        json={"external_session_id": "chat-a", "title": "Chat A", "status": "running"},
        headers=h,
    ).json()
    b = client.post(
        "/api/hermes/sessions",
        json={"external_session_id": "chat-b", "title": "Chat B", "status": "idle"},
        headers=h,
    ).json()
    assert a["id"] != b["id"]

    listed = client.get("/api/hermes/sessions", headers=auth(tenant_admin["token"])).json()
    titles = {s["title"] for s in listed}
    assert {"Chat A", "Chat B"} <= titles


def test_sessions_are_tenant_isolated(client, tenant_admin, other_tenant_admin):
    mine = _pair(client, tenant_admin["email"], name="Meu")
    theirs = _pair(client, other_tenant_admin["email"], name="Deles")
    client.post(
        "/api/hermes/sessions",
        json={"external_session_id": "s", "title": "Minha sessão", "status": "idle"},
        headers=_device_auth(mine["credential"]),
    )
    client.post(
        "/api/hermes/sessions",
        json={"external_session_id": "s", "title": "Sessão deles", "status": "idle"},
        headers=_device_auth(theirs["credential"]),
    )

    visible = client.get("/api/hermes/sessions", headers=auth(tenant_admin["token"])).json()
    assert [s["title"] for s in visible] == ["Minha sessão"]


def test_cannot_read_a_session_from_another_tenant_by_id(client, tenant_admin, other_tenant_admin):
    theirs = _pair(client, other_tenant_admin["email"], name="Deles")
    session = client.post(
        "/api/hermes/sessions",
        json={"external_session_id": "s", "title": "x", "status": "idle"},
        headers=_device_auth(theirs["credential"]),
    ).json()
    r = client.get(f"/api/hermes/sessions/{session['id']}", headers=auth(tenant_admin["token"]))
    assert r.status_code == 404


def test_event_publish_is_idempotent_by_sequence(client, tenant_admin):
    device = _pair(client, tenant_admin["email"])
    h = _device_auth(device["credential"])
    session = client.post(
        "/api/hermes/sessions",
        json={"external_session_id": "s", "status": "running"},
        headers=h,
    ).json()

    for _ in range(2):  # simula reenvio após reconexão
        r = client.post(
            f"/api/hermes/sessions/{session['id']}/events",
            json={"sequence": 1, "type": "agent_message_chunk", "payload": {"text": "olá"}},
            headers=h,
        )
        assert r.status_code == 201

    events = client.get(
        f"/api/hermes/sessions/{session['id']}/events", headers=auth(tenant_admin["token"])
    ).json()
    assert len(events) == 1
    assert events[0]["sequence"] == 1


def test_events_are_returned_in_order_and_filterable_by_after_sequence(client, tenant_admin):
    device = _pair(client, tenant_admin["email"])
    h = _device_auth(device["credential"])
    session = client.post(
        "/api/hermes/sessions", json={"external_session_id": "s", "status": "running"}, headers=h
    ).json()
    for seq in range(3):
        client.post(
            f"/api/hermes/sessions/{session['id']}/events",
            json={"sequence": seq, "type": "tool_call", "payload": {"n": seq}},
            headers=h,
        )

    events = client.get(
        f"/api/hermes/sessions/{session['id']}/events?after_sequence=0",
        headers=auth(tenant_admin["token"]),
    ).json()
    assert [e["sequence"] for e in events] == [1, 2]


def test_device_cannot_publish_events_to_a_session_it_does_not_own(client, tenant_admin):
    mine = _pair(client, tenant_admin["email"], name="A")
    other_device = _pair(client, tenant_admin["email"], name="B")
    session = client.post(
        "/api/hermes/sessions",
        json={"external_session_id": "s", "status": "idle"},
        headers=_device_auth(mine["credential"]),
    ).json()
    r = client.post(
        f"/api/hermes/sessions/{session['id']}/events",
        json={"sequence": 0, "type": "x", "payload": {}},
        headers=_device_auth(other_device["credential"]),
    )
    assert r.status_code == 404


def test_archive_session(client, tenant_admin):
    device = _pair(client, tenant_admin["email"])
    session = client.post(
        "/api/hermes/sessions",
        json={"external_session_id": "s", "status": "ended"},
        headers=_device_auth(device["credential"]),
    ).json()
    r = client.post(
        f"/api/hermes/sessions/{session['id']}/archive", headers=auth(tenant_admin["token"])
    )
    assert r.status_code == 200
    assert r.json()["is_archived"] is True

    default_listing = client.get("/api/hermes/sessions", headers=auth(tenant_admin["token"])).json()
    assert session["id"] not in [s["id"] for s in default_listing]

    with_archived = client.get(
        "/api/hermes/sessions?include_archived=true", headers=auth(tenant_admin["token"])
    ).json()
    assert session["id"] in [s["id"] for s in with_archived]
