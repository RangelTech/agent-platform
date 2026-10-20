"""Artefato é do dono do chat: outro usuário do mesmo tenant recebe 404 (spec 10, U-2a)."""

import uuid

import pytest
from tests.conftest import auth

pytestmark = pytest.mark.integration


def _user(client, master_token, tenant):
    email = f"dono-{uuid.uuid4().hex[:8]}@acme.com"
    r = client.post(
        "/api/users",
        json={
            "email": email,
            "name": "U",
            "password": "senha-forte-123",
            "tenant_id": tenant["id"],
        },
        headers=auth(master_token),
    )
    assert r.status_code == 201, r.text
    token = client.post(
        "/api/auth/login", json={"email": email, "password": "senha-forte-123"}
    ).json()["token"]
    return r.json(), token


def test_outro_usuario_do_mesmo_tenant_nao_ve_artefato(client, master_token, tenant):
    from app.db import get_connection

    dono, tok_dono = _user(client, master_token, tenant)
    _, tok_outro = _user(client, master_token, tenant)
    with get_connection() as conn:
        chat = conn.execute(
            "INSERT INTO chats (tenant_id, user_id, title) VALUES (%s, %s, 't') RETURNING id",
            (tenant["id"], dono["id"]),
        ).fetchone()
        art = conn.execute(
            """INSERT INTO artifacts (tenant_id, chat_id, agent_name, kind, title, storage_path,
                                      content_type)
               VALUES (%s, %s, 'a', 'file', 'x.txt', 'inexistente', 'text/plain') RETURNING id""",
            (tenant["id"], str(chat["id"])),
        ).fetchone()
    aid = str(art["id"])
    assert client.get(f"/api/artifacts/{aid}", headers=auth(tok_dono)).status_code == 200
    assert client.get(f"/api/artifacts/{aid}", headers=auth(tok_outro)).status_code == 404
    assert client.get(f"/api/artifacts/{aid}/download", headers=auth(tok_outro)).status_code == 404
