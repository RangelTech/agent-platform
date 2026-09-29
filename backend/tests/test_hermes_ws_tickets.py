"""Ticket de handshake WSS do Hermes Relay (seção 7.2/8.1 da spec de
integração): a extensão troca a credencial de longa duração por um ticket
descartável em vez de mandar a credencial direto na query string do
WebSocket."""

from datetime import UTC, datetime, timedelta

import pytest
from tests.conftest import auth

pytestmark = pytest.mark.integration


def _pair(client, email, password, name="Notebook pessoal"):
    return client.post(
        "/api/hermes/devices/pair",
        json={"email": email, "password": password, "device_name": name},
    ).json()


def test_issues_a_short_lived_ticket_distinct_from_the_device_credential(client, tenant_admin):
    device = _pair(client, tenant_admin["email"], "senha-forte-123")
    credential = device["credential"]

    r = client.post(
        "/api/hermes/devices/ws-ticket", headers={"Authorization": f"Bearer {credential}"}
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert len(body["ticket"]) > 20
    assert body["ticket"] != credential

    expires_at = datetime.fromisoformat(body["expires_at"])
    now = datetime.now(UTC)
    assert now < expires_at <= now + timedelta(seconds=60)


def test_each_call_issues_a_different_ticket(client, tenant_admin):
    device = _pair(client, tenant_admin["email"], "senha-forte-123")
    credential = device["credential"]
    headers = {"Authorization": f"Bearer {credential}"}

    first = client.post("/api/hermes/devices/ws-ticket", headers=headers).json()
    second = client.post("/api/hermes/devices/ws-ticket", headers=headers).json()
    assert first["ticket"] != second["ticket"]


def test_ws_ticket_requires_a_valid_device_credential(client):
    r = client.post("/api/hermes/devices/ws-ticket")
    assert r.status_code == 401

    r = client.post(
        "/api/hermes/devices/ws-ticket", headers={"Authorization": "Bearer nao-existe"}
    )
    assert r.status_code == 401


def test_ws_ticket_is_refused_for_a_revoked_device(client, tenant_admin):
    device = _pair(client, tenant_admin["email"], "senha-forte-123")
    credential = device["credential"]
    client.post(
        f"/api/hermes/devices/{device['device']['id']}/revoke",
        headers=auth(tenant_admin["token"]),
    )

    r = client.post(
        "/api/hermes/devices/ws-ticket", headers={"Authorization": f"Bearer {credential}"}
    )
    assert r.status_code == 401
