"""Narrow internal API used only by the Assistente RAgentes kernel toolkit."""

import re

from fastapi import APIRouter, HTTPException, Request
from psycopg.types.json import Json
from pydantic import ValidationError

from app.config import settings
from app.db import get_connection
from app.guide_catalog import ragentes_guide
from app.permissions import has_permission
from app.routes.templates import AgentIn, VersionIn, create_guided_template

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


def _guide_chat(conn, *, tenant_id: str, user_id: str, chat_id: str) -> None:
    """Bind every toolkit call to the caller's own system-guide chat.

    The kernel receives its execution context from the backend, but this
    additional database check makes a forged/replayed ``chat_id`` harmless as
    well.  In particular, a guide call must never use another tenant's chat as
    an audit trail or confirmation source.
    """
    chat = conn.execute(
        """SELECT c.id FROM chats c
             JOIN templates t ON t.id=c.template_id
            WHERE c.id=%s AND c.tenant_id=%s AND c.user_id=%s
              AND NOT c.is_hidden AND t.system_key='assistente-ragentes'
              AND NOT t.is_deleted""",
        (chat_id, tenant_id, user_id),
    ).fetchone()
    if chat is None:
        _audit(
            conn,
            tenant_id,
            user_id,
            None,
            "context_denied",
            error="Chat do guia fora do escopo do solicitante",
        )
        # The caller receives a 403, which rolls the surrounding request
        # transaction back. Persist the security audit before raising.
        conn.commit()
        raise HTTPException(403, "Chat do guia fora do escopo do solicitante")


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


def _deny_create(
    conn, tenant_id: str, user_id: str, chat_id: str, status: int, message: str
) -> None:
    """Persist a denied creation attempt before returning its HTTP error."""
    _audit(conn, tenant_id, user_id, chat_id, "create_denied", error=message)
    # get_connection rolls back when HTTPException leaves this request. Denied
    # confirmations are security audit events, so persist them first.
    conn.commit()
    raise HTTPException(status, message)


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
                "ai_service_id": agent.get("ai_service_id"),
                "model_override": agent.get("model_override"),
                "reasoning_effort": agent.get("reasoning_effort"),
                "file_ids": [
                    file_id for file_id in (agent.get("file_ids") or []) if isinstance(file_id, str)
                ],
            }
        )
    if not safe_agents or any(not a["description"] or not a["prompt"] for a in safe_agents):
        raise HTTPException(400, "Cada agente precisa de descrição e prompt")
    plan = {
        "name": name[:200],
        "description": description[:2000],
        "supervisor_prompt": supervisor_prompt[:12000],
        "agents": safe_agents,
        "supervisor_ai_service_id": raw.get("supervisor_ai_service_id"),
        "supervisor_model_override": raw.get("supervisor_model_override"),
        "supervisor_reasoning_effort": raw.get("supervisor_reasoning_effort"),
        "max_steps": raw.get("max_steps", 6),
        "datasource_ids": [
            item for item in (raw.get("datasource_ids") or []) if isinstance(item, str)
        ],
        "write_tables": [item for item in (raw.get("write_tables") or []) if isinstance(item, str)],
        "require_write_confirmation": raw.get("require_write_confirmation", True),
    }
    try:
        VersionIn(
            supervisor_prompt=plan["supervisor_prompt"],
            agents=[AgentIn(**agent) for agent in plan["agents"]],
            supervisor_ai_service_id=plan["supervisor_ai_service_id"],
            supervisor_model_override=plan["supervisor_model_override"],
            supervisor_reasoning_effort=plan["supervisor_reasoning_effort"],
            max_steps=plan["max_steps"],
            datasource_ids=plan["datasource_ids"],
            write_tables=plan["write_tables"],
            require_write_confirmation=plan["require_write_confirmation"],
        )
    except ValidationError as exc:
        raise HTTPException(400, "Prévia de template inválida") from exc
    return plan


@router.post("/{action}")
async def tenant_guide(action: str, request: Request):
    _internal(request)
    body = await request.json()
    tenant_id, user_id, chat_id = body.get("tenant_id"), body.get("user_id"), body.get("chat_id")
    if not all(isinstance(v, str) and v for v in (tenant_id, user_id, chat_id)):
        raise HTTPException(400, "Contexto de execução inválido")
    with get_connection() as conn:
        actor = _actor(conn, tenant_id, user_id)
        _guide_chat(conn, tenant_id=tenant_id, user_id=user_id, chat_id=chat_id)
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
                _deny_create(
                    conn, tenant_id, user_id, chat_id, 403, "Sem permissão para criar template"
                )
            approval = conn.execute(
                """SELECT plan, created_at FROM tenant_guide_audit
                   WHERE id=%s AND tenant_id=%s AND user_id=%s AND chat_id=%s
                     AND action='plan'""",
                (confirmation_id, tenant_id, user_id, chat_id),
            ).fetchone()
            if approval is None:
                _deny_create(
                    conn,
                    tenant_id,
                    user_id,
                    chat_id,
                    409,
                    "Aguarde confirmação explícita do usuário",
                )
            confirmation = conn.execute(
                """SELECT content FROM chat_messages
                   WHERE chat_id=%s AND role='user' AND created_at >= %s
                   ORDER BY created_at DESC LIMIT 1""",
                (chat_id, approval["created_at"]),
            ).fetchone()
            if confirmation is None or not _CONFIRMATION.search(confirmation["content"]):
                _deny_create(
                    conn,
                    tenant_id,
                    user_id,
                    chat_id,
                    409,
                    "Aguarde confirmação explícita do usuário",
                )
            plan = approval["plan"]
            result = create_guided_template(
                conn, tenant_id=tenant_id, created_by=user_id, plan=plan
            )
            _audit(conn, tenant_id, user_id, chat_id, "created", plan=plan, result=result)
            return result
    raise HTTPException(404, "Tool do guia não encontrada")
