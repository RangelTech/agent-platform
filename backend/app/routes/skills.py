"""Skills em duas camadas.

Da empresa: as do pacote dos agentes por skills ativos (somente leitura aqui, editam-se no
agente) e as compartilhadas do tenant (`tenant_skills`), que quem gere a empresa cria, edita,
liga e desliga para todos. Pessoais: as do usuário (o agente propõe, a pessoa revisa, ou a
pessoa escreve a sua); nunca visíveis a outro usuário.

Nenhuma rota daqui lista ou devolve as skills de sistema.
"""

from __future__ import annotations

import base64
import hashlib
import mimetypes

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from app import skill_package as pkg
from app.auth import current_user, require
from app.db import get_connection
from app.kernel_router import tenant_skills_enabled
from app.permissions import has_permission, require_scope_for_chatwoot

router = APIRouter(prefix="/api", tags=["skills"])

_STATUSES = ("pending_review", "active", "disabled")


# ------------------------------------------------------------ da empresa ---


def company_skill_names(conn, tenant_id) -> set[str]:
    rows = conn.execute(
        """SELECT DISTINCT substring(f.path from '^skills/([^/]+)/SKILL\\.md$') AS name
             FROM templates t
             JOIN template_skill_files f ON f.version_id = t.active_version_id
            WHERE t.tenant_id = %s AND t.agent_kind = 'skills' AND NOT t.is_deleted
              AND f.kind = 'skill'""",
        (tenant_id,),
    ).fetchall()
    names = {r["name"] for r in rows if r["name"]}
    names |= {
        r["name"]
        for r in conn.execute(
            "SELECT name FROM tenant_skills WHERE tenant_id = %s", (tenant_id,)
        ).fetchall()
    }
    return names


def can_manage_company(user: dict) -> bool:
    """Quem edita agentes ou gere as skills da empresa edita as da empresa para todos."""
    return has_permission(user, "skills", "edit") or has_permission(user, "templates", "edit")


def _require_manage_company(user: dict) -> None:
    if user["tenant_id"] is None or not can_manage_company(user):
        raise HTTPException(status_code=403, detail="Permissão negada")
    if user.get("via") == "chatwoot" and not (
        {"ai.skills.manage_company", "ai.agents.edit"} & set(user.get("scopes", ()))
    ):
        raise HTTPException(status_code=403, detail="Permissão negada")


def _company_view(row: dict, with_content: bool = False) -> dict:
    out = {
        "id": str(row["id"]),
        "scope": "company",
        "editable": True,
        "name": row["name"],
        "description": row["description"],
        "status": row["status"],
        "revision": row["revision"],
        "template_id": None,
        "template_name": None,
        "updated_at": row["updated_at"].isoformat(),
    }
    if with_content:
        out["content"] = row["content"]
    return out


def _first_line(text: str) -> str:
    for line in text.splitlines():
        line = line.strip().lstrip("#").strip()
        if line:
            return line[:200]
    return ""


@router.get("/skills/company")
def list_company_skills(user: dict = Depends(require("skills", "view"))):
    if user["is_master"]:
        return []
    with get_connection() as conn:
        if not tenant_skills_enabled(conn, user["tenant_id"]):
            return []
        rows = conn.execute(
            """SELECT t.id AS template_id, t.name AS template_name, f.content,
                      f.kind AS file_kind, v.created_at
                 FROM templates t
                 JOIN template_versions v ON v.id = t.active_version_id
                 JOIN template_skill_files f ON f.version_id = v.id
                      AND f.kind IN ('agent', 'skill')
                WHERE t.tenant_id = %s AND t.agent_kind = 'skills' AND NOT t.is_deleted
                ORDER BY t.name, (f.kind <> 'agent'), f.path""",
            (user["tenant_id"],),
        ).fetchall()
        shared = conn.execute(
            "SELECT * FROM tenant_skills WHERE tenant_id = %s ORDER BY name", (user["tenant_id"],)
        ).fetchall()
    out = []
    for r in rows:
        data, body, err = pkg.split_frontmatter(r["content"])
        if r["file_kind"] == "agent":
            # AGENTE.md: aparece sempre, mesmo sem frontmatter, para o agente nunca sumir da tela.
            data = data or {}
            name, description = "AGENTE.md", str(data.get("description") or _first_line(body))
        else:
            if err or not data:
                continue
            name, description = str(data.get("name") or ""), str(data.get("description") or "")
        out.append(
            {
                "template_id": str(r["template_id"]),
                "template_name": r["template_name"],
                "kind": "agent" if r["file_kind"] == "agent" else "skill",
                "name": name,
                "description": description,
                "updated_at": r["created_at"].isoformat(),
                "scope": "template",
                "editable": False,
                "status": "active",
            }
        )
    manager = can_manage_company(user)
    out.extend(_company_view(r) for r in shared if manager or r["status"] == "active")
    return out


def _company_row(conn, skill_id: str, user: dict) -> dict:
    row = None
    if user["tenant_id"] is not None:
        row = conn.execute(
            "SELECT * FROM tenant_skills WHERE id::text = %s AND tenant_id = %s",
            (skill_id, user["tenant_id"]),
        ).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="Skill não encontrada")
    return row


def check_company_skill(conn, tenant_id, content: str, expected_name: str | None):
    problems, data = pkg.validate_company_skill(content, expected_name)
    name = data.get("name") if data else None
    if isinstance(name, str) and not expected_name and name in company_skill_names(conn, tenant_id):
        problems.append(
            pkg.Problem(
                "SKILL.md", "duplicate_name", "Já existe uma skill da empresa com esse nome", line=1
            )
        )
    return problems, data


class CompanySkillIn(BaseModel):
    content: str = Field(min_length=1, max_length=200_000)


@router.get("/skills/company/{skill_id}")
def get_company_skill(skill_id: str, user: dict = Depends(require("skills", "view"))):
    with get_connection() as conn:
        row = _company_row(conn, skill_id, user)
        if row["status"] != "active" and not can_manage_company(user):
            raise HTTPException(status_code=404, detail="Skill não encontrada")
        return _company_view(row, with_content=True)


@router.post("/skills/company", status_code=201)
def create_company_skill(payload: CompanySkillIn, user: dict = Depends(current_user)):
    _require_manage_company(user)
    with get_connection() as conn:
        if not tenant_skills_enabled(conn, user["tenant_id"]):
            raise HTTPException(status_code=403, detail="Agentes por skills não estão ativos")
        problems, data = check_company_skill(conn, user["tenant_id"], payload.content, None)
        if pkg.has_errors(problems):
            return _problems_response(problems)
        row = conn.execute(
            """INSERT INTO tenant_skills (tenant_id, name, description, content, created_by,
                                          updated_by)
               VALUES (%s, %s, %s, %s, %s, %s) RETURNING *""",
            (
                user["tenant_id"],
                data["name"],
                str(data["description"]),
                payload.content,
                user["id"],
                user["id"],
            ),
        ).fetchone()
        audit(
            conn,
            tenant_id=user["tenant_id"],
            user_id=user["id"],
            actor="user",
            action="company_create",
            skill_ref=row["name"],
        )
    return _company_view(row, with_content=True)


@router.put("/skills/company/{skill_id}")
def update_company_skill(
    skill_id: str, payload: CompanySkillIn, user: dict = Depends(current_user)
):
    _require_manage_company(user)
    with get_connection() as conn:
        row = _company_row(conn, skill_id, user)
        problems, data = check_company_skill(conn, user["tenant_id"], payload.content, row["name"])
        if pkg.has_errors(problems):
            return _problems_response(problems)
        updated = conn.execute(
            """UPDATE tenant_skills
                  SET content = %s, description = %s, revision = revision + 1,
                      updated_by = %s, updated_at = now()
                WHERE id = %s RETURNING *""",
            (payload.content, str(data["description"]), user["id"], row["id"]),
        ).fetchone()
        audit(
            conn,
            tenant_id=user["tenant_id"],
            user_id=user["id"],
            actor="user",
            action="company_edit",
            skill_ref=row["name"],
        )
    return _company_view(updated, with_content=True)


@router.delete("/skills/company/{skill_id}")
def delete_company_skill(skill_id: str, user: dict = Depends(current_user)):
    _require_manage_company(user)
    with get_connection() as conn:
        row = _company_row(conn, skill_id, user)
        conn.execute("DELETE FROM tenant_skills WHERE id = %s", (row["id"],))
        audit(
            conn,
            tenant_id=user["tenant_id"],
            user_id=user["id"],
            actor="user",
            action="company_delete",
            skill_ref=row["name"],
        )
    return {"status": "ok"}


def _company_toggle(skill_id: str, user: dict, status: str, action: str):
    _require_manage_company(user)
    with get_connection() as conn:
        row = _company_row(conn, skill_id, user)
        updated = conn.execute(
            """UPDATE tenant_skills SET status = %s, updated_by = %s, updated_at = now()
                WHERE id = %s RETURNING *""",
            (status, user["id"], row["id"]),
        ).fetchone()
        audit(
            conn,
            tenant_id=user["tenant_id"],
            user_id=user["id"],
            actor="user",
            action=action,
            skill_ref=row["name"],
        )
    return _company_view(updated)


@router.post("/skills/company/{skill_id}/enable")
def enable_company_skill(skill_id: str, user: dict = Depends(current_user)):
    return _company_toggle(skill_id, user, "active", "company_enable")


@router.post("/skills/company/{skill_id}/disable")
def disable_company_skill(skill_id: str, user: dict = Depends(current_user)):
    return _company_toggle(skill_id, user, "disabled", "company_disable")


# ------------------------------------------------------------- do usuário ---


def _view(row: dict, with_content: bool = False) -> dict:
    out = {
        "id": str(row["id"]),
        "name": row["name"],
        "description": row["description"],
        "status": row["status"],
        "source": row["source"],
        "template_id": str(row["template_id"]) if row["template_id"] else None,
        "origin_chat_id": row["origin_chat_id"],
        "revision": row["revision"],
        "created_at": row["created_at"].isoformat(),
        "updated_at": row["updated_at"].isoformat(),
    }
    if with_content:
        out["content"] = row["content"]
    return out


def _own(conn, skill_id: str, user: dict) -> dict:
    if user["tenant_id"] is None:
        raise HTTPException(status_code=404, detail="Skill não encontrada")
    row = conn.execute(
        """SELECT * FROM user_skills
            WHERE id::text = %s AND tenant_id = %s AND user_id = %s
              AND status <> 'discarded'""",
        (skill_id, user["tenant_id"], user["id"]),
    ).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="Skill não encontrada")
    return row


def audit(conn, *, tenant_id, user_id, actor: str, action: str, skill_ref: str) -> None:
    conn.execute(
        """INSERT INTO skill_audit (tenant_id, user_id, actor, action, skill_ref)
           VALUES (%s, %s, %s, %s, %s)""",
        (tenant_id, user_id, actor, action, skill_ref),
    )


def _problems_response(problems: list[pkg.Problem]):
    return JSONResponse(
        status_code=422,
        content={
            "detail": "A skill tem problemas e não pode ser salva",
            "problems": [p.as_dict() for p in problems],
        },
    )


def check_user_skill(conn, tenant_id, content: str, expected_name: str | None):
    """(problemas, frontmatter). Também recusa colisão com skill da empresa."""
    problems, data = pkg.validate_user_skill(content, expected_name)
    name = data.get("name") if data else None
    if isinstance(name, str) and name.removeprefix("u-") in company_skill_names(conn, tenant_id):
        problems.append(
            pkg.Problem(
                "SKILL.md", "duplicate_name", "Já existe uma skill da empresa com esse nome", line=1
            )
        )
    return problems, data


@router.get("/me/skills")
def list_my_skills(status: str | None = Query(default=None), user: dict = Depends(current_user)):
    require_scope_for_chatwoot(user, "ai.skills.view")
    if status is not None and status not in _STATUSES:
        raise HTTPException(status_code=400, detail="Situação inválida")
    if user["tenant_id"] is None:
        return []
    with get_connection() as conn:
        rows = conn.execute(
            """SELECT * FROM user_skills
                WHERE tenant_id = %s AND user_id = %s AND status <> 'discarded'
                  AND (%s::text IS NULL OR status = %s)
                ORDER BY updated_at DESC""",
            (user["tenant_id"], user["id"], status, status),
        ).fetchall()
    return [_view(r) for r in rows]


class SkillCreate(BaseModel):
    content: str = Field(min_length=1, max_length=200_000)


@router.post("/me/skills", status_code=201)
def create_my_skill(payload: SkillCreate, user: dict = Depends(current_user)):
    """A pessoa escreve a própria skill: nasce ativa (quem escreve já revisou)."""
    require_scope_for_chatwoot(user, "ai.skills.manage_own")
    if user["tenant_id"] is None:
        raise HTTPException(status_code=404, detail="Skill não encontrada")
    with get_connection() as conn:
        problems, data = check_user_skill(conn, user["tenant_id"], payload.content, None)
        if pkg.has_errors(problems):
            return _problems_response(problems)
        if _active_count(conn, user) >= pkg.MAX_USER_SKILLS_ACTIVE:
            raise HTTPException(
                status_code=409,
                detail=f"Limite de {pkg.MAX_USER_SKILLS_ACTIVE} skills ativas atingido",
            )
        dup = conn.execute(
            """SELECT 1 FROM user_skills WHERE tenant_id = %s AND user_id = %s AND name = %s
                  AND template_id IS NULL""",
            (user["tenant_id"], user["id"], data["name"]),
        ).fetchone()
        if dup:
            raise HTTPException(status_code=409, detail="Você já tem uma skill com esse nome")
        row = conn.execute(
            """INSERT INTO user_skills (tenant_id, user_id, name, description, content, source,
                                        status)
               VALUES (%s, %s, %s, %s, %s, 'user', 'active') RETURNING *""",
            (
                user["tenant_id"],
                user["id"],
                data["name"],
                str(data["description"]),
                payload.content,
            ),
        ).fetchone()
        audit(
            conn,
            tenant_id=user["tenant_id"],
            user_id=user["id"],
            actor="user",
            action="create",
            skill_ref=row["name"],
        )
    return _view(row, with_content=True)


@router.get("/me/skills/{skill_id}")
def get_my_skill(skill_id: str, user: dict = Depends(current_user)):
    require_scope_for_chatwoot(user, "ai.skills.view")
    with get_connection() as conn:
        return _view(_own(conn, skill_id, user), with_content=True)


class SkillUpdate(BaseModel):
    description: str | None = Field(default=None, max_length=pkg.MAX_DESCRIPTION)
    content: str = Field(min_length=1, max_length=200_000)


@router.put("/me/skills/{skill_id}")
def update_my_skill(skill_id: str, payload: SkillUpdate, user: dict = Depends(current_user)):
    require_scope_for_chatwoot(user, "ai.skills.manage_own")
    with get_connection() as conn:
        row = _own(conn, skill_id, user)
        content = payload.content
        if payload.description is not None:
            data, body, err = pkg.split_frontmatter(content)
            if not err and data is not None:
                data["description"] = payload.description
                content = pkg.render_frontmatter(data, body)
        problems, data = check_user_skill(conn, user["tenant_id"], content, row["name"])
        if pkg.has_errors(problems):
            return _problems_response(problems)
        updated = conn.execute(
            """UPDATE user_skills
                  SET content = %s, description = %s, revision = revision + 1,
                      updated_at = now()
                WHERE id = %s RETURNING *""",
            (content, str(data.get("description")), row["id"]),
        ).fetchone()
        audit(
            conn,
            tenant_id=user["tenant_id"],
            user_id=user["id"],
            actor="user",
            action="edit",
            skill_ref=row["name"],
        )
    return _view(updated, with_content=True)


@router.delete("/me/skills/{skill_id}")
def delete_my_skill(skill_id: str, user: dict = Depends(current_user)):
    require_scope_for_chatwoot(user, "ai.skills.manage_own")
    with get_connection() as conn:
        row = _own(conn, skill_id, user)
        conn.execute("DELETE FROM user_skills WHERE id = %s", (row["id"],))
        audit(
            conn,
            tenant_id=user["tenant_id"],
            user_id=user["id"],
            actor="user",
            action="delete",
            skill_ref=row["name"],
        )
    return {"status": "ok"}


def _active_count(conn, user: dict) -> int:
    return conn.execute(
        """SELECT count(*) AS n FROM user_skills
            WHERE tenant_id = %s AND user_id = %s AND status = 'active'""",
        (user["tenant_id"], user["id"]),
    ).fetchone()["n"]


def _transition(skill_id: str, user: dict, action: str, new_status: str, allowed: tuple[str, ...]):
    with get_connection() as conn:
        row = _own(conn, skill_id, user)
        if row["status"] not in allowed:
            raise HTTPException(
                status_code=409, detail="Esta ação não vale para a situação atual da skill"
            )
        if new_status == "active":
            if _active_count(conn, user) >= pkg.MAX_USER_SKILLS_ACTIVE:
                raise HTTPException(
                    status_code=409,
                    detail=f"Limite de {pkg.MAX_USER_SKILLS_ACTIVE} skills ativas atingido",
                )
            # Revalida na ativação: o texto pode ter sido gravado antes de uma regra nova.
            problems, _ = check_user_skill(conn, user["tenant_id"], row["content"], row["name"])
            if pkg.has_errors(problems):
                return _problems_response(problems)
        updated = conn.execute(
            "UPDATE user_skills SET status = %s, updated_at = now() WHERE id = %s RETURNING *",
            (new_status, row["id"]),
        ).fetchone()
        audit(
            conn,
            tenant_id=user["tenant_id"],
            user_id=user["id"],
            actor="user",
            action=action,
            skill_ref=row["name"],
        )
    return _view(updated)


@router.post("/me/skills/{skill_id}/approve")
def approve_my_skill(skill_id: str, user: dict = Depends(current_user)):
    require_scope_for_chatwoot(user, "ai.skills.manage_own")
    return _transition(skill_id, user, "approve", "active", ("pending_review", "disabled"))


@router.post("/me/skills/{skill_id}/discard")
def discard_my_skill(skill_id: str, user: dict = Depends(current_user)):
    require_scope_for_chatwoot(user, "ai.skills.manage_own")
    with get_connection() as conn:
        row = _own(conn, skill_id, user)
        updated = conn.execute(
            "UPDATE user_skills SET status = 'discarded', updated_at = now() "
            "WHERE id = %s RETURNING *",
            (row["id"],),
        ).fetchone()
        audit(
            conn,
            tenant_id=user["tenant_id"],
            user_id=user["id"],
            actor="user",
            action="discard",
            skill_ref=row["name"],
        )
    return _view(updated)


@router.post("/me/skills/{skill_id}/disable")
def disable_my_skill(skill_id: str, user: dict = Depends(current_user)):
    require_scope_for_chatwoot(user, "ai.skills.manage_own")
    return _transition(skill_id, user, "disable", "disabled", ("active", "pending_review"))


@router.post("/me/skills/{skill_id}/enable")
def enable_my_skill(skill_id: str, user: dict = Depends(current_user)):
    require_scope_for_chatwoot(user, "ai.skills.manage_own")
    return _transition(skill_id, user, "enable", "active", ("disabled",))


# --- Spec 11 §4: arquivos da skill da empresa e vínculo agente <-> skills ---------------------

MAX_TENANT_SKILL_BYTES = 500 * 1024 * 1024


@router.get("/skills/company/{skill_id}/files")
def list_company_skill_files(skill_id: str, user: dict = Depends(require("skills", "view"))):
    with get_connection() as conn:
        row = _company_row(conn, skill_id, user)
        files = conn.execute(
            """SELECT id, name, mime, size_bytes, created_at FROM skill_files
                WHERE skill_id = %s ORDER BY name""",
            (row["id"],),
        ).fetchall()
        used = conn.execute(
            "SELECT COALESCE(SUM(size_bytes), 0) AS n FROM skill_files WHERE tenant_id = %s",
            (user["tenant_id"],),
        ).fetchone()["n"]
    return {
        "files": [
            {
                "id": str(f["id"]),
                "name": f["name"],
                "mime": f["mime"],
                "size_bytes": f["size_bytes"],
                "created_at": f["created_at"].isoformat(),
            }
            for f in files
        ],
        "used_bytes": int(used),
        "limit_bytes": MAX_TENANT_SKILL_BYTES,
    }


class SkillFileIn(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    mime: str | None = None
    content_b64: str = Field(min_length=1, max_length=15_000_000)


@router.post("/skills/company/{skill_id}/files", status_code=201)
def upload_company_skill_file(
    skill_id: str, payload: SkillFileIn, user: dict = Depends(current_user)
):
    _require_manage_company(user)
    try:
        data = base64.b64decode(payload.content_b64, validate=True)
    except ValueError:
        raise HTTPException(status_code=400, detail="Arquivo inválido") from None
    name = payload.name.replace("\\", "/").split("/")[-1].strip()
    problems = pkg.validate_asset(f"{pkg.ASSETS_DIR}/{name}", data)
    if name == "" or pkg.has_errors(problems):
        return _problems_response(problems or [pkg.Problem(name, "bad_path", "Nome inválido")])
    digest = hashlib.sha256(data).hexdigest()
    mime = payload.mime or mimetypes.guess_type(name)[0] or "application/octet-stream"
    with get_connection() as conn:
        row = _company_row(conn, skill_id, user)
        used = conn.execute(
            """SELECT COALESCE(SUM(size_bytes), 0) AS n FROM skill_files
                WHERE tenant_id = %s AND NOT (skill_id = %s AND name = %s)""",
            (user["tenant_id"], row["id"], name),
        ).fetchone()["n"]
        if used + len(data) > MAX_TENANT_SKILL_BYTES:
            raise HTTPException(
                status_code=413, detail="Limite de 500 MB de arquivos de skills da empresa atingido"
            )
        saved = conn.execute(
            """INSERT INTO skill_files (tenant_id, skill_id, name, mime, size_bytes, sha256, data, created_by)
               VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
               ON CONFLICT (skill_id, name) DO UPDATE
                  SET mime = EXCLUDED.mime, size_bytes = EXCLUDED.size_bytes,
                      sha256 = EXCLUDED.sha256, data = EXCLUDED.data
               RETURNING id, name, size_bytes""",
            (user["tenant_id"], row["id"], name, mime, len(data), digest, data, user["id"]),
        ).fetchone()
        conn.execute(
            "UPDATE tenant_skills SET revision = revision + 1, updated_at = now() WHERE id = %s",
            (row["id"],),
        )
    return {"id": str(saved["id"]), "name": saved["name"], "size_bytes": saved["size_bytes"]}


@router.delete("/skills/company/{skill_id}/files/{file_id}")
def delete_company_skill_file(skill_id: str, file_id: str, user: dict = Depends(current_user)):
    _require_manage_company(user)
    with get_connection() as conn:
        row = _company_row(conn, skill_id, user)
        conn.execute(
            "DELETE FROM skill_files WHERE id::text = %s AND skill_id = %s", (file_id, row["id"])
        )
        conn.execute(
            "UPDATE tenant_skills SET revision = revision + 1, updated_at = now() WHERE id = %s",
            (row["id"],),
        )
    return {"ok": True}


class AgentSkillsIn(BaseModel):
    skill_ids: list[str] = Field(default_factory=list, max_length=200)


@router.get("/skills/agent/{template_id}")
def get_agent_skills(template_id: str, user: dict = Depends(require("skills", "view"))):
    with get_connection() as conn:
        linked = {
            str(r["skill_id"])
            for r in conn.execute(
                "SELECT skill_id FROM agent_skills WHERE template_id::text = %s AND tenant_id = %s",
                (template_id, user["tenant_id"]),
            ).fetchall()
        }
        rows = conn.execute(
            """SELECT s.id, s.name, s.description,
                      (SELECT count(*) FROM skill_files f WHERE f.skill_id = s.id) AS files
                 FROM tenant_skills s WHERE s.tenant_id = %s AND s.status = 'active' ORDER BY s.name""",
            (user["tenant_id"],),
        ).fetchall()
    return {
        "skills": [
            {
                "id": str(r["id"]),
                "name": r["name"],
                "description": r["description"],
                "files": r["files"],
                "linked": str(r["id"]) in linked,
            }
            for r in rows
        ]
    }


@router.put("/skills/agent/{template_id}")
def set_agent_skills(template_id: str, payload: AgentSkillsIn, user: dict = Depends(current_user)):
    _require_manage_company(user)
    with get_connection() as conn:
        tpl = conn.execute(
            "SELECT id FROM templates WHERE id::text = %s AND tenant_id = %s",
            (template_id, user["tenant_id"]),
        ).fetchone()
        if tpl is None:
            raise HTTPException(status_code=404, detail="Agente não encontrado")
        valid = {
            str(r["id"])
            for r in conn.execute(
                "SELECT id FROM tenant_skills WHERE tenant_id = %s", (user["tenant_id"],)
            ).fetchall()
        }
        ids = [i for i in dict.fromkeys(payload.skill_ids) if i in valid]
        conn.execute("DELETE FROM agent_skills WHERE template_id = %s", (tpl["id"],))
        for pos, sid in enumerate(ids):
            conn.execute(
                "INSERT INTO agent_skills (template_id, skill_id, tenant_id, position) VALUES (%s, %s, %s, %s)",
                (tpl["id"], sid, user["tenant_id"], pos),
            )
    return {"linked": ids}


# --- Spec 11 §23: Sr Moon, resetar o que ele aprendeu ----------------------------------------


@router.post("/me/moon/reset")
def reset_moon_memory(everyone: bool = False, user: dict = Depends(current_user)):
    if user.get("via") == "chatwoot" and "ai.moon.use" not in set(user.get("scopes", ())):
        raise HTTPException(status_code=403, detail="Permissão negada")
    with get_connection() as conn:
        tpl = conn.execute(
            "SELECT id FROM templates WHERE tenant_id = %s AND system_key = 'moon' AND NOT is_deleted",
            (user["tenant_id"],),
        ).fetchone()
        if tpl is None:
            return {"removed": 0}
        if everyone:
            if not can_manage_company(user):
                raise HTTPException(status_code=403, detail="Permissão negada")
            cur = conn.execute(
                "DELETE FROM user_skills WHERE tenant_id = %s AND template_id = %s",
                (user["tenant_id"], tpl["id"]),
            )
        else:
            cur = conn.execute(
                "DELETE FROM user_skills WHERE tenant_id = %s AND user_id = %s AND template_id = %s",
                (user["tenant_id"], user["id"], tpl["id"]),
            )
    return {"removed": cur.rowcount}
