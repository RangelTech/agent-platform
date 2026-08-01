from fastapi import APIRouter, Depends, HTTPException
from psycopg.errors import UniqueViolation

from app.auth import require
from app.db import get_connection
from app.schemas import UserIn, UserUpdate
from app.security import hash_password
from app.tenancy import resolve_target_tenant

router = APIRouter(prefix="/api/users", tags=["users"])


def _serialize(row: dict) -> dict:
    return {
        "id": str(row["id"]),
        "tenant_id": str(row["tenant_id"]) if row["tenant_id"] else None,
        "profile_id": str(row["profile_id"]) if row["profile_id"] else None,
        "email": row["email"],
        "name": row["name"],
        "is_master": row["is_master"],
        "is_active": row["is_active"],
    }


def _assert_profile_in_tenant(conn, profile_id: str | None, tenant_id: str) -> None:
    """A user may only carry a profile from their own tenant — otherwise a
    tenant admin could grant themselves another tenant's permissions."""
    if profile_id is None:
        return
    row = conn.execute(
        "SELECT tenant_id FROM user_profiles WHERE id = %s", (profile_id,)
    ).fetchone()
    if row is None:
        raise HTTPException(status_code=400, detail="Perfil não encontrado")
    if row["tenant_id"] is None or str(row["tenant_id"]) != str(tenant_id):
        raise HTTPException(status_code=403, detail="Perfil não pertence ao tenant")


@router.get("")
def list_users(user: dict = Depends(require("users", "view"))):
    with get_connection() as conn:
        if user["is_master"]:
            rows = conn.execute("SELECT * FROM users ORDER BY name").fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM users WHERE tenant_id = %s ORDER BY name",
                (user["tenant_id"],),
            ).fetchall()
    return [_serialize(r) for r in rows]


def _default_profile_id(conn, tenant_id) -> str | None:
    """Perfil de entrada quando quem cria não escolhe um.

    Usuário sem perfil fica com permissão nenhuma e some da própria navegação —
    um estado quebrado que não ajuda ninguém. O primeiro usuário da empresa é o
    dono dela e entra como administrador; os seguintes entram como usuário
    comum, e o administrador promove quem precisar.
    """
    has_users = conn.execute(
        "SELECT 1 FROM users WHERE tenant_id = %s LIMIT 1", (tenant_id,)
    ).fetchone()
    wanted = "Usuário" if has_users else "Administrador"
    row = conn.execute(
        "SELECT id FROM user_profiles WHERE tenant_id = %s AND name = %s",
        (tenant_id, wanted),
    ).fetchone()
    return str(row["id"]) if row else None


@router.post("", status_code=201)
def create_user(payload: UserIn, user: dict = Depends(require("users", "create"))):
    tenant_id = resolve_target_tenant(user, payload.tenant_id)
    with get_connection() as conn:
        _assert_profile_in_tenant(conn, payload.profile_id, tenant_id)
        profile_id = payload.profile_id or _default_profile_id(conn, tenant_id)
        try:
            row = conn.execute(
                """INSERT INTO users (tenant_id, profile_id, email, name, password_hash)
                   VALUES (%s, %s, %s, %s, %s) RETURNING *""",
                (
                    tenant_id,
                    profile_id,
                    payload.email,
                    payload.name,
                    hash_password(payload.password),
                ),
            ).fetchone()
        except UniqueViolation as exc:
            raise HTTPException(
                status_code=409, detail="Já existe um usuário com esse e-mail"
            ) from exc
    return _serialize(row)


@router.put("/{user_id}")
def update_user(
    user_id: str, payload: UserUpdate, user: dict = Depends(require("users", "edit"))
):
    with get_connection() as conn:
        target = conn.execute(
            "SELECT * FROM users WHERE id = %s", (user_id,)
        ).fetchone()
        if target is None:
            raise HTTPException(status_code=404, detail="Usuário não encontrado")
        if not user["is_master"] and str(target["tenant_id"]) != str(user["tenant_id"]):
            raise HTTPException(status_code=404, detail="Usuário não encontrado")
        # The master account is platform infrastructure: not editable through
        # tenant administration.
        if target["is_master"] and not user["is_master"]:
            raise HTTPException(status_code=403, detail="Permissão negada")

        fields, values = [], []
        if payload.name is not None:
            fields.append("name = %s")
            values.append(payload.name)
        if payload.password is not None:
            fields.append("password_hash = %s")
            values.append(hash_password(payload.password))
        if payload.profile_id is not None:
            _assert_profile_in_tenant(conn, payload.profile_id, target["tenant_id"])
            fields.append("profile_id = %s")
            values.append(payload.profile_id)
        if payload.is_active is not None:
            fields.append("is_active = %s")
            values.append(payload.is_active)
        if not fields:
            raise HTTPException(status_code=400, detail="Nada para atualizar")

        fields.append("updated_at = now()")
        values.append(user_id)
        row = conn.execute(
            f"UPDATE users SET {', '.join(fields)} WHERE id = %s RETURNING *",
            tuple(values),
        ).fetchone()
    return _serialize(row)
