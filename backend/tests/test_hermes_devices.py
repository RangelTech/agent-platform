"""Pareamento e gestão de dispositivos Hermes (seção 7.2/7.3/13 da spec de
integração)."""

import pytest
from tests.conftest import auth

pytestmark = pytest.mark.integration


def _pair(client, email, password, name="Notebook pessoal"):
    return client.post(
        "/api/hermes/devices/pair",
        json={"email": email, "password": password, "device_name": name, "platform": "win32"},
    )


def test_pair_with_valid_ria_credentials_issues_a_device_credential(client, tenant_admin):
    r = _pair(client, tenant_admin["email"], "senha-forte-123")
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["device"]["name"] == "Notebook pessoal"
    assert body["device"]["status"] == "connected"
    assert body["device"]["tenant_id"] == tenant_admin["user"]["tenant_id"]
    assert len(body["credential"]) > 20

    me = client.get(
        "/api/hermes/devices/me", headers={"Authorization": f"Bearer {body['credential']}"}
    )
    assert me.status_code == 200
    assert me.json()["id"] == body["device"]["id"]


def test_pair_rejects_wrong_password(client, tenant_admin):
    r = _pair(client, tenant_admin["email"], "senha-errada")
    assert r.status_code == 401


def test_pair_rejects_unknown_email(client):
    r = _pair(client, "ninguem@nada.com", "qualquer")
    assert r.status_code == 401


def test_master_cannot_pair_a_device(client, master_token):
    from app.config import settings

    r = _pair(client, settings.master_email, settings.master_password)
    assert r.status_code == 400


def test_device_credential_is_never_the_users_password_or_a_web_session(client, tenant_admin):
    r = _pair(client, tenant_admin["email"], "senha-forte-123")
    credential = r.json()["credential"]
    assert credential != "senha-forte-123"
    # Não pode ser usado como token de sessão web (rotas de usuário exigem `sessions`).
    denied = client.get(
        "/api/hermes/devices", headers={"Authorization": f"Bearer {credential}"}
    )
    assert denied.status_code == 401


def test_list_devices_is_tenant_scoped(client, tenant_admin, other_tenant_admin):
    _pair(client, tenant_admin["email"], "senha-forte-123", name="Meu notebook")
    _pair(client, other_tenant_admin["email"], "senha-forte-123", name="Notebook do outro")

    mine = client.get("/api/hermes/devices", headers=auth(tenant_admin["token"])).json()
    assert [d["name"] for d in mine] == ["Meu notebook"]

    theirs = client.get("/api/hermes/devices", headers=auth(other_tenant_admin["token"])).json()
    assert [d["name"] for d in theirs] == ["Notebook do outro"]


def test_rename_device(client, tenant_admin):
    device = _pair(client, tenant_admin["email"], "senha-forte-123").json()["device"]
    r = client.put(
        f"/api/hermes/devices/{device['id']}",
        json={"name": "Renomeado"},
        headers=auth(tenant_admin["token"]),
    )
    assert r.status_code == 200
    assert r.json()["name"] == "Renomeado"


def test_cannot_rename_a_device_from_another_tenant(client, tenant_admin, other_tenant_admin):
    device = _pair(client, other_tenant_admin["email"], "senha-forte-123").json()["device"]
    r = client.put(
        f"/api/hermes/devices/{device['id']}",
        json={"name": "Sequestrado"},
        headers=auth(tenant_admin["token"]),
    )
    assert r.status_code == 404


def test_revoke_device_ends_its_access_and_blocks_new_connections(client, tenant_admin):
    paired = _pair(client, tenant_admin["email"], "senha-forte-123").json()
    device_id, credential = paired["device"]["id"], paired["credential"]

    r = client.post(f"/api/hermes/devices/{device_id}/revoke", headers=auth(tenant_admin["token"]))
    assert r.status_code == 200

    me = client.get(
        "/api/hermes/devices/me", headers={"Authorization": f"Bearer {credential}"}
    )
    assert me.status_code == 401

    devices = client.get("/api/hermes/devices", headers=auth(tenant_admin["token"])).json()
    assert next(d for d in devices if d["id"] == device_id)["status"] == "revoked"
