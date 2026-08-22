from app.config import settings
from app.db import get_connection
from app.ragentes_guide import SYSTEM_KEY, ensure_for_tenant


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
