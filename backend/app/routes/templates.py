"""Template management: versioned supervisor+specialists definitions.

A version is an immutable snapshot; POST /{id}/deploy points the template's
active_version_id at it (rollback = deploy an older version). The chat
resolves the active version into the kernel's run payload.
"""

import base64
import binascii
import posixpath
import re

from fastapi import (
    APIRouter,
    Body,
    Depends,
    File,
    Form,
    HTTPException,
    Response,
    UploadFile,
)
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from psycopg.errors import UniqueViolation
from psycopg.types.json import Json
from pydantic import BaseModel, Field, ValidationError

from app import skill_package as pkg
from app.auth import require
from app.db import get_connection
from app.kernel_router import NOT_ENABLED, ensure_skills_enabled, tenant_skills_enabled
from app.tenancy import resolve_target_tenant
from app.tool_catalog import tool_ids

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
    file_ids: list[str] = Field(default_factory=list)


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
    write_tables: list[str] = Field(default_factory=list)
    require_write_confirmation: bool = True
    # Quantas mensagens recentes vão para o modelo a cada turno.
    history_limit: int = Field(default=100, ge=4, le=500)
    # Resumir o começo em vez de descartá-lo. Custa uma chamada a mais e
    # depende do resumo estar bom, por isso é escolha explícita.
    compress_history: bool = False
    # Teto, em caracteres, do que uma ferramenta devolve para o especialista.
    # É onde o contexto realmente estoura; ver migração 0023.
    tool_output_limit: int = Field(default=24_000, ge=2_000, le=200_000)
    notes: str = ""


class TemplateIn(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    description: str = Field(default="", max_length=2000)
    tenant_id: str | None = None  # master only
    # Imutável depois de criado. 'structured' = supervisor + especialistas;
    # 'skills' = pacote de arquivos Markdown.
    agent_kind: str = Field(default="structured", pattern=r"^(structured|skills)$")


class DraftFileIn(BaseModel):
    path: str = Field(max_length=500)
    # Arquivos de arquivos/ chegam da listagem sem texto; são ignorados aqui (têm rota própria).
    content: str | None = ""


class AssetIn(BaseModel):
    content_base64: str


class DraftIn(BaseModel):
    files: list[DraftFileIn]
    manifest: dict = Field(default_factory=dict)


class SkillsVersionIn(BaseModel):
    source: str = Field(default="draft", pattern=r"^draft$")
    notes: str = Field(default="", max_length=2000)


def _template_scoped(conn, template_id: str, user: dict) -> dict:
    row = conn.execute(
        "SELECT * FROM templates WHERE id = %s AND NOT is_deleted", (template_id,)
    ).fetchone()
    if row is None or (not user["is_master"] and str(row["tenant_id"]) != str(user["tenant_id"])):
        raise HTTPException(status_code=404, detail="Template não encontrado")
    return row


def _reject_system_template(template: dict) -> None:
    if template.get("system_key"):
        raise HTTPException(
            status_code=403, detail="Template de sistema é gerenciado pela Rangel Tech"
        )


def _serialize_template(row: dict, active_version_number: int | None = None) -> dict:
    return {
        "id": str(row["id"]),
        "tenant_id": str(row["tenant_id"]),
        "name": row["name"],
        "description": row["description"],
        "agent_kind": row.get("agent_kind") or "structured",
        "active_version_id": str(row["active_version_id"]) if row["active_version_id"] else None,
        "active_version_number": active_version_number,
        "updated_at": row["updated_at"].isoformat() if row.get("updated_at") else None,
        "system_key": row.get("system_key"),
    }


def _skills_template(conn, template_id: str, user: dict) -> dict:
    """Template do tipo `skills` do chamador, com o recurso habilitado."""
    template = _template_scoped(conn, template_id, user)
    _reject_system_template(template)
    if template["agent_kind"] != "skills":
        raise HTTPException(status_code=400, detail="Este agente não usa pacote de arquivos")
    if not user["is_master"] and not tenant_skills_enabled(conn, template["tenant_id"]):
        raise HTTPException(status_code=403, detail=NOT_ENABLED)
    return template


def _file_view(f: dict) -> dict:
    return {
        "path": f["path"],
        "content": f["content"],
        "size_bytes": pkg.size_bytes(f["content"]),
    }


@router.get("")
def list_templates(user: dict = Depends(require("templates", "view"))):
    with get_connection() as conn:
        # Com a flag da empresa desligada, os agentes por skills não aparecem.
        scope = (
            ""
            if user["is_master"]
            else " AND t.tenant_id = %s AND (t.agent_kind <> 'skills' OR tn.harness_enabled)"
        )
        params = () if user["is_master"] else (user["tenant_id"],)
        rows = conn.execute(
            f"""SELECT t.*, v.version_number AS active_version_number
                  FROM templates t
                  JOIN tenants tn ON tn.id = t.tenant_id
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
        if payload.agent_kind == "skills":
            ensure_skills_enabled(conn, tenant_id)
        try:
            row = conn.execute(
                """INSERT INTO templates (tenant_id, name, description, agent_kind)
                   VALUES (%s, %s, %s, %s) RETURNING *""",
                (tenant_id, payload.name, payload.description, payload.agent_kind),
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
            raise HTTPException(status_code=400, detail="Serviço de IA não pertence ao tenant")
    for datasource_id in payload.datasource_ids:
        row = conn.execute(
            "SELECT tenant_id FROM datasources WHERE id = %s", (datasource_id,)
        ).fetchone()
        if row is None or str(row["tenant_id"]) != str(tenant_id):
            raise HTTPException(status_code=400, detail="Fonte de dados não pertence ao tenant")
    for file_id in {f for a in payload.agents for f in a.file_ids}:
        row = conn.execute("SELECT tenant_id FROM files WHERE id = %s", (file_id,)).fetchone()
        if row is None or str(row["tenant_id"]) != str(tenant_id):
            raise HTTPException(status_code=400, detail="Arquivo não pertence ao tenant")


def _persist_version(conn, *, template_id, payload: VersionIn, created_by, version_number: int):
    """Persist the immutable snapshot used by both the UI and tenant guide.

    Keeping this operation in one service avoids a privileged guide path that
    quietly drifts from the normal template API's validation and schema.
    """
    version = conn.execute(
        """INSERT INTO template_versions
               (template_id, version_number, supervisor_prompt,
                supervisor_ai_service_id, supervisor_model_override,
                supervisor_reasoning_effort, max_steps, created_by, notes,
                write_tables, require_write_confirmation,
                history_limit, compress_history, tool_output_limit)
           VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
           RETURNING *""",
        (
            template_id,
            version_number,
            payload.supervisor_prompt,
            payload.supervisor_ai_service_id,
            payload.supervisor_model_override,
            payload.supervisor_reasoning_effort,
            payload.max_steps,
            created_by,
            payload.notes,
            Json(payload.write_tables),
            payload.require_write_confirmation,
            payload.history_limit,
            payload.compress_history,
            payload.tool_output_limit,
        ),
    ).fetchone()
    for order, agent in enumerate(payload.agents):
        agent_row = conn.execute(
            """INSERT INTO template_agents
                   (version_id, name, description, prompt, ai_service_id,
                    model_override, reasoning_effort, sort_order, tools)
               VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s) RETURNING id""",
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
        ).fetchone()
        for file_id in agent.file_ids:
            conn.execute(
                "INSERT INTO template_agent_files (agent_id, file_id) VALUES (%s, %s)",
                (agent_row["id"], file_id),
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
    return version


def create_guided_template(conn, *, tenant_id, created_by, plan: dict) -> dict:
    """Create an approved guide plan through ordinary template validation."""
    template_input = TemplateIn(name=plan["name"], description=plan["description"])
    version_input = VersionIn(
        supervisor_prompt=plan["supervisor_prompt"],
        agents=[AgentIn(**agent) for agent in plan["agents"]],
        supervisor_ai_service_id=plan.get("supervisor_ai_service_id"),
        supervisor_model_override=plan.get("supervisor_model_override"),
        supervisor_reasoning_effort=plan.get("supervisor_reasoning_effort"),
        max_steps=plan.get("max_steps", 6),
        datasource_ids=plan.get("datasource_ids", []),
        write_tables=plan.get("write_tables", []),
        require_write_confirmation=plan.get("require_write_confirmation", True),
        notes="Criado pelo Assistente RAgentes apos confirmacao",
    )
    _validate_version(conn, version_input, tenant_id)
    try:
        template = conn.execute(
            """INSERT INTO templates (tenant_id, name, description)
               VALUES (%s,%s,%s) RETURNING id""",
            (tenant_id, template_input.name, template_input.description),
        ).fetchone()
    except UniqueViolation as exc:
        raise HTTPException(409, "Ja existe um template com esse nome") from exc
    version = _persist_version(
        conn,
        template_id=template["id"],
        payload=version_input,
        created_by=created_by,
        version_number=1,
    )
    conn.execute(
        "UPDATE templates SET active_version_id=%s WHERE id=%s",
        (version["id"], template["id"]),
    )
    return {
        "template_id": str(template["id"]),
        "version_id": str(version["id"]),
        "editor_url": f"/templates/{template['id']}",
    }


def _asset_view(a: dict) -> dict:
    return {
        "path": a["path"],
        "kind": "asset",
        "is_binary": True,
        "content": None,
        "size_bytes": a["size_bytes"],
        "mime": a["mime"],
        "sha256": a["sha256"],
        "updated_at": a["updated_at"].isoformat() if a.get("updated_at") else None,
    }


def _text_files(files: list[dict]) -> list[dict]:
    """Só o texto do rascunho; arquivos/ vive em template_draft_assets."""
    return [f for f in files if not pkg.is_asset_path(f.get("path"))]


def _draft_assets(conn, template_id) -> list[dict]:
    rows = conn.execute(
        """SELECT path, mime, sha256, size_bytes, updated_at FROM template_draft_assets
            WHERE template_id = %s ORDER BY path""",
        (template_id,),
    ).fetchall()
    return [dict(r) for r in rows]


def _package_for_validation(files: list[dict], assets: list[dict]) -> list[dict]:
    return _text_files(files) + [{"path": a["path"], "size_bytes": a["size_bytes"]} for a in assets]


def _problems_response(status: int, problems: list[pkg.Problem]) -> JSONResponse:
    return JSONResponse(
        status_code=status,
        content={"detail": problems[0].message, "problems": [p.as_dict() for p in problems]},
    )


def _version_view(row: dict, active_id) -> dict:
    return {
        "id": str(row["id"]),
        "number": row["version_number"],
        "version_number": row["version_number"],
        "notes": row["notes"],
        "created_at": row["created_at"].isoformat(),
        "created_by": str(row["created_by"]) if row.get("created_by") else None,
        "is_active": bool(active_id) and str(active_id) == str(row["id"]),
    }


def _create_skills_version(conn, template: dict, user: dict, payload: SkillsVersionIn):
    draft = conn.execute(
        "SELECT files, manifest FROM template_skill_drafts WHERE template_id = %s",
        (template["id"],),
    ).fetchone()
    if draft is None:
        raise HTTPException(status_code=422, detail="Não há rascunho para publicar")
    assets = _draft_assets(conn, template["id"])
    problems = pkg.validate_package(
        _package_for_validation(draft["files"], assets), draft["manifest"], tool_ids()
    )
    if pkg.has_errors(problems):
        return JSONResponse(
            status_code=422,
            content={
                "detail": "O pacote tem problemas e não pode ser publicado",
                "problems": [p.as_dict() for p in problems],
            },
        )
    manifest = pkg.normalize_manifest(draft["manifest"])
    number = conn.execute(
        """SELECT COALESCE(MAX(version_number), 0) + 1 AS n
             FROM template_versions WHERE template_id = %s""",
        (template["id"],),
    ).fetchone()["n"]
    version = conn.execute(
        """INSERT INTO template_versions
               (template_id, version_number, supervisor_prompt, created_by, notes, manifest)
           VALUES (%s, %s, '', %s, %s, %s) RETURNING *""",
        (template["id"], number, user["id"], payload.notes, Json(manifest)),
    ).fetchone()
    for f in _text_files(draft["files"]):
        kind, _ = pkg.classify_path(f["path"])
        conn.execute(
            """INSERT INTO template_skill_files
                   (version_id, path, kind, content, sha256, size_bytes)
               VALUES (%s, %s, %s, %s, %s, %s)""",
            (
                version["id"],
                f["path"],
                kind,
                f["content"],
                pkg.sha256_text(f["content"]),
                pkg.size_bytes(f["content"]),
            ),
        )
    # Versão imutável: copia os bytes do rascunho (o rascunho pode mudar depois).
    conn.execute(
        """INSERT INTO template_skill_files
               (version_id, path, kind, content, sha256, size_bytes,
                is_binary, content_bin, mime)
           SELECT %s, path, 'asset', '', sha256, size_bytes, true, content, mime
             FROM template_draft_assets WHERE template_id = %s""",
        (version["id"], template["id"]),
    )
    return JSONResponse(
        status_code=201, content=_version_view(version, template["active_version_id"])
    )


def _validation_error(exc: ValidationError) -> RequestValidationError:
    return RequestValidationError(
        exc.errors(include_url=False, include_context=False, include_input=False)
    )


@router.post("/{template_id}/versions", status_code=201)
def create_version(
    template_id: str,
    body: dict = Body(...),
    user: dict = Depends(require("templates", "edit")),
):
    with get_connection() as conn:
        template = _template_scoped(conn, template_id, user)
        _reject_system_template(template)
        if template["agent_kind"] == "skills":
            if not user["is_master"] and not tenant_skills_enabled(conn, template["tenant_id"]):
                raise HTTPException(status_code=403, detail=NOT_ENABLED)
            try:
                skills_payload = SkillsVersionIn.model_validate(body)
            except ValidationError as exc:
                raise _validation_error(exc) from exc
            return _create_skills_version(conn, template, user, skills_payload)
        try:
            payload = VersionIn.model_validate(body)
        except ValidationError as exc:
            raise _validation_error(exc) from exc
        _validate_version(conn, payload, template["tenant_id"])

        number = conn.execute(
            """SELECT COALESCE(MAX(version_number), 0) + 1 AS n
                 FROM template_versions WHERE template_id = %s""",
            (template_id,),
        ).fetchone()["n"]
        version = _persist_version(
            conn,
            template_id=template_id,
            payload=payload,
            created_by=user["id"],
            version_number=number,
        )
    return {"id": str(version["id"]), "version_number": number, "number": number}


@router.get("/{template_id}/versions")
def list_versions(template_id: str, user: dict = Depends(require("templates", "view"))):
    with get_connection() as conn:
        template = _template_scoped(conn, template_id, user)
        rows = conn.execute(
            """SELECT id, version_number, notes, created_at, created_by
                 FROM template_versions
                WHERE template_id = %s ORDER BY version_number DESC""",
            (template_id,),
        ).fetchall()
    return [_version_view(r, template["active_version_id"]) for r in rows]


# ---------------------------------------------------------------- skills ---


def _draft_response(row: dict, assets: list[dict] | None = None) -> dict:
    return {
        "files": [_file_view(f) for f in _text_files(row["files"])]
        + [_asset_view(a) for a in assets or []],
        "manifest": pkg.normalize_manifest(row["manifest"]),
        "updated_at": row["updated_at"].isoformat(),
        "updated_by": str(row["updated_by"]) if row["updated_by"] else None,
    }


@router.get("/{template_id}/draft")
def get_draft(template_id: str, user: dict = Depends(require("templates", "view"))):
    with get_connection() as conn:
        template = _skills_template(conn, template_id, user)
        row = conn.execute(
            "SELECT * FROM template_skill_drafts WHERE template_id = %s", (template["id"],)
        ).fetchone()
        if row is None:
            files, manifest = pkg.initial_package()
            row = conn.execute(
                """INSERT INTO template_skill_drafts (template_id, files, manifest, updated_by)
                   VALUES (%s, %s, %s, %s) RETURNING *""",
                (template["id"], Json(files), Json(manifest), user["id"]),
            ).fetchone()
        assets = _draft_assets(conn, template["id"])
    return _draft_response(row, assets)


@router.put("/{template_id}/draft")
def put_draft(
    template_id: str, payload: DraftIn, user: dict = Depends(require("templates", "edit"))
):
    files = _text_files([{"path": f.path, "content": f.content} for f in payload.files])
    with get_connection() as conn:
        template = _skills_template(conn, template_id, user)
        conn.execute(
            """INSERT INTO template_skill_drafts (template_id, files, manifest, updated_by)
               VALUES (%s, %s, %s, %s)
               ON CONFLICT (template_id) DO UPDATE
                  SET files = EXCLUDED.files, manifest = EXCLUDED.manifest,
                      updated_by = EXCLUDED.updated_by, updated_at = now()""",
            (template["id"], Json(files), Json(payload.manifest), user["id"]),
        )
        conn.execute("UPDATE templates SET updated_at = now() WHERE id = %s", (template["id"],))
        assets = _draft_assets(conn, template["id"])
    problems = pkg.validate_package(
        _package_for_validation(files, assets), payload.manifest, tool_ids()
    )
    return {"ok": not pkg.has_errors(problems), "problems": [p.as_dict() for p in problems]}


@router.post("/{template_id}/validate")
def validate_draft(
    template_id: str, payload: DraftIn, user: dict = Depends(require("templates", "edit"))
):
    files = _text_files([{"path": f.path, "content": f.content} for f in payload.files])
    with get_connection() as conn:
        _skills_template(conn, template_id, user)
        assets = _draft_assets(conn, template_id)
    problems = pkg.validate_package(
        _package_for_validation(files, assets), payload.manifest, tool_ids()
    )
    return {"ok": not pkg.has_errors(problems), "problems": [p.as_dict() for p in problems]}


# ------------------------------------------- arquivos do pacote (arquivos/) ---


def _asset_path(raw: str) -> str:
    """Caminho dentro do pacote: sem pasta informada, cai em arquivos/."""
    path = (raw or "").replace("\\", "/").strip()
    if not path.startswith(pkg.ASSETS_DIR + "/"):
        path = f"{pkg.ASSETS_DIR}/{posixpath.basename(path)}"
    return path


def _store_asset(template_id: str, user: dict, raw_path: str, data: bytes):
    path = _asset_path(raw_path)
    problems = pkg.validate_asset(path, data)
    if pkg.has_errors(problems):
        return _problems_response(422, problems)
    with get_connection() as conn:
        template = _skills_template(conn, template_id, user)
        others = [a for a in _draft_assets(conn, template["id"]) if a["path"] != path]
        total = sum(a["size_bytes"] for a in others) + len(data)
        if len(others) + 1 > pkg.MAX_ASSETS:
            msg = f"Máximo de {pkg.MAX_ASSETS} arquivos na pasta"
            return _problems_response(422, [pkg.Problem(path, "too_many_files", msg)])
        if total > pkg.MAX_ASSETS_TOTAL_BYTES:
            msg = f"Os arquivos passam de {pkg.MAX_ASSETS_TOTAL_BYTES // 2**20} MB no total"
            return _problems_response(422, [pkg.Problem(path, "package_too_large", msg)])
        row = conn.execute(
            """INSERT INTO template_draft_assets
                   (template_id, path, content, mime, sha256, size_bytes, updated_by)
               VALUES (%s, %s, %s, %s, %s, %s, %s)
               ON CONFLICT (template_id, path) DO UPDATE
                  SET content = EXCLUDED.content, mime = EXCLUDED.mime,
                      sha256 = EXCLUDED.sha256, size_bytes = EXCLUDED.size_bytes,
                      updated_by = EXCLUDED.updated_by, updated_at = now()
            RETURNING path, mime, sha256, size_bytes, updated_at""",
            (
                template["id"],
                path,
                data,
                pkg.guess_mime(path),
                pkg.sha256_bytes(data),
                len(data),
                user["id"],
            ),
        ).fetchone()
        conn.execute("UPDATE templates SET updated_at = now() WHERE id = %s", (template["id"],))
    return _asset_view(dict(row))


@router.put("/{template_id}/draft/files/{path:path}")
def put_draft_file(
    template_id: str,
    path: str,
    payload: AssetIn,
    user: dict = Depends(require("templates", "edit")),
):
    """Envia ou substitui um arquivo do pacote (JSON com o conteúdo em base64)."""
    if len(payload.content_base64) > (pkg.MAX_ASSET_BYTES * 4) // 3 + 16:
        msg = f"O arquivo passa de {pkg.MAX_ASSET_BYTES // 2**20} MB"
        return _problems_response(422, [pkg.Problem(path, "file_too_large", msg)])
    try:
        data = base64.b64decode(payload.content_base64, validate=True)
    except (binascii.Error, ValueError):
        msg = "Conteúdo do arquivo inválido"
        return _problems_response(422, [pkg.Problem(path, "bad_encoding", msg)])
    return _store_asset(template_id, user, path, data)


@router.post("/{template_id}/draft/files", status_code=201)
async def upload_draft_file(
    template_id: str,
    file: UploadFile = File(...),
    path: str = Form(default=""),
    user: dict = Depends(require("templates", "edit")),
):
    """Envia ou substitui um arquivo do pacote (multipart; `path` opcional)."""
    data = await file.read(pkg.MAX_ASSET_BYTES + 1)
    return _store_asset(template_id, user, path or file.filename or "", data)


@router.get("/{template_id}/draft/files/{path:path}")
def get_draft_file(template_id: str, path: str, user: dict = Depends(require("templates", "view"))):
    with get_connection() as conn:
        template = _skills_template(conn, template_id, user)
        row = conn.execute(
            "SELECT content, mime FROM template_draft_assets WHERE template_id = %s AND path = %s",
            (template["id"], path),
        ).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="Arquivo não encontrado")
    return _download(path, bytes(row["content"]), row["mime"])


def _download(path: str, data: bytes, mime: str | None) -> Response:
    name = posixpath.basename(path).replace('"', "")
    return Response(
        content=data,
        media_type=mime or "application/octet-stream",
        headers={"Content-Disposition": f'attachment; filename="{name}"'},
    )


@router.delete("/{template_id}/draft/files/{path:path}")
def delete_draft_file(
    template_id: str, path: str, user: dict = Depends(require("templates", "edit"))
):
    with get_connection() as conn:
        template = _skills_template(conn, template_id, user)
        deleted = conn.execute(
            "DELETE FROM template_draft_assets WHERE template_id = %s AND path = %s RETURNING 1",
            (template["id"], path),
        ).fetchone()
        if deleted:
            conn.execute("UPDATE templates SET updated_at = now() WHERE id = %s", (template["id"],))
    if deleted is None:
        raise HTTPException(status_code=404, detail="Arquivo não encontrado")
    return {"ok": True}


def _skills_version(conn, template_id: str, version_id: str) -> dict:
    row = conn.execute(
        "SELECT * FROM template_versions WHERE id::text = %s AND template_id = %s",
        (version_id, template_id),
    ).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="Versão não encontrada")
    return row


@router.get("/{template_id}/versions/{version_id}/files")
def list_version_files(
    template_id: str, version_id: str, user: dict = Depends(require("templates", "view"))
):
    with get_connection() as conn:
        _skills_template(conn, template_id, user)
        version = _skills_version(conn, template_id, version_id)
        rows = conn.execute(
            """SELECT path, size_bytes, sha256, is_binary, mime FROM template_skill_files
                WHERE version_id = %s ORDER BY path""",
            (version["id"],),
        ).fetchall()
    return [dict(r) for r in rows]


@router.get("/{template_id}/versions/{version_id}/files/{path:path}")
def get_version_file(
    template_id: str,
    version_id: str,
    path: str,
    user: dict = Depends(require("templates", "view")),
):
    with get_connection() as conn:
        _skills_template(conn, template_id, user)
        version = _skills_version(conn, template_id, version_id)
        row = conn.execute(
            """SELECT path, content, content_bin, is_binary, mime FROM template_skill_files
                WHERE version_id = %s AND path = %s""",
            (version["id"], path),
        ).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="Arquivo não encontrado")
    if row["is_binary"]:
        return _download(path, bytes(row["content_bin"] or b""), row["mime"])
    return {"path": row["path"], "content": row["content"]}


@router.get("/{template_id}/versions/{version_id}")
def get_version(
    template_id: str, version_id: str, user: dict = Depends(require("templates", "view"))
):
    with get_connection() as conn:
        template = _template_scoped(conn, template_id, user)
        _reject_system_template(template)
        version = conn.execute(
            "SELECT * FROM template_versions WHERE id = %s AND template_id = %s",
            (version_id, template_id),
        ).fetchone()
        if version is None:
            raise HTTPException(status_code=404, detail="Versão não encontrada")
        if template["agent_kind"] == "skills":
            return {
                **_version_view(version, template["active_version_id"]),
                "agent_kind": "skills",
                "manifest": pkg.normalize_manifest(version["manifest"]),
            }
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
        conn2_files = conn.execute(
            """SELECT af.agent_id, af.file_id
                 FROM template_agent_files af
                 JOIN template_agents a ON a.id = af.agent_id
                WHERE a.version_id = %s""",
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
        "write_tables": version.get("write_tables") or [],
        "require_write_confirmation": version.get("require_write_confirmation", True),
        "history_limit": version.get("history_limit", 100),
        "compress_history": version.get("compress_history", False),
        "tool_output_limit": version.get("tool_output_limit", 24_000),
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
                "file_ids": [
                    str(f["file_id"]) for f in conn2_files if str(f["agent_id"]) == str(a["id"])
                ],
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


@router.delete("/{template_id}")
def archive_template(template_id: str, user: dict = Depends(require("templates", "delete"))):
    """Soft-delete: hides the template; existing conversations pinned to it
    fall back to the tenant default on their next turn."""
    with get_connection() as conn:
        template = _template_scoped(conn, template_id, user)
        _reject_system_template(template)
        conn.execute(
            "UPDATE templates SET is_deleted = TRUE, updated_at = now() WHERE id = %s",
            (template_id,),
        )
    return {"status": "ok"}
