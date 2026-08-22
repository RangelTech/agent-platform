"""Tenant isolation and permission enforcement — the security core of T02."""

import uuid

import pytest
from tests.conftest import auth

pytestmark = pytest.mark.integration


@pytest.fixture
def other_tenant_admin(client, master_token):
    """An admin in a *different* tenant, used to prove isolation."""
    key = f"globex-{uuid.uuid4().hex[:8]}"
    t = client.post(
        "/api/tenants",
        json={"name": "Globex", "tenant_key": key},
        headers=auth(master_token),
    ).json()
    profiles = client.get("/api/user-profiles", headers=auth(master_token)).json()
    profile = next(
        p for p in profiles if p["tenant_id"] == t["id"] and p["name"] == "Administrador"
    )
    email = f"admin-{uuid.uuid4().hex[:8]}@globex.com"
    client.post(
        "/api/users",
        json={
            "email": email,
            "name": "Admin Globex",
            "password": "senha-forte-123",
            "profile_id": profile["id"],
            "tenant_id": t["id"],
        },
        headers=auth(master_token),
    )
    token = client.post(
        "/api/auth/login", json={"email": email, "password": "senha-forte-123"}
    ).json()["token"]
    return {"tenant": t, "token": token, "profile": profile}


def test_new_tenant_gets_seeded_profiles(client, master_token, tenant):
    profiles = client.get("/api/user-profiles", headers=auth(master_token)).json()
    names = {p["name"] for p in profiles if p["tenant_id"] == tenant["id"]}
    assert names == {"Administrador", "Usuário"}


def test_tenant_key_must_be_unique(client, master_token, tenant):
    r = client.post(
        "/api/tenants",
        json={"name": "Outra", "tenant_key": tenant["tenant_key"]},
        headers=auth(master_token),
    )
    assert r.status_code == 409


def test_only_master_manages_tenants(client, tenant_admin):
    assert client.get("/api/tenants", headers=auth(tenant_admin["token"])).status_code == 403
    r = client.post(
        "/api/tenants",
        json={"name": "Pirata", "tenant_key": "pirata"},
        headers=auth(tenant_admin["token"]),
    )
    assert r.status_code == 403


def test_master_tenant_list_paginates_and_filters(client, master_token, tenant):
    """The company table stays bounded even with a large tenant base."""
    r = client.get(
        "/api/tenants?q=acme&page=1&page_size=1", headers=auth(master_token)
    )
    assert r.status_code == 200
    body = r.json()
    assert body["total"] >= 1
    assert body["active_total"] >= 1
    assert body["page"] == 1
    assert body["page_size"] == 1
    assert len(body["items"]) == 1
    assert body["items"][0]["tenant_key"] == tenant["tenant_key"]


def test_admin_sees_only_users_of_their_own_tenant(
    client, tenant_admin, other_tenant_admin
):
    visible = client.get("/api/users", headers=auth(tenant_admin["token"])).json()
    tenant_ids = {u["tenant_id"] for u in visible}
    assert tenant_ids == {tenant_admin["user"]["tenant_id"]}


def test_admin_sees_only_profiles_of_their_own_tenant(
    client, tenant_admin, other_tenant_admin
):
    visible = client.get("/api/user-profiles", headers=auth(tenant_admin["token"])).json()
    assert {p["tenant_id"] for p in visible} == {tenant_admin["user"]["tenant_id"]}


def test_admin_cannot_create_a_user_in_another_tenant(
    client, tenant_admin, other_tenant_admin
):
    r = client.post(
        "/api/users",
        json={
            "email": f"invasor-{uuid.uuid4().hex[:6]}@x.com",
            "name": "Invasor",
            "password": "senha-forte-123",
            "tenant_id": other_tenant_admin["tenant"]["id"],
        },
        headers=auth(tenant_admin["token"]),
    )
    assert r.status_code == 403


def test_admin_cannot_edit_a_user_of_another_tenant(
    client, master_token, tenant_admin, other_tenant_admin
):
    victims = client.get("/api/users", headers=auth(master_token)).json()
    victim = next(
        u for u in victims if u["tenant_id"] == other_tenant_admin["tenant"]["id"]
    )
    r = client.put(
        f"/api/users/{victim['id']}",
        json={"name": "Sequestrado"},
        headers=auth(tenant_admin["token"]),
    )
    assert r.status_code == 404


def test_user_cannot_be_given_a_profile_from_another_tenant(
    client, tenant_admin, other_tenant_admin
):
    r = client.post(
        "/api/users",
        json={
            "email": f"escalada-{uuid.uuid4().hex[:6]}@acme.com",
            "name": "Escalada",
            "password": "senha-forte-123",
            "profile_id": other_tenant_admin["profile"]["id"],
        },
        headers=auth(tenant_admin["token"]),
    )
    assert r.status_code == 403


def test_member_profile_cannot_manage_users(client, master_token, tenant, tenant_admin):
    profiles = client.get("/api/user-profiles", headers=auth(tenant_admin["token"])).json()
    member_profile = next(p for p in profiles if p["name"] == "Usuário")
    email = f"membro-{uuid.uuid4().hex[:8]}@acme.com"
    client.post(
        "/api/users",
        json={
            "email": email,
            "name": "Membro",
            "password": "senha-forte-123",
            "profile_id": member_profile["id"],
        },
        headers=auth(tenant_admin["token"]),
    )
    token = client.post(
        "/api/auth/login", json={"email": email, "password": "senha-forte-123"}
    ).json()["token"]

    assert client.get("/api/users", headers=auth(token)).status_code == 403
    assert client.get("/api/auth/me", headers=auth(token)).status_code == 200


def test_tenant_admin_cannot_edit_the_master_user(client, master_token, tenant_admin):
    users = client.get("/api/users", headers=auth(master_token)).json()
    master = next(u for u in users if u["is_master"])
    r = client.put(
        f"/api/users/{master['id']}",
        json={"password": "tomei-a-plataforma"},
        headers=auth(tenant_admin["token"]),
    )
    assert r.status_code in (403, 404)


def test_profile_rejects_unknown_permissions(client, tenant_admin):
    r = client.post(
        "/api/user-profiles",
        json={"name": "Estranho", "permissions": {"nao_existe": ["view"]}},
        headers=auth(tenant_admin["token"]),
    )
    assert r.status_code == 400


def test_duplicate_email_is_rejected(client, tenant_admin):
    payload = {
        "email": f"dup-{uuid.uuid4().hex[:8]}@acme.com",
        "name": "Duplicado",
        "password": "senha-forte-123",
    }
    headers = auth(tenant_admin["token"])
    assert client.post("/api/users", json=payload, headers=headers).status_code == 201
    assert client.post("/api/users", json=payload, headers=headers).status_code == 409
