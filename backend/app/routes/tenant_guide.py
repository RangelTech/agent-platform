"""Narrow internal API used only by the Assistente RAgentes kernel toolkit."""

import re

from fastapi import APIRouter, HTTPException, Request
from psycopg.types.json import Json

from app.config import settings
from app.db import get_connection
from app.permissions import has_permission
from app.guide_catalog import ragentes_guide

router = APIRouter(prefix="/api/internal/tenant-guide", tags=["tenant-guide"])
_AGENT = re.compile(r"^[a-z][a-z0-9_]{1,60}$")
_CONFIRMATION = re.compile(r"\b(confirmo|confirmar|pode criar|sim,? pode|aprovo)\b", re.I)
GUIDE = ragentes_guide()


def _internal(request: Request) -> None:
    expected = settings.kernel_internal_token
    if not expected or request.headers.get("authorization") != f"Bearer {expected}":
        raise HTTPException(401, "Não autorizado")


def _actor(conn, tenant_id: str, user_id: str) -> dict:
    row = conn.execute(
        """SELECT u.id, u.tenant_id, u.is_master, p.permissions
             FROM users u LEFT JOIN user_profiles p ON p.id=u.profile_id
            WHERE u.id=%s AND u.is_active""",
        (user_id,),
    ).fetchone()
    if row is None or str(row["tenant_id"]) != str(tenant_id) or row["is_master"]:
        raise HTTPException(403, "Identidade fora do tenant")
    return {
        "id": str(row["id"]),
        "tenant_id": str(row["tenant_id"]),
        "permissions": row["permissions"] or {},
    }


def _audit(conn, tenant_id, user_id, chat_id, action: str, plan=None, result=None, error=None):
    return conn.execute(
        """INSERT INTO tenant_guide_audit
           (tenant_id, user_id, chat_id, action, plan, result, error_message)
           VALUES (%s,%s,%s,%s,%s,%s,%s) RETURNING id""",
        (
            tenant_id,
            user_id,
            chat_id or None,
            action,
            Json(plan) if plan is not None else None,
            Json(result) if result is not None else None,
            error,
        ),
    ).fetchone()["id"]


def _plan(raw: dict) -> dict:
    name = str(raw.get("name") or "").strip()
    description = str(raw.get("description") or "").strip()
    supervisor_prompt = str(raw.get("supervisor_prompt") or "").strip()
    agents = raw.get("agents") or []
    if not name or not description or not supervisor_prompt or not isinstance(agents, list):
        raise HTTPException(400, "Prévia precisa de nome, descrição, supervisor e agentes")
    safe_agents = []
    for agent in agents[:8]:
        if not isinstance(agent, dict) or not _AGENT.fullmatch(str(agent.get("name") or "")):
            raise HTTPException(400, "Agente inválido")
        safe_agents.append(
            {
                "name": agent["name"],
                "description": str(agent.get("description") or "").strip(),
                "prompt": str(agent.get("prompt") or "").strip(),
                "tools": [tool for tool in (agent.get("tools") or []) if isinstance(tool, str)],
            }
        )
    if not safe_agents or any(not a["description"] or not a["prompt"] for a in safe_agents):
        raise HTTPException(400, "Cada agente precisa de descrição e prompt")
    return {
        "name": name[:200],
        "description": description[:2000],
        "supervisor_prompt": supervisor_prompt[:12000],
        "agents": safe_agents,
    }


@router.post("/{action}")
async def tenant_guide(action: str, request: Request):
    _internal(request)
    body = await request.json()
    tenant_id, user_id, chat_id = body.get("tenant_id"), body.get("user_id"), body.get("chat_id")
    if not all(isinstance(v, str) and v for v in (tenant_id, user_id, chat_id)):
        raise HTTPException(400, "Contexto de execução inválido")
    with get_connection() as conn:
        actor = _actor(conn, tenant_id, user_id)
        if action == "platform-guide":
            _audit(conn, tenant_id, user_id, chat_id, "platform_guide")
            return GUIDE
        if action == "overview":
            counts = {}
            for table in (
                "templates",
                "ai_services",
                "datasources",
                "files",
                "integrations",
                "custom_tools",
            ):
                where = "tenant_id=%s" + (
                    " AND NOT is_deleted" if table in {"templates", "ai_services"} else ""
                )
                counts[table] = conn.execute(
                    f"SELECT count(*) AS n FROM {table} WHERE {where}", (tenant_id,)
                ).fetchone()["n"]
            _audit(conn, tenant_id, user_id, chat_id, "overview", result=counts)
            return {"counts": counts, "links": GUIDE["links"]}
        if action == "users-activity":
            if not has_permission(actor, "users", "view"):
                _audit(conn, tenant_id, user_id, chat_id, "users_activity_denied")
                return {"limited": True, "message": "Seu perfil não permite consultar usuários."}
            total = conn.execute(
                "SELECT count(*) AS n FROM users WHERE tenant_id=%s AND is_active", (tenant_id,)
            ).fetchone()["n"]
            _audit(
                conn, tenant_id, user_id, chat_id, "users_activity", result={"active_users": total}
            )
            return {"active_users": total}
        if action == "ratende-status":
            account = conn.execute(
                "SELECT chatwoot_account_id FROM tenants WHERE id=%s", (tenant_id,)
            ).fetchone()
            result = {
                "configured": bool(settings.bridge_url and settings.bridge_admin_token),
                "chatwoot_account_id": account["chatwoot_account_id"] if account else None,
                "link": GUIDE["links"]["atendimento"],
            }
            _audit(conn, tenant_id, user_id, chat_id, "ratende_status", result=result)
            return result
        if action == "plan":
            plan = _plan(body.get("plan") or {})
            audit_id = _audit(conn, tenant_id, user_id, chat_id, "plan", plan=plan)
            return {
                "plan": plan,
                "confirmation_id": str(audit_id),
                "requires_explicit_confirmation": True,
            }
        if action == "create":
            confirmation_id = body.get("confirmation_id")
            if not isinstance(confirmation_id, str) or not has_permission(
                actor, "templates", "create"
            ):
                raise HTTPException(403, "Sem permissão para criar template")
            approval = conn.execute(
                """SELECT plan FROM tenant_guide_audit
                   WHERE id=%s AND tenant_id=%s AND user_id=%s AND chat_id=%s
                     AND action='plan'""",
                (confirmation_id, tenant_id, user_id, chat_id),
            ).fetchone()
            last_message = conn.execute(
                """SELECT content FROM chat_messages WHERE chat_id=%s AND role='user'
                   ORDER BY created_at DESC LIMIT 1""",
                (chat_id,),
            ).fetchone()
            if (
                approval is None
                or last_message is None
                or not _CONFIRMATION.search(last_message["content"])
            ):
                raise HTTPException(409, "Aguarde confirmação explícita do usuário")
            plan = approval["plan"]
            template = conn.execute(
                """INSERT INTO templates (tenant_id, name, description)
                   VALUES (%s,%s,%s) RETURNING id""",
                (tenant_id, plan["name"], plan["description"]),
            ).fetchone()
            version = conn.execute(
                """INSERT INTO template_versions
                   (template_id, version_number, supervisor_prompt, max_steps, created_by, notes)
                   VALUES (%s,1,%s,6,%s,%s) RETURNING id""",
                (
                    template["id"],
                    plan["supervisor_prompt"],
                    user_id,
                    "Criado pelo Assistente RAgentes após confirmação",
                ),
            ).fetchone()
            for order, agent in enumerate(plan["agents"]):
                conn.execute(
                    """INSERT INTO template_agents
                       (version_id,name,description,prompt,sort_order,tools)
                       VALUES (%s,%s,%s,%s,%s,%s)""",
                    (
                        version["id"],
                        agent["name"],
                        agent["description"],
                        agent["prompt"],
                        order,
                        Json(agent["tools"]),
                    ),
                )
            conn.execute(
                "UPDATE templates SET active_version_id=%s WHERE id=%s",
                (version["id"], template["id"]),
            )
            result = {
                "template_id": str(template["id"]),
                "version_id": str(version["id"]),
                "editor_url": f"/templates/{template['id']}",
            }
            _audit(conn, tenant_id, user_id, chat_id, "created", plan=plan, result=result)
            return result
    raise HTTPException(404, "Tool do guia não encontrada")
