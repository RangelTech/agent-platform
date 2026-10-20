"""Rotas internas: só serviço (kernel de skills e gerenciador de execução).

Autenticação: `Authorization: Bearer <token>` com o token de serviço
(`HARNESS_INTERNAL_TOKEN`) ou, nas rotas de uma run, o `run_token` daquela run.
Tenant, usuário e chat vêm SEMPRE do registro da run (`chat_runs`), nunca do
corpo. Documentação: backend/docs/harness-internal-api.md.
"""

from __future__ import annotations

import hmac
import re
from datetime import UTC, datetime, timedelta
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Request
from psycopg.errors import UniqueViolation
from psycopg.types.json import Json
from pydantic import BaseModel, Field

from app import chat_runs
from app import skill_package as pkg
from app.config import settings
from app.db import get_connection
from app.run_tokens import verify_run_token

router = APIRouter(prefix="/internal", tags=["internal"])

MAX_PENDING_PER_USER = 20
DEFAULT_APPROVAL_TTL = 600


# ------------------------------------------------------------------ auth ---


def _bearer(request: Request) -> str:
    header = request.headers.get("authorization", "")
    scheme, _, token = header.partition(" ")
    if scheme.lower() != "bearer" or not token:
        raise HTTPException(status_code=401, detail="Não autorizado")
    return token


def _is_service(token: str) -> bool:
    expected = settings.harness_internal_token
    return bool(expected) and hmac.compare_digest(token, expected)


def service_only(request: Request) -> None:
    if not _is_service(_bearer(request)):
        raise HTTPException(status_code=401, detail="Não autorizado")


def _load_run(run_id: str) -> dict:
    try:
        row = get_run(run_id)
    except Exception:  # id malformado  # noqa: BLE001
        row = None
    if row is None:
        raise HTTPException(status_code=404, detail="Execução não encontrada")
    return row


def get_run(run_id: str) -> dict | None:
    with get_connection() as conn:
        return conn.execute("SELECT * FROM chat_runs WHERE run_id::text = %s", (run_id,)).fetchone()


def run_auth(run_id: str, request: Request) -> dict:
    """Devolve a linha de `chat_runs` se o chamador pode agir sobre a run."""
    token = _bearer(request)
    run = _load_run(run_id)
    if _is_service(token):
        return run
    claims = verify_run_token(token)
    if claims is None or claims.get("run_id") != str(run["run_id"]):
        raise HTTPException(status_code=401, detail="Não autorizado")
    if run["status"] not in ("running", "cancelling"):
        raise HTTPException(status_code=401, detail="Execução encerrada")
    return run


def _run_dep(run_id: str, request: Request) -> dict:
    return run_auth(run_id, request)


# ----------------------------------------------------------------- usage ---


class UsageIn(BaseModel):
    input_tokens: int = Field(ge=0, le=100_000_000)
    output_tokens: int = Field(ge=0, le=100_000_000)
    cost_usd: float = Field(default=0, ge=0, le=100_000)
    provider: str = Field(default="runtime", max_length=100)
    model: str = Field(default="agent", max_length=200)
    latency_ms: int = Field(default=0, ge=0)
    agent_name: str = Field(default="", max_length=200)


@router.post("/runs/{run_id}/usage")
def post_usage(run_id: str, payload: UsageIn, run: dict = Depends(_run_dep)):
    with get_connection() as conn:
        conn.execute(
            """INSERT INTO usage_records
                   (tenant_id, user_id, chat_id, agent_name, provider, model,
                    prompt_tokens, completion_tokens, cost_usd, latency_ms)
               VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)""",
            (
                run["tenant_id"],
                run["user_id"],
                run["chat_id"],
                payload.agent_name,
                payload.provider,
                payload.model,
                payload.input_tokens,
                payload.output_tokens,
                payload.cost_usd,
                payload.latency_ms,
            ),
        )
        conn.execute(
            """INSERT INTO tenant_usage_hourly (tenant_id, hour, runs, input_tokens, output_tokens)
               VALUES (%s, date_trunc('hour', now()), 1, %s, %s)
               ON CONFLICT (tenant_id, hour) DO UPDATE
                  SET runs = tenant_usage_hourly.runs + 1,
                      input_tokens = tenant_usage_hourly.input_tokens + EXCLUDED.input_tokens,
                      output_tokens = tenant_usage_hourly.output_tokens + EXCLUDED.output_tokens""",
            (run["tenant_id"], payload.input_tokens, payload.output_tokens),
        )
    return {"ok": True}


# ------------------------------------------------------------- schedules ---


@router.post("/runs/{run_id}/schedules")
def post_schedule(run_id: str, payload: dict, run: dict = Depends(_run_dep)):
    """Rotinas pedidas pelo agente (rt_schedule_*): escopo = dono da run."""
    from app import scheduler

    return scheduler.handle_kernel_op(run, payload)


# ------------------------------------------------------------- artifacts ---


class ArtifactIn(BaseModel):
    artifact_id: str | None = None
    kind: Literal["dataset", "chart", "document", "file", "image"] = "file"
    title: str = Field(default="", max_length=500)
    content_type: str = Field(default="application/octet-stream", max_length=200)
    storage_path: str = Field(min_length=1, max_length=2000)
    row_count: int | None = Field(default=None, ge=0)
    columns: dict | list | None = None
    preview_json: dict | list | None = None
    agent_name: str = Field(default="agent", max_length=200)


@router.post("/runs/{run_id}/artifacts", status_code=201)
def post_artifact(run_id: str, payload: ArtifactIn, run: dict = Depends(_run_dep)):
    tenant = str(run["tenant_id"])
    # O arquivo precisa morar no espaço da empresa da run: impede registrar um
    # artefato apontando para o arquivo de outra empresa.
    if tenant not in payload.storage_path or ".." in payload.storage_path:
        raise HTTPException(
            status_code=400, detail="Caminho de armazenamento fora do espaço da empresa"
        )
    with get_connection() as conn:
        try:
            row = conn.execute(
                """INSERT INTO artifacts
                       (id, tenant_id, chat_id, agent_name, kind, title, schema_json,
                        preview_json, row_count, storage_path, content_type)
                   VALUES (COALESCE(%s::uuid, gen_random_uuid()), %s, %s, %s, %s, %s, %s, %s,
                           %s, %s, %s) RETURNING id""",
                (
                    payload.artifact_id,
                    run["tenant_id"],
                    run["chat_id"],
                    payload.agent_name,
                    payload.kind,
                    payload.title,
                    Json(payload.columns) if payload.columns is not None else None,
                    Json(payload.preview_json) if payload.preview_json is not None else None,
                    payload.row_count,
                    payload.storage_path,
                    payload.content_type,
                ),
            ).fetchone()
        except UniqueViolation as exc:
            raise HTTPException(status_code=409, detail="Artefato já registrado") from exc
    return {"artifact_id": str(row["id"])}


# -------------------------------------------------------------- messages ---


class MessageIn(BaseModel):
    role: Literal["assistant"] = "assistant"
    content: str = Field(min_length=1, max_length=200_000)
    attachments: list[dict] = Field(default_factory=list)


@router.post("/runs/{run_id}/messages")
def post_message(run_id: str, payload: MessageIn, run: dict = Depends(_run_dep)):
    message_id = chat_runs.save_assistant_message(
        str(run["run_id"]), run["chat_id"], payload.content, payload.attachments
    )
    return {"saved": message_id is not None, "message_id": message_id}


# ----------------------------------------------------------------- status ---


class StatusIn(BaseModel):
    status: Literal["done", "error", "cancelled"]


@router.post("/runs/{run_id}/status")
def post_status(run_id: str, payload: StatusIn, run: dict = Depends(_run_dep)):
    chat_runs.finish_run(str(run["run_id"]), payload.status)
    return {"ok": True}


# ------------------------------------------------------------ tool audit ---


class ToolAuditIn(BaseModel):
    tool: str = Field(min_length=1, max_length=200)
    credential_kind: str | None = Field(default=None, max_length=100)
    ok: bool


@router.post("/runs/{run_id}/tool-audit", status_code=201)
def post_tool_audit(run_id: str, payload: ToolAuditIn, run: dict = Depends(_run_dep)):
    with get_connection() as conn:
        conn.execute(
            """INSERT INTO tool_audit
                   (run_id, tenant_id, user_id, chat_id, template_id, tool, credential_kind, ok)
               VALUES (%s, %s, %s, %s, %s, %s, %s, %s)""",
            (
                run["run_id"],
                run["tenant_id"],
                run["user_id"],
                run["chat_id"],
                run["template_id"],
                payload.tool,
                payload.credential_kind,
                payload.ok,
            ),
        )
    return {"ok": True}


# ---------------------------------------------------------------- package ---


@router.get("/runs/{run_id}/package")
def get_package(run_id: str, run: dict = Depends(_run_dep)):
    from app.harness_runtime import package_files, package_sha, wire_file

    if not run["version_id"]:
        raise HTTPException(status_code=404, detail="Pacote não encontrado")
    with get_connection() as conn:
        files = package_files(conn, run["version_id"], run["tenant_id"])
    return {"sha256": package_sha(files), "files": [wire_file(f) for f in files]}


# ------------------------------------------------------------ user skills ---


class SkillProposalIn(BaseModel):
    name: str = Field(min_length=1, max_length=64)
    description: str = Field(default="", max_length=pkg.MAX_DESCRIPTION)
    content: str = Field(min_length=1, max_length=200_000)
    scope: Literal["template", "user"] = "user"


@router.post("/runs/{run_id}/skills", status_code=201)
def propose_skill(run_id: str, payload: SkillProposalIn, run: dict = Depends(_run_dep)):
    from app.routes.skills import audit, check_user_skill

    if not run["user_id"]:
        raise HTTPException(
            status_code=400, detail="Esta conversa não tem usuário para guardar skills"
        )
    with get_connection() as conn:
        problems, data = check_user_skill(conn, run["tenant_id"], payload.content, payload.name)
        if pkg.has_errors(problems):
            raise HTTPException(
                status_code=422,
                detail="; ".join(
                    f"{p.code}: {p.message}" for p in problems if p.severity == "error"
                ),
            )
        template_id = run["template_id"] if payload.scope == "template" else None
        existing = conn.execute(
            """SELECT id, status FROM user_skills
                WHERE tenant_id = %s AND user_id = %s AND name = %s
                  AND COALESCE(template_id, '00000000-0000-0000-0000-000000000000'::uuid)
                    = COALESCE(%s::uuid, '00000000-0000-0000-0000-000000000000'::uuid)""",
            (run["tenant_id"], run["user_id"], payload.name, template_id),
        ).fetchone()
        description = str(data.get("description") or payload.description)
        if existing is None:
            pending = conn.execute(
                """SELECT count(*) AS n FROM user_skills
                    WHERE tenant_id = %s AND user_id = %s AND status = 'pending_review'""",
                (run["tenant_id"], run["user_id"]),
            ).fetchone()["n"]
            if pending >= MAX_PENDING_PER_USER:
                raise HTTPException(status_code=409, detail="Há skills demais aguardando revisão")
            row = conn.execute(
                """INSERT INTO user_skills (tenant_id, user_id, template_id, name, description,
                                            content, source, status, origin_chat_id)
                   VALUES (%s, %s, %s, %s, %s, %s, 'agent', 'pending_review', %s) RETURNING *""",
                (
                    run["tenant_id"],
                    run["user_id"],
                    template_id,
                    payload.name,
                    description,
                    payload.content,
                    run["chat_id"],
                ),
            ).fetchone()
        else:
            # Uma proposta nova para uma skill existente volta para revisão: o
            # que a pessoa aprovou antes não vale para o texto novo.
            row = conn.execute(
                """UPDATE user_skills
                      SET content = %s, description = %s, revision = revision + 1,
                          status = 'pending_review', source = 'agent',
                          origin_chat_id = %s, updated_at = now()
                    WHERE id = %s RETURNING *""",
                (payload.content, description, run["chat_id"], existing["id"]),
            ).fetchone()
        audit(
            conn,
            tenant_id=run["tenant_id"],
            user_id=run["user_id"],
            actor="agent",
            action="propose",
            skill_ref=payload.name,
        )
    return {"user_skill_id": str(row["id"]), "name": row["name"], "status": row["status"]}


@router.get("/skills/user/{tenant_id}/{user_id}", dependencies=[Depends(service_only)])
def user_skills_for_materialization(tenant_id: str, user_id: str, template_id: str | None = None):
    """Skills ativas do usuário (a pasta que o kernel materializa em disco)."""
    from app.harness_runtime import active_user_skills

    with get_connection() as conn:
        owner = conn.execute(
            "SELECT 1 FROM users WHERE id::text = %s AND tenant_id::text = %s AND is_active",
            (user_id, tenant_id),
        ).fetchone()
        if owner is None:
            raise HTTPException(status_code=404, detail="Usuário não encontrado")
        skills = active_user_skills(conn, tenant_id, user_id, template_id)
    return {"skills": skills}


# -------------------------------------------------------------- approvals ---


class ApprovalIn(BaseModel):
    run_id: str
    summary: str = Field(min_length=1, max_length=2000)
    risk: Literal["low", "medium", "high"] = "medium"
    kind: str = Field(default="action", max_length=100)
    details: dict = Field(default_factory=dict)
    ttl_seconds: int = Field(default=DEFAULT_APPROVAL_TTL, ge=30, le=3600)


@router.post("/approvals", status_code=201)
def create_approval(payload: ApprovalIn, request: Request):
    run = run_auth(payload.run_id, request)
    if not run["user_id"]:
        # Canal externo: a política do agente decide (negar ou fila humana);
        # aqui não há quem confirme.
        raise HTTPException(status_code=400, detail="Esta conversa não tem usuário para confirmar")
    expires = datetime.now(UTC) + timedelta(seconds=payload.ttl_seconds)
    with get_connection() as conn:
        row = conn.execute(
            """INSERT INTO approvals (run_id, tenant_id, user_id, chat_id, summary, risk,
                                      kind, details, expires_at)
               VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s) RETURNING *""",
            (
                run["run_id"],
                run["tenant_id"],
                run["user_id"],
                run["chat_id"],
                payload.summary,
                payload.risk,
                payload.kind,
                Json(payload.details),
                expires,
            ),
        ).fetchone()
    return {
        "approval_id": str(row["approval_id"]),
        "status": row["status"],
        "expires_at": row["expires_at"].isoformat(),
    }


@router.get("/approvals/{approval_id}")
def get_approval(approval_id: str, request: Request):
    token = _bearer(request)
    with get_connection() as conn:
        row = conn.execute(
            "SELECT * FROM approvals WHERE approval_id::text = %s", (approval_id,)
        ).fetchone()
        if row is None:
            raise HTTPException(status_code=404, detail="Aprovação não encontrada")
        if not _is_service(token):
            claims = verify_run_token(token)
            if claims is None or claims.get("run_id") != str(row["run_id"]):
                raise HTTPException(status_code=401, detail="Não autorizado")
        if row["status"] == "pending" and row["expires_at"] <= datetime.now(UTC):
            conn.execute(
                "UPDATE approvals SET status = 'expired' WHERE approval_id = %s",
                (row["approval_id"],),
            )
            row["status"] = "expired"
    return {
        "approval_id": str(row["approval_id"]),
        "run_id": str(row["run_id"]),
        "status": row["status"],
        "decided_at": row["decided_at"].isoformat() if row["decided_at"] else None,
    }


# ------------------------------------------------------------ environment ---


class EnvironmentStateIn(BaseModel):
    state: Literal["stopped", "starting", "running", "degraded", "suspended"] | None = None
    plan: str | None = Field(default=None, max_length=100)
    usage: dict | None = None
    last_backup_at: datetime | None = None
    apps: list[dict] | None = None


_APP_NAME = re.compile(r"^[a-z0-9][a-z0-9-]{0,40}$")


@router.put("/environment/{tenant_id}", dependencies=[Depends(service_only)])
def put_environment_state(tenant_id: str, payload: EnvironmentStateIn):
    """O gerenciador de execução informa o estado observado do ambiente."""
    with get_connection() as conn:
        tenant = conn.execute("SELECT id FROM tenants WHERE id::text = %s", (tenant_id,)).fetchone()
        if tenant is None:
            raise HTTPException(status_code=404, detail="Empresa não encontrada")
        conn.execute(
            """INSERT INTO tenant_environment (tenant_id, enabled, state)
               VALUES (%s, true, 'stopped') ON CONFLICT (tenant_id) DO NOTHING""",
            (tenant["id"],),
        )
        if payload.state:
            conn.execute(
                "UPDATE tenant_environment SET state = %s, updated_at = now() WHERE tenant_id = %s",
                (payload.state, tenant["id"]),
            )
        if payload.plan is not None:
            conn.execute(
                "UPDATE tenant_environment SET plan = %s WHERE tenant_id = %s",
                (payload.plan, tenant["id"]),
            )
        if payload.usage is not None:
            conn.execute(
                "UPDATE tenant_environment SET usage = %s WHERE tenant_id = %s",
                (Json(payload.usage), tenant["id"]),
            )
        if payload.last_backup_at is not None:
            conn.execute(
                "UPDATE tenant_environment SET last_backup_at = %s WHERE tenant_id = %s",
                (payload.last_backup_at, tenant["id"]),
            )
        if payload.apps is not None:
            names = []
            for app in payload.apps:
                name = str(app.get("name") or "")
                port = app.get("port")
                if not _APP_NAME.match(name) or not isinstance(port, int):
                    raise HTTPException(status_code=422, detail="Aplicativo inválido")
                status = (
                    app.get("status")
                    if app.get("status") in ("online", "offline", "waking")
                    else "online"
                )
                conn.execute(
                    """INSERT INTO tenant_exposures (tenant_id, name, port, url, status)
                       VALUES (%s, %s, %s, %s, %s)
                       ON CONFLICT (tenant_id, name) DO UPDATE
                          SET port = EXCLUDED.port, url = EXCLUDED.url, status = EXCLUDED.status""",
                    (tenant["id"], name, port, app.get("url"), status),
                )
                names.append(name)
            conn.execute(
                "DELETE FROM tenant_exposures WHERE tenant_id = %s AND NOT (name = ANY(%s))",
                (tenant["id"], names),
            )
    return {"ok": True}
