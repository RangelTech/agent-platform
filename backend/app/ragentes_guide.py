"""Idempotent system template for the RAgentes onboarding assistant."""

from psycopg.types.json import Json

from app.db import get_connection

SYSTEM_KEY = "assistente-ragentes"
TEMPLATE_NAME = "Assistente RAgentes"
DESCRIPTION = "Guia da plataforma RAgentes e criação assistida de novos templates."
SUPERVISOR_PROMPT = """Você é o Assistente RAgentes da Rangel Tech. Fale em português claro.
Explique a plataforma e oriente o usuário dentro do tenant atual. Nunca revele
secrets, tokens, conversas privadas ou dados de outro tenant. Para criar um
template, primeiro produza uma prévia completa e peça confirmação explícita;
somente após uma confirmação inequívoca use a ferramenta de criação."""
AGENT_PROMPT = """Você é especialista em onboarding do RAgentes. Use apenas as tools
tenant_guide_ autorizadas para consultar o ambiente atual ou elaborar/criar
templates. Nunca execute administração geral, escrita em fontes de dados,
alterações de usuários/perfis, pagamentos ou integração RAtende."""


def ensure_for_tenant(conn, tenant_id) -> str:
    """Guarantee exactly one active system template and its first version."""
    template = conn.execute(
        """SELECT * FROM templates WHERE tenant_id=%s AND system_key=%s
           AND NOT is_deleted FOR UPDATE""",
        (tenant_id, SYSTEM_KEY),
    ).fetchone()
    if template is None:
        template = conn.execute(
            """INSERT INTO templates (tenant_id, name, description, system_key)
               VALUES (%s, %s, %s, %s) RETURNING *""",
            (tenant_id, TEMPLATE_NAME, DESCRIPTION, SYSTEM_KEY),
        ).fetchone()
    if template["active_version_id"]:
        return str(template["id"])
    version = conn.execute(
        """INSERT INTO template_versions
           (template_id, version_number, supervisor_prompt, max_steps, notes)
           VALUES (%s, 1, %s, 6, %s) RETURNING id""",
        (template["id"], SUPERVISOR_PROMPT, "Sistema Rangel Tech: onboarding v1"),
    ).fetchone()
    conn.execute(
        """INSERT INTO template_agents
           (version_id, name, description, prompt, sort_order, tools)
           VALUES (%s, %s, %s, %s, 0, %s)""",
        (
            version["id"],
            "guia_ragentes",
            "Explica o ambiente e cria templates após confirmação explícita.",
            AGENT_PROMPT,
            Json(
                [
                    "tenant_guide_get_platform_guide",
                    "tenant_guide_get_tenant_overview",
                    "tenant_guide_get_users_activity_summary",
                    "tenant_guide_get_ratende_status",
                    "tenant_guide_plan_template",
                    "tenant_guide_create_template_from_plan",
                ]
            ),
        ),
    )
    conn.execute(
        "UPDATE templates SET active_version_id=%s, updated_at=now() WHERE id=%s",
        (version["id"], template["id"]),
    )
    return str(template["id"])


def ensure_all_tenants() -> None:
    with get_connection() as conn:
        for row in conn.execute("SELECT id FROM tenants WHERE is_active").fetchall():
            ensure_for_tenant(conn, row["id"])
