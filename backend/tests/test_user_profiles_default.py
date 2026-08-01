"""Usuário criado sem perfil não pode nascer sem acesso nenhum."""

import uuid

import pytest
from tests.conftest import auth

pytestmark = pytest.mark.integration


def _profiles(client, master_token, tenant_id):
    rows = client.get(
        f"/api/user-profiles?tenant_id={tenant_id}", headers=auth(master_token)
    ).json()
    return {p["id"]: p["name"] for p in rows if p.get("tenant_id") == tenant_id}


def test_first_user_of_a_tenant_becomes_its_administrator(client, master_token, tenant):
    """O primeiro usuário é o dono da empresa: sem isso ele entrava sem menu."""
    created = client.post(
        "/api/users",
        json={
            "email": f"dono-{uuid.uuid4().hex[:6]}@acme.com",
            "name": "Dono",
            "password": "senha-forte-123",
            "tenant_id": tenant["id"],
        },
        headers=auth(master_token),
    ).json()
    names = _profiles(client, master_token, tenant["id"])
    assert names.get(created["profile_id"]) == "Administrador"


def test_following_users_come_in_as_members(client, master_token, tenant):
    base = uuid.uuid4().hex[:6]
    for suffix in ("dono", "membro"):
        created = client.post(
            "/api/users",
            json={
                "email": f"{suffix}-{base}@acme.com",
                "name": suffix,
                "password": "senha-forte-123",
                "tenant_id": tenant["id"],
            },
            headers=auth(master_token),
        ).json()
    names = _profiles(client, master_token, tenant["id"])
    assert names.get(created["profile_id"]) == "Usuário"


def test_an_explicit_profile_still_wins(client, master_token, tenant):
    names = _profiles(client, master_token, tenant["id"])
    member_id = next(pid for pid, name in names.items() if name == "Usuário")
    created = client.post(
        "/api/users",
        json={
            "email": f"escolhido-{uuid.uuid4().hex[:6]}@acme.com",
            "name": "Escolhido",
            "password": "senha-forte-123",
            "tenant_id": tenant["id"],
            "profile_id": member_id,
        },
        headers=auth(master_token),
    ).json()
    assert created["profile_id"] == member_id


def test_the_owner_sees_every_administrable_area(client, master_token, tenant):
    """O menu é montado a partir das permissões: se elas faltarem, ele some."""
    email = f"dono-{uuid.uuid4().hex[:6]}@acme.com"
    client.post(
        "/api/users",
        json={
            "email": email,
            "name": "Dono",
            "password": "senha-forte-123",
            "tenant_id": tenant["id"],
        },
        headers=auth(master_token),
    )
    token = client.post(
        "/api/auth/login", json={"email": email, "password": "senha-forte-123"}
    ).json()["token"]
    me = client.get("/api/auth/me", headers=auth(token)).json()
    esperado = {
        "templates",
        "ai_services",
        "datasources",
        "files",
        "users",
        "integrations",
        "usage",
        "payments",
        "mcp_store",
        "omnichannel",
    }
    assert esperado <= set((me.get("permissions") or {}).keys())
