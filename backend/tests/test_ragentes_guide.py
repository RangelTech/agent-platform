import pytest
from app.config import settings
from app.db import get_connection
from app.ragentes_guide import SYSTEM_KEY, ensure_for_tenant

pytestmark = pytest.mark.integration


def auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def test_tenant_gets_one_idempotent_ragentes_guide(client, master_token, tenant):
    with get_connection() as conn:
        first = ensure_for_tenant(conn, tenant["id"])
        second = ensure_for_tenant(conn, tenant["id"])
        count = conn.execute(
            """SELECT count(*) AS n FROM templates
                 WHERE tenant_id=%s AND system_key=%s AND NOT is_deleted""",
            (tenant["id"], SYSTEM_KEY),
        ).fetchone()["n"]
        template = conn.execute(
            "SELECT active_version_id FROM templates WHERE id=%s", (first,)
        ).fetchone()
    assert first == second
    assert count == 1
    assert template["active_version_id"] is not None


def test_system_guide_is_visible_but_cannot_be_changed(client, tenant_admin):
    templates = client.get("/api/templates", headers=auth(tenant_admin["token"])).json()
    guide = next(item for item in templates if item["name"] == "Assistente RAgentes")
    response = client.delete(f"/api/templates/{guide['id']}", headers=auth(tenant_admin["token"]))
    assert response.status_code == 403


def test_open_guide_creates_only_the_callers_tenant_chat(client, tenant_admin):
    response = client.post("/api/chats/ragentes-guide", headers=auth(tenant_admin["token"]))
    assert response.status_code == 200, response.text
    opened = response.json()
    assert opened["title"] == "Assistente RAgentes"
    assert opened["template_id"]
    assert opened["ai_ready"] is False
    second = client.post("/api/chats/ragentes-guide", headers=auth(tenant_admin["token"]))
    assert second.status_code == 200
    assert second.json()["id"] == opened["id"]


def test_guide_plan_is_tenant_scoped_and_audited(client, tenant_admin):
    chat = client.post("/api/chats/ragentes-guide", headers=auth(tenant_admin["token"])).json()
    old_token = settings.kernel_internal_token
    settings.kernel_internal_token = "test-guide-token"
    try:
        response = client.post(
            "/api/internal/tenant-guide/plan",
            headers={"Authorization": "Bearer test-guide-token"},
            json={
                "tenant_id": tenant_admin["user"]["tenant_id"],
                "user_id": tenant_admin["user"]["id"],
                "chat_id": chat["id"],
                "plan": {
                    "name": "Agente de teste",
                    "description": "Organiza solicitações.",
                    "supervisor_prompt": "Delegue ao especialista.",
                    "agents": [
                        {"name": "organizador", "description": "Organiza", "prompt": "Organize."}
                    ],
                },
            },
        )
    finally:
        settings.kernel_internal_token = old_token
    assert response.status_code == 200, response.text
    assert response.json()["requires_explicit_confirmation"] is True


def test_platform_guide_is_a_versioned_structured_package(client, tenant_admin):
    chat = client.post("/api/chats/ragentes-guide", headers=auth(tenant_admin["token"])).json()
    old_token = settings.kernel_internal_token
    settings.kernel_internal_token = "test-guide-token"
    try:
        response = client.post(
            "/api/internal/tenant-guide/platform-guide",
            headers={"Authorization": "Bearer test-guide-token"},
            json={
                "tenant_id": tenant_admin["user"]["tenant_id"],
                "user_id": tenant_admin["user"]["id"],
                "chat_id": chat["id"],
            },
        )
    finally:
        settings.kernel_internal_token = old_token
    assert response.status_code == 200, response.text
    guide = response.json()
    assert guide["version"] == "1.0.0"
    assert guide["compatibility"]["rollback_supported"] is True
    assert any(section["id"] == "custom-tools" for section in guide["sections"])


def test_member_gets_a_limited_users_summary_without_cross_tenant_data(
    client, tenant, master_token
):
    profiles = client.get("/api/user-profiles", headers=auth(master_token)).json()
    member_profile = next(
        item
        for item in profiles
        if item["tenant_id"] == tenant["id"] and item["name"] == "Usuário"
    )
    created = client.post(
        "/api/users",
        headers=auth(master_token),
        json={
            "email": "member-guide@acme.com",
            "name": "Membro do Guia",
            "password": "senha-forte-123",
            "profile_id": member_profile["id"],
            "tenant_id": tenant["id"],
        },
    )
    assert created.status_code == 201, created.text
    login = client.post(
        "/api/auth/login",
        json={"email": "member-guide@acme.com", "password": "senha-forte-123"},
    )
    assert login.status_code == 200, login.text
    member = created.json()
    chat = client.post("/api/chats/ragentes-guide", headers=auth(login.json()["token"])).json()

    previous = settings.kernel_internal_token
    settings.kernel_internal_token = "test-guide-token"
    try:
        response = client.post(
            "/api/internal/tenant-guide/users-activity",
            headers={"Authorization": "Bearer test-guide-token"},
            json={"tenant_id": tenant["id"], "user_id": member["id"], "chat_id": chat["id"]},
        )
    finally:
        settings.kernel_internal_token = previous
    assert response.status_code == 200, response.text
    assert response.json() == {
        "limited": True,
        "message": "Seu perfil não permite consultar usuários.",
    }


def test_confirmed_guide_plan_creates_a_deployed_template(client, tenant_admin):
    chat = client.post("/api/chats/ragentes-guide", headers=auth(tenant_admin["token"])).json()
    body = {
        "tenant_id": tenant_admin["user"]["tenant_id"],
        "user_id": tenant_admin["user"]["id"],
        "chat_id": chat["id"],
        "plan": {
            "name": "Agente de pedidos",
            "description": "Organiza pedidos de clientes.",
            "supervisor_prompt": "Delegue pedidos ao especialista.",
            "supervisor_model_override": "modelo-planejado",
            "max_steps": 7,
            "write_tables": ["pedidos"],
            "require_write_confirmation": False,
            "agents": [
                {
                    "name": "pedidos",
                    "description": "Organiza pedidos",
                    "prompt": "Organize.",
                    "model_override": "modelo-especialista",
                    "tools": ["consultar_pedidos"],
                }
            ],
        },
    }
    old_token = settings.kernel_internal_token
    settings.kernel_internal_token = "test-guide-token"
    try:
        planned = client.post(
            "/api/internal/tenant-guide/plan",
            headers={"Authorization": "Bearer test-guide-token"},
            json=body,
        )
        assert planned.status_code == 200, planned.text
        with get_connection() as conn:
            conn.execute(
                "INSERT INTO chat_messages (chat_id, role, content) VALUES (%s,'user','confirmo')",
                (chat["id"],),
            )
        created = client.post(
            "/api/internal/tenant-guide/create",
            headers={"Authorization": "Bearer test-guide-token"},
            json={**body, "confirmation_id": planned.json()["confirmation_id"]},
        )
    finally:
        settings.kernel_internal_token = old_token
    assert created.status_code == 200, created.text
    with get_connection() as conn:
        template = conn.execute(
            "SELECT active_version_id FROM templates WHERE id=%s", (created.json()["template_id"],)
        ).fetchone()
        version = conn.execute(
            """SELECT max_steps, supervisor_model_override, write_tables,
                      require_write_confirmation
                 FROM template_versions WHERE id=%s""",
            (created.json()["version_id"],),
        ).fetchone()
        agent = conn.execute(
            "SELECT model_override, tools FROM template_agents WHERE version_id=%s",
            (created.json()["version_id"],),
        ).fetchone()
    assert str(template["active_version_id"]) == created.json()["version_id"]
    assert version["max_steps"] == 7
    assert version["supervisor_model_override"] == "modelo-planejado"
    assert version["write_tables"] == ["pedidos"]
    assert version["require_write_confirmation"] is False
    assert agent["model_override"] == "modelo-especialista"
    assert agent["tools"] == ["consultar_pedidos"]


def test_guide_plan_requires_confirmation_after_the_preview(client, tenant_admin):
    """An old 'confirmo' must never authorize a newly produced plan."""
    chat = client.post("/api/chats/ragentes-guide", headers=auth(tenant_admin["token"])).json()
    body = {
        "tenant_id": tenant_admin["user"]["tenant_id"],
        "user_id": tenant_admin["user"]["id"],
        "chat_id": chat["id"],
        "plan": {
            "name": "Agente sem confirmação nova",
            "description": "Não pode ser criado com uma confirmação antiga.",
            "supervisor_prompt": "Delegue ao especialista.",
            "agents": [{"name": "seguro", "description": "Protege", "prompt": "Proteja."}],
        },
    }
    old_token = settings.kernel_internal_token
    settings.kernel_internal_token = "test-guide-token"
    try:
        with get_connection() as conn:
            conn.execute(
                "INSERT INTO chat_messages (chat_id, role, content) VALUES (%s,'user','confirmo')",
                (chat["id"],),
            )
        planned = client.post(
            "/api/internal/tenant-guide/plan",
            headers={"Authorization": "Bearer test-guide-token"},
            json=body,
        )
        assert planned.status_code == 200, planned.text
        denied = client.post(
            "/api/internal/tenant-guide/create",
            headers={"Authorization": "Bearer test-guide-token"},
            json={**body, "confirmation_id": planned.json()["confirmation_id"]},
        )
    finally:
        settings.kernel_internal_token = old_token
    assert denied.status_code == 409
    with get_connection() as conn:
        audit = conn.execute(
            """SELECT action, error_message FROM tenant_guide_audit
                 WHERE tenant_id=%s AND user_id=%s ORDER BY created_at DESC LIMIT 1""",
            (tenant_admin["user"]["tenant_id"], tenant_admin["user"]["id"]),
        ).fetchone()
    assert audit["action"] == "create_denied"
    assert "confirmação explícita" in audit["error_message"]


def test_guide_rejects_a_forged_cross_tenant_chat_and_audits_the_attempt(
    client, tenant_admin, tenant, master_token
):
    """The internal token cannot turn a foreign guide chat into our context."""
    other_tenant = client.post(
        "/api/tenants",
        json={"name": "Outra empresa", "tenant_key": "outra-empresa"},
        headers=auth(master_token),
    ).json()
    profiles = client.get("/api/user-profiles", headers=auth(master_token)).json()
    profile = next(
        item
        for item in profiles
        if item["tenant_id"] == other_tenant["id"] and item["name"] == "Administrador"
    )
    created_user = client.post(
        "/api/users",
        json={
                "email": "outra-admin@empresa.com",
            "name": "Outra Admin",
            "password": "senha-forte-123",
            "profile_id": profile["id"],
            "tenant_id": other_tenant["id"],
        },
        headers=auth(master_token),
    )
    assert created_user.status_code == 201, created_user.text
    login = client.post(
        "/api/auth/login",
        json={"email": "outra-admin@empresa.com", "password": "senha-forte-123"},
    )
    assert login.status_code == 200, login.text
    other_token = login.json()["token"]
    foreign_chat = client.post("/api/chats/ragentes-guide", headers=auth(other_token)).json()
    own_chat = client.post("/api/chats/ragentes-guide", headers=auth(tenant_admin["token"])).json()

    old_token = settings.kernel_internal_token
    settings.kernel_internal_token = "test-guide-token"
    try:
        denied = client.post(
            "/api/internal/tenant-guide/overview",
            headers={"Authorization": "Bearer test-guide-token"},
            json={
                "tenant_id": tenant["id"],
                "user_id": tenant_admin["user"]["id"],
                "chat_id": foreign_chat["id"],
            },
        )
    finally:
        settings.kernel_internal_token = old_token
    assert denied.status_code == 403
    with get_connection() as conn:
        denied_audit = conn.execute(
            """SELECT action, error_message FROM tenant_guide_audit
                 WHERE tenant_id=%s AND user_id=%s ORDER BY created_at DESC LIMIT 1""",
            (tenant["id"], tenant_admin["user"]["id"]),
        ).fetchone()
    assert denied_audit["action"] == "context_denied"
    assert "fora do escopo" in denied_audit["error_message"]
    assert own_chat["id"] != foreign_chat["id"]
