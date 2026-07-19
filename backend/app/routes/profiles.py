from fastapi import APIRouter, Depends, HTTPException
from psycopg.types.json import Json

from app.auth import require
from app.db import get_connection
from app.permissions import validate_permissions
from app.schemas import ProfileIn, ProfileUpdate
from app.tenancy import resolve_target_tenant

router = APIRouter(prefix="/api/user-profiles", tags=["user_profiles"])


def _serialize(row: dict) -> dict:
    return {
        "id": str(row["id"]),
        "tenant_id": str(row["tenant_id"]) if row["tenant_id"] else None,
        "name": row["name"],
        "permissions": row["permissions"],
        "is_active": row["is_active"],
    }


@router.get("")
def list_profiles(user: dict = Depends(require("user_profiles", "view"))):
    with get_connection() as conn:
        if user["is_master"]:
            rows = conn.execute(
                "SELECT * FROM user_profiles ORDER BY name"
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM user_profiles WHERE tenant_id = %s ORDER BY name",
                (user["tenant_id"],),
            ).fetchall()
    return [_serialize(r) for r in rows]


@router.post("", status_code=201)
def create_profile(
    payload: ProfileIn, user: dict = Depends(require("user_profiles", "create"))
):
    try:
        validate_permissions(payload.permissions)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    tenant_id = resolve_target_tenant(user, payload.tenant_id)
    with get_connection() as conn:
        row = conn.execute(
            """INSERT INTO user_profiles (tenant_id, name, permissions)
               VALUES (%s, %s, %s) RETURNING *""",
            (tenant_id, payload.name, Json(payload.permissions)),
        ).fetchone()
    return _serialize(row)


@router.put("/{profile_id}")
def update_profile(
    profile_id: str,
    payload: ProfileUpdate,
    user: dict = Depends(require("user_profiles", "edit")),
):
    if payload.permissions is not None:
        try:
            validate_permissions(payload.permissions)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    fields, values = [], []
    if payload.name is not None:
        fields.append("name = %s")
        values.append(payload.name)
    if payload.permissions is not None:
        fields.append("permissions = %s")
        values.append(Json(payload.permissions))
    if payload.is_active is not None:
        fields.append("is_active = %s")
        values.append(payload.is_active)
    if not fields:
        raise HTTPException(status_code=400, detail="Nada para atualizar")

    fields.append("updated_at = now()")
    values.append(profile_id)
    scope = "" if user["is_master"] else " AND tenant_id = %s"
    if not user["is_master"]:
        values.append(user["tenant_id"])

    with get_connection() as conn:
        row = conn.execute(
            f"UPDATE user_profiles SET {', '.join(fields)} "
            f"WHERE id = %s{scope} RETURNING *",
            tuple(values),
        ).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="Perfil não encontrado")
    return _serialize(row)
