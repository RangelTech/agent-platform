"""Template management: versioned supervisor+specialists definitions.

A version is an immutable snapshot; POST /{id}/deploy points the template's
active_version_id at it (rollback = deploy an older version). The chat
resolves the active version into the kernel's run payload.
"""

import re

from fastapi import APIRouter, Depends, HTTPException
from psycopg.errors import UniqueViolation
from psycopg.types.json import Json
from pydantic import BaseModel, Field

from app.auth import require
from app.db import get_connection
from app.tenancy import resolve_target_tenant

router = APIRouter(prefix="/api/templates", tags=["templates"])

_AGENT_NAME = re.compile(r"^[a-z][a-z0-9_]{1,60}$")


class AgentIn(BaseModel):
    name: str
    description: str = Field(min_length=1, max_length=2000)
    prompt: str = Field(min_length=1)
    ai_service_id: str | None = None
    model_override: str | None = None
    reasoning_effort: str | None = None
    tools: list[str] = Field(default_factory=list)


class McpServerIn(BaseModel):
    name: str = Field(min_length=1, max_length=60, pattern=r"^[a-z0-9_]+$")
    url: str = Field(min_length=8, max_length=2000)
    auth_token: str | None = None


class VersionIn(BaseModel):
    supervisor_prompt: str = Field(min_length=1)
    supervisor_ai_service_id: str | None = None
    supervisor_model_override: str | None = None
    supervisor_reasoning_effort: str | None = None
    max_steps: int = Field(default=6, ge=1, le=20)
    agents: list[AgentIn] = Field(default_factory=list)
    mcp_servers: list[McpServerIn] = Field(default_factory=list)
    datasource_ids: list[str] = Field(default_factory=list)
    notes: str = ""


class TemplateIn(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    description: str = Field(default="", max_length=2000)
    tenant_id: str | None = None  # master only


def _template_scoped(conn, template_id: str, user: dict) -> dict:
    row = conn.execute(
        "SELECT * FROM templates WHERE id = %s AND NOT is_deleted", (template_id,)
    ).fetchone()
    if row is None or (
        not user["is_master"] and str(row["tenant_id"]) != str(user["tenant_id"])
    ):
        raise HTTPException(status_code=404, detail="Template não encontrado")
    return row


def _serialize_template(row: dict, active_version_number: int | None = None) -> dict:
    return {
        "id": str(row["id"]),
        "tenant_id": str(row["tenant_id"]),
        "name": row["name"],
        "description": row["description"],
        "active_version_id": str(row["active_version_id"]) if row["active_version_id"] else None,
        "active_version_number": active_version_number,
    }


@router.get("")
def list_templates(user: dict = Depends(require("templates", "view"))):
    with get_connection() as conn:
        scope = "" if user["is_master"] else " AND t.tenant_id = %s"
        params = () if user["is_master"] else (user["tenant_id"],)
        rows = conn.execute(
            f"""SELECT t.*, v.version_number AS active_version_number
                  FROM templates t
                  LEFT JOIN template_versions v ON v.id = t.active_version_id
                 WHERE NOT t.is_deleted{scope}
                 ORDER BY t.name""",
            params,
        ).fetchall()
    return [_serialize_template(r, r["active_version_number"]) for r in rows]


@router.post("", status_code=201)
def create_template(payload: TemplateIn, user: dict = Depends(require("templates", "create"))):
    tenant_id = resolve_target_tenant(user, payload.tenant_id)
    with get_connection() as conn:
        try:
            row = conn.execute(
                """INSERT INTO templates (tenant_id, name, description)
                   VALUES (%s, %s, %s) RETURNING *""",
                (tenant_id, payload.name, payload.description),
            ).fetchone()
        except UniqueViolation as exc:
            raise HTTPException(
                status_code=409, detail="Já existe um template com esse nome"
            ) from exc
    return _serialize_template(row)


def _validate_version(conn, payload: VersionIn, tenant_id) -> None:
    seen = set()
    for agent in payload.agents:
        if not _AGENT_NAME.match(agent.name):
            raise HTTPException(
                status_code=400,
                detail=f"Nome de agente inválido: {agent.name} (use snake_case)",
            )
        if agent.name in seen:
            raise HTTPException(status_code=400, detail=f"Agente duplicado: {agent.name}")
        seen.add(agent.name)
    # Every referenced AI service must belong to the same tenant.
    for service_id in filter(
        None,
        [payload.supervisor_ai_service_id] + [a.ai_service_id for a in payload.agents],
    ):
        row = conn.execute(
            "SELECT tenant_id FROM ai_services WHERE id = %s", (service_id,)
        ).fetchone()
        if row is None or str(row["tenant_id"]) != str(tenant_id):
            raise HTTPException(
                status_code=400, detail="Serviço de IA não pertence ao tenant"
            )
    for datasource_id in payload.datasource_ids:
        row = conn.execute(
            "SELECT tenant_id FROM datasources WHERE id = %s", (datasource_id,)
        ).fetchone()
        if row is None or str(row["tenant_id"]) != str(tenant_id):
            raise HTTPException(
                status_code=400, detail="Fonte de dados não pertence ao tenant"
            )


@router.post("/{template_id}/versions", status_code=201)
def create_version(
    template_id: str,
    payload: VersionIn,
    user: dict = Depends(require("templates", "edit")),
):
    with get_connection() as conn:
        template = _template_scoped(conn, template_id, user)
        _validate_version(conn, payload, template["tenant_id"])

        number = conn.execute(
            """SELECT COALESCE(MAX(version_number), 0) + 1 AS n
                 FROM template_versions WHERE template_id = %s""",
            (template_id,),
        ).fetchone()["n"]
        version = conn.execute(
            """INSERT INTO template_versions
                   (template_id, version_number, supervisor_prompt,
                    supervisor_ai_service_id, supervisor_model_override,
                    supervisor_reasoning_effort, max_steps, created_by, notes)
               VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s) RETURNING *""",
            (
                template_id,
                number,
                payload.supervisor_prompt,
                payload.supervisor_ai_service_id,
                payload.supervisor_model_override,
                payload.supervisor_reasoning_effort,
                payload.max_steps,
                user["id"],
                payload.notes,
            ),
        ).fetchone()
        for order, agent in enumerate(payload.agents):
            conn.execute(
                """INSERT INTO template_agents
                       (version_id, name, description, prompt, ai_service_id,
                        model_override, reasoning_effort, sort_order, tools)
                   VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)""",
                (
                    version["id"],
                    agent.name,
                    agent.description,
                    agent.prompt,
                    agent.ai_service_id,
                    agent.model_override,
                    agent.reasoning_effort,
                    order,
                    Json(agent.tools),
                ),
            )
        for datasource_id in payload.datasource_ids:
            conn.execute(
                """INSERT INTO template_version_datasources (version_id, datasource_id)
                   VALUES (%s, %s)""",
                (version["id"], datasource_id),
            )
        from app.crypto import encrypt

        for server in payload.mcp_servers:
            conn.execute(
                """INSERT INTO template_mcp_servers
                       (version_id, name, url, auth_token_encrypted)
                   VALUES (%s, %s, %s, %s)""",
                (
                    version["id"],
                    server.name,
                    server.url,
                    encrypt(server.auth_token) if server.auth_token else None,
                ),
            )
    return {"id": str(version["id"]), "version_number": number}


@router.get("/{template_id}/versions")
def list_versions(template_id: str, user: dict = Depends(require("templates", "view"))):
    with get_connection() as conn:
        _template_scoped(conn, template_id, user)
        rows = conn.execute(
            """SELECT id, version_number, notes, created_at
                 FROM template_versions
                WHERE template_id = %s ORDER BY version_number DESC""",
            (template_id,),
        ).fetchall()
    return [
        {
            "id": str(r["id"]),
            "version_number": r["version_number"],
            "notes": r["notes"],
            "created_at": r["created_at"].isoformat(),
        }
        for r in rows
    ]


@router.get("/{template_id}/versions/{version_id}")
def get_version(
    template_id: str, version_id: str, user: dict = Depends(require("templates", "view"))
):
    with get_connection() as conn:
        _template_scoped(conn, template_id, user)
        version = conn.execute(
            "SELECT * FROM template_versions WHERE id = %s AND template_id = %s",
            (version_id, template_id),
        ).fetchone()
        if version is None:
            raise HTTPException(status_code=404, detail="Versão não encontrada")
        agents = conn.execute(
            "SELECT * FROM template_agents WHERE version_id = %s ORDER BY sort_order",
            (version_id,),
        ).fetchall()
        mcp_servers = conn.execute(
            "SELECT name, url FROM template_mcp_servers WHERE version_id = %s",
            (version_id,),
        ).fetchall()
        datasource_ids = conn.execute(
            "SELECT datasource_id FROM template_version_datasources WHERE version_id = %s",
            (version_id,),
        ).fetchall()
    return {
        "id": str(version["id"]),
        "version_number": version["version_number"],
        "supervisor_prompt": version["supervisor_prompt"],
        "supervisor_ai_service_id": (
            str(version["supervisor_ai_service_id"])
            if version["supervisor_ai_service_id"]
            else None
        ),
        "supervisor_model_override": version["supervisor_model_override"],
        "supervisor_reasoning_effort": version["supervisor_reasoning_effort"],
        "max_steps": version["max_steps"],
        "notes": version["notes"],
        "agents": [
            {
                "name": a["name"],
                "description": a["description"],
                "prompt": a["prompt"],
                "ai_service_id": str(a["ai_service_id"]) if a["ai_service_id"] else None,
                "model_override": a["model_override"],
                "reasoning_effort": a["reasoning_effort"],
                "tools": a["tools"] or [],
            }
            for a in agents
        ],
        # Tokens are write-only; the editor resubmits them when changing servers.
        "mcp_servers": [{"name": s["name"], "url": s["url"]} for s in mcp_servers],
        "datasource_ids": [str(d["datasource_id"]) for d in datasource_ids],
    }


@router.post("/{template_id}/deploy")
def deploy_version(
    template_id: str,
    payload: dict,
    user: dict = Depends(require("templates", "edit")),
):
    version_id = payload.get("version_id")
    if not version_id:
        raise HTTPException(status_code=400, detail="Informe version_id")
    with get_connection() as conn:
        _template_scoped(conn, template_id, user)
        version = conn.execute(
            "SELECT id FROM template_versions WHERE id = %s AND template_id = %s",
            (version_id, template_id),
        ).fetchone()
        if version is None:
            raise HTTPException(status_code=404, detail="Versão não encontrada")
        conn.execute(
            "UPDATE templates SET active_version_id = %s, updated_at = now() WHERE id = %s",
            (version_id, template_id),
        )
    return {"status": "ok", "active_version_id": version_id}
