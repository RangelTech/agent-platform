"""produto-15 §6 -- rotas de captura do RAtende Connector. Cobre validação
de provider, isolamento de tenant e que o cookie cifrado nunca volta na
serialização (mesma regra de senha/token nas outras rotas de credencial)."""

import pytest
from tests.conftest import auth

pytestmark = pytest.mark.integration


def _cookies():
    return [{"name": "sessionid", "value": "abc123", "domain": "tiktok.com", "path": "/"}]


def test_create_rejects_unknown_provider(client, tenant_admin):
    r = client.post(
        "/api/unofficial-connections",
        json={"provider": "twitter_web", "label": "X", "cookies": _cookies()},
        headers=auth(tenant_admin["token"]),
    )
    assert r.status_code == 400
    assert "provider" in r.json()["detail"]


def test_create_and_list_round_trip_never_returns_cookies(client, tenant_admin):
    r = client.post(
        "/api/unofficial-connections",
        json={
            "provider": "tiktok_web",
            "label": "TikTok principal",
            "external_label": "@empresa.tiktok",
            "cookies": _cookies(),
        },
        headers=auth(tenant_admin["token"]),
    )
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["provider"] == "tiktok_web"
    assert body["external_label"] == "@empresa.tiktok"
    assert "cookies" not in body
    assert "cookies_encrypted" not in body

    listed = client.get(
        "/api/unofficial-connections", headers=auth(tenant_admin["token"])
    ).json()
    assert len(listed) == 1
    assert "cookies" not in listed[0]


def test_connections_are_isolated_by_tenant(client, master_token, tenant, tenant_admin):
    client.post(
        "/api/unofficial-connections",
        json={"provider": "tiktok_web", "label": "TikTok tenant A", "cookies": _cookies()},
        headers=auth(tenant_admin["token"]),
    )

    outro = client.post(
        "/api/tenants",
        json={"name": "Outra Empresa", "tenant_key": "outra-empresa-e2e"},
        headers=auth(master_token),
    ).json()
    profiles = client.get("/api/user-profiles", headers=auth(master_token)).json()
    admin_profile = next(
        p for p in profiles if p["tenant_id"] == outro["id"] and p["name"] == "Administrador"
    )
    client.post(
        "/api/users",
        json={
            "email": "admin-outra@empresa.com",
            "name": "Admin Outra",
            "password": "senha-forte-123",
            "profile_id": admin_profile["id"],
            "tenant_id": outro["id"],
        },
        headers=auth(master_token),
    )
    outro_token = client.post(
        "/api/auth/login",
        json={"email": "admin-outra@empresa.com", "password": "senha-forte-123"},
    ).json()["token"]

    listed = client.get("/api/unofficial-connections", headers=auth(outro_token)).json()
    assert listed == []


def test_delete_is_scoped_to_own_tenant(client, master_token, tenant, tenant_admin):
    created = client.post(
        "/api/unofficial-connections",
        json={"provider": "instagram_web", "label": "IG", "cookies": _cookies()},
        headers=auth(tenant_admin["token"]),
    ).json()

    outro = client.post(
        "/api/tenants",
        json={"name": "Outra B", "tenant_key": "outra-b-e2e"},
        headers=auth(master_token),
    ).json()
    profiles = client.get("/api/user-profiles", headers=auth(master_token)).json()
    admin_profile = next(
        p for p in profiles if p["tenant_id"] == outro["id"] and p["name"] == "Administrador"
    )
    client.post(
        "/api/users",
        json={
            "email": "admin-outrab@empresa.com",
            "name": "Admin Outra B",
            "password": "senha-forte-123",
            "profile_id": admin_profile["id"],
            "tenant_id": outro["id"],
        },
        headers=auth(master_token),
    )
    outro_token = client.post(
        "/api/auth/login",
        json={"email": "admin-outrab@empresa.com", "password": "senha-forte-123"},
    ).json()["token"]

    r = client.delete(
        f"/api/unofficial-connections/{created['id']}", headers=auth(outro_token)
    )
    assert r.status_code == 404

    ainda_la = client.get(
        "/api/unofficial-connections", headers=auth(tenant_admin["token"])
    ).json()
    assert len(ainda_la) == 1
