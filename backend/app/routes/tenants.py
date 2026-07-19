from fastapi import APIRouter, Depends, HTTPException
from psycopg.errors import UniqueViolation
from psycopg.types.json import Json

from app.auth import current_user
from app.db import get_connection
from app.permissions import ADMIN_PERMISSIONS, MEMBER_PERMISSIONS
from app.schemas import TenantIn, TenantUpdate

router = APIRouter(prefix="/api/tenants", tags=["tenants"])


def _master_only(user: dict = Depends(current_user)) -> dict:
    """Tenants are platform-level: only the master manages them."""
    if not user["is_master"]:
        raise HTTPException(status_code=403, detail="Permissão negada")
    return user


def _serialize(row: dict) -> dict:
    return {
        "id": str(row["id"]),
        "tenant_key": row["tenant_key"],
        "name": row["name"],
        "is_active": row["is_active"],
    }


@router.get("")
def list_tenants(user: dict = Depends(_master_only)):
    with get_connection() as conn:
        rows = conn.execute("SELECT * FROM tenants ORDER BY name").fetchall()
    return [_serialize(r) for r in rows]


@router.post("", status_code=201)
def create_tenant(payload: TenantIn, user: dict = Depends(_master_only)):
    with get_connection() as conn:
        try:
            row = conn.execute(
                "INSERT INTO tenants (tenant_key, name) VALUES (%s, %s) RETURNING *",
                (payload.tenant_key, payload.name),
            ).fetchone()
        except UniqueViolation as exc:
            raise HTTPException(
                status_code=409, detail="Já existe um tenant com essa chave"
            ) from exc
        # Every tenant starts with usable profiles, so an admin can be created
        # immediately after.
        for name, permissions in (
            ("Administrador", ADMIN_PERMISSIONS),
            ("Usuário", MEMBER_PERMISSIONS),
        ):
            conn.execute(
                """INSERT INTO user_profiles (tenant_id, name, permissions)
                   VALUES (%s, %s, %s)""",
                (row["id"], name, Json(permissions)),
            )
    return _serialize(row)


@router.put("/{tenant_id}")
def update_tenant(
    tenant_id: str, payload: TenantUpdate, user: dict = Depends(_master_only)
):
    fields, values = [], []
    if payload.name is not None:
        fields.append("name = %s")
        values.append(payload.name)
    if payload.is_active is not None:
        fields.append("is_active = %s")
        values.append(payload.is_active)
    if not fields:
        raise HTTPException(status_code=400, detail="Nada para atualizar")

    fields.append("updated_at = now()")
    values.append(tenant_id)
    with get_connection() as conn:
        row = conn.execute(
            f"UPDATE tenants SET {', '.join(fields)} WHERE id = %s RETURNING *",
            tuple(values),
        ).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="Tenant não encontrado")
    return _serialize(row)
