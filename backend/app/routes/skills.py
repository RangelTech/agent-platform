"""Skills: as da empresa (somente leitura, vêm dos agentes por skills ativos) e
as pessoais do usuário (camada 3: o agente propõe, a pessoa revisa).

Nenhuma rota daqui lista ou devolve as skills de sistema.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from app import skill_package as pkg
from app.auth import current_user, require
from app.db import get_connection
from app.kernel_router import tenant_skills_enabled

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
    return {r["name"] for r in rows if r["name"]}


@router.get("/skills/company")
def list_company_skills(user: dict = Depends(require("skills", "view"))):
    if user["is_master"]:
        return []
    with get_connection() as conn:
        if not tenant_skills_enabled(conn, user["tenant_id"]):
            return []
        rows = conn.execute(
            """SELECT t.id AS template_id, t.name AS template_name, f.content,
                      v.created_at
                 FROM templates t
                 JOIN template_versions v ON v.id = t.active_version_id
                 JOIN template_skill_files f ON f.version_id = v.id AND f.kind = 'skill'
                WHERE t.tenant_id = %s AND t.agent_kind = 'skills' AND NOT t.is_deleted
                ORDER BY t.name, f.path""",
            (user["tenant_id"],),
        ).fetchall()
    out = []
    for r in rows:
        data, _body, err = pkg.split_frontmatter(r["content"])
        if err or not data:
            continue
        out.append(
            {
                "template_id": str(r["template_id"]),
                "template_name": r["template_name"],
                "name": str(data.get("name") or ""),
                "description": str(data.get("description") or ""),
                "updated_at": r["created_at"].isoformat(),
            }
        )
    return out


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


@router.get("/me/skills/{skill_id}")
def get_my_skill(skill_id: str, user: dict = Depends(current_user)):
    with get_connection() as conn:
        return _view(_own(conn, skill_id, user), with_content=True)


class SkillUpdate(BaseModel):
    description: str | None = Field(default=None, max_length=pkg.MAX_DESCRIPTION)
    content: str = Field(min_length=1, max_length=200_000)


@router.put("/me/skills/{skill_id}")
def update_my_skill(skill_id: str, payload: SkillUpdate, user: dict = Depends(current_user)):
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
    return _transition(skill_id, user, "approve", "active", ("pending_review", "disabled"))


@router.post("/me/skills/{skill_id}/discard")
def discard_my_skill(skill_id: str, user: dict = Depends(current_user)):
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
    return _transition(skill_id, user, "disable", "disabled", ("active", "pending_review"))


@router.post("/me/skills/{skill_id}/enable")
def enable_my_skill(skill_id: str, user: dict = Depends(current_user)):
    return _transition(skill_id, user, "enable", "active", ("disabled",))
