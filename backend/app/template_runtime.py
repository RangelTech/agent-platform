"""Resolve a template's active version into the kernel's run payload.

Model resolution per agent: its ai_service (decrypted key) + optional model
override + reasoning effort. Without a template (or service) the platform
degrades to the tenant's default service, then env config, then the stub —
a missing setup never breaks the chat.
"""

from app.config import settings
from app.crypto import decrypt
from app.db import get_connection

_EFFORT_VALUES = ("low", "medium", "high")


def _model_from_service(row: dict | None, model_override: str | None, effort: str | None) -> dict:
    if row is None:
        if settings.ai_provider != "stub" and settings.ai_api_key:
            spec = {
                "provider": settings.ai_provider,
                "model": settings.ai_model,
                "api_key": settings.ai_api_key,
            }
        else:
            spec = {"provider": "stub", "model": "stub-1"}
    else:
        spec = {
            "provider": "openai" if row["provider"] == "openai-compatible" else row["provider"],
            "model": model_override or row["model"],
            "api_key": decrypt(row["api_key_encrypted"]),
            "api_base": row["api_base"],
        }
    if effort in _EFFORT_VALUES:
        # litellm normalizes reasoning_effort per provider; unsupported
        # combinations are dropped by litellm itself.
        spec["extra"] = {"reasoning_effort": effort}
    return spec


def _embedding_spec(conn, tenant_id) -> dict:
    """Embedding provider from the tenant's services (Gemini or OpenAI keys
    embed natively); stub otherwise (dev)."""
    rows = conn.execute(
        """SELECT provider, api_key_encrypted FROM ai_services
            WHERE tenant_id = %s AND is_active AND NOT is_deleted
                      AND api_key_encrypted IS NOT NULL
            ORDER BY created_at""",
        (tenant_id,),
    ).fetchall()
    for row in rows:
        if row["provider"] == "gemini":
            return {
                "provider": "gemini",
                "model": "gemini/text-embedding-004",
                "api_key": decrypt(row["api_key_encrypted"]),
            }
        if row["provider"] == "openai":
            return {
                "provider": "openai",
                "model": "text-embedding-3-small",
                "api_key": decrypt(row["api_key_encrypted"]),
            }
    return {"provider": "stub"}


def _default_service(conn, tenant_id) -> dict | None:
    return conn.execute(
        """SELECT * FROM ai_services
            WHERE tenant_id = %s AND is_active AND NOT is_deleted AND auth_type = 'api_key'
                  AND api_key_encrypted IS NOT NULL
            ORDER BY created_at LIMIT 1""",
        (tenant_id,),
    ).fetchone()


def build_run_payload(tenant_id, template_id: str | None) -> dict:
    """Kernel run payload (supervisor/agents/max_steps) for this conversation."""
    with get_connection() as conn:
        default_service = _default_service(conn, tenant_id)

        version = None
        if template_id:
            version = conn.execute(
                """SELECT v.* FROM templates t
                     JOIN template_versions v ON v.id = t.active_version_id
                    WHERE t.id = %s AND t.tenant_id = %s AND NOT t.is_deleted""",
                (template_id, tenant_id),
            ).fetchone()

        if version is None:
            return {
                "supervisor": {
                    "prompt": settings.ai_system_prompt,
                    "model": _model_from_service(default_service, None, None),
                },
                "agents": [],
                "max_steps": 4,
                "tenant_id": str(tenant_id),
                "secrets": {},
                "mcp_servers": [],
            }

        def service_by_id(service_id):
            if service_id is None:
                return default_service
            return (
                conn.execute(
                    "SELECT * FROM ai_services WHERE id = %s", (service_id,)
                ).fetchone()
                or default_service
            )

        agents = conn.execute(
            "SELECT * FROM template_agents WHERE version_id = %s ORDER BY sort_order",
            (version["id"],),
        ).fetchall()
        agent_file_rows = conn.execute(
            """SELECT af.agent_id, af.file_id
                 FROM template_agent_files af
                 JOIN template_agents a ON a.id = af.agent_id
                WHERE a.version_id = %s""",
            (version["id"],),
        ).fetchall()
        files_by_agent: dict[str, list[str]] = {}
        for r in agent_file_rows:
            files_by_agent.setdefault(str(r["agent_id"]), []).append(str(r["file_id"]))

        # Active named secrets, decrypted only here — they cross the internal
        # OIDC link and are substituted inside tools, never shown to the model.
        secret_rows = conn.execute(
            "SELECT name, value_encrypted FROM secrets WHERE tenant_id = %s AND is_active",
            (tenant_id,),
        ).fetchall()
        secrets = {r["name"]: decrypt(r["value_encrypted"]) for r in secret_rows}

        datasource_rows = conn.execute(
            """SELECT d.* FROM template_version_datasources l
                 JOIN datasources d ON d.id = l.datasource_id
                WHERE l.version_id = %s AND d.is_active""",
            (version["id"],),
        ).fetchall()
        datasources = [
            {
                "name": d["name"],
                "kind": d["kind"],
                "config": d["config"],
                "secret": decrypt(d["secret_encrypted"]) if d["secret_encrypted"] else None,
            }
            for d in datasource_rows
        ]

        server_rows = conn.execute(
            """SELECT name, url, auth_token_encrypted
                 FROM template_mcp_servers WHERE version_id = %s""",
            (version["id"],),
        ).fetchall()
        mcp_servers = [
            {
                "name": s["name"],
                "url": s["url"],
                "auth_token": decrypt(s["auth_token_encrypted"])
                if s["auth_token_encrypted"]
                else None,
            }
            for s in server_rows
        ]

        return {
            "supervisor": {
                "prompt": version["supervisor_prompt"],
                "model": _model_from_service(
                    service_by_id(version["supervisor_ai_service_id"]),
                    version["supervisor_model_override"],
                    version["supervisor_reasoning_effort"],
                ),
            },
            "agents": [
                {
                    "name": a["name"],
                    "description": a["description"],
                    "prompt": a["prompt"],
                    "model": _model_from_service(
                        service_by_id(a["ai_service_id"]),
                        a["model_override"],
                        a["reasoning_effort"],
                    ),
                    "tools": a["tools"] or [],
                    "file_ids": files_by_agent.get(str(a["id"]), []),
                }
                for a in agents
            ],
            "max_steps": version["max_steps"],
            "tenant_id": str(tenant_id),
            "secrets": secrets,
            "mcp_servers": mcp_servers,
            "datasources": datasources,
            "embedding": _embedding_spec(conn, tenant_id),
        }
