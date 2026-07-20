from fastapi import APIRouter, Depends, File, HTTPException, Response, UploadFile
from psycopg.errors import UniqueViolation
from psycopg.types.json import Json
from pydantic import BaseModel, EmailStr, Field

from app.auth import current_user
from app.db import get_connection
from app.permissions import ADMIN_PERMISSIONS, MEMBER_PERMISSIONS
from app.schemas import TenantIn, TenantUpdate
from app.security import hash_password

router = APIRouter(prefix="/api/tenants", tags=["tenants"])


class TenantWithAdminIn(TenantIn):
    """Company + (optionally) its first admin in one step — with the admin,
    creating the company is the master's whole job."""

    admin_name: str | None = Field(default=None, min_length=1, max_length=200)
    admin_email: EmailStr | None = None
    admin_password: str | None = Field(default=None, min_length=8, max_length=200)


class BrandingIn(BaseModel):
    brand_name: str = Field(default="", max_length=200)
    brand_color: str = Field(default="", pattern=r"^(#[0-9a-fA-F]{6})?$")
    brand_theme: str = Field(default="dark", pattern="^(dark|light)$")


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
        "brand_name": row.get("brand_name", ""),
        "brand_color": row.get("brand_color", ""),
        "brand_theme": row.get("brand_theme", "dark"),
        "has_logo": bool(row.get("brand_logo_url")),
        "is_active": row["is_active"],
    }


@router.get("")
def list_tenants(user: dict = Depends(_master_only)):
    with get_connection() as conn:
        rows = conn.execute("SELECT * FROM tenants ORDER BY name").fetchall()
    return [_serialize(r) for r in rows]


@router.post("", status_code=201)
def create_tenant(payload: TenantWithAdminIn, user: dict = Depends(_master_only)):
    admin_fields = (payload.admin_name, payload.admin_email, payload.admin_password)
    if any(admin_fields) and not all(admin_fields):
        raise HTTPException(
            status_code=400,
            detail="Informe nome, e-mail e senha do admin (ou nenhum dos três)",
        )
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
        # Every tenant starts with usable profiles; the first admin (when
        # provided) is created in the same transaction — one-step onboarding.
        admin_profile_id = None
        for name, permissions in (
            ("Administrador", ADMIN_PERMISSIONS),
            ("Usuário", MEMBER_PERMISSIONS),
        ):
            profile = conn.execute(
                """INSERT INTO user_profiles (tenant_id, name, permissions)
                   VALUES (%s, %s, %s) RETURNING id""",
                (row["id"], name, Json(permissions)),
            ).fetchone()
            if name == "Administrador":
                admin_profile_id = profile["id"]

        if all(admin_fields):
            try:
                conn.execute(
                    """INSERT INTO users (tenant_id, profile_id, email, name, password_hash)
                       VALUES (%s, %s, %s, %s, %s)""",
                    (
                        row["id"],
                        admin_profile_id,
                        payload.admin_email,
                        payload.admin_name,
                        hash_password(payload.admin_password),
                    ),
                )
            except UniqueViolation as exc:
                raise HTTPException(
                    status_code=409, detail="Já existe um usuário com esse e-mail"
                ) from exc
    return _serialize(row)


@router.put("/branding")
def update_branding(payload: BrandingIn, user: dict = Depends(current_user)):
    """Tenant-scoped: the company admin styles their own workspace."""
    from app.permissions import has_permission

    if user["is_master"] or not has_permission(user, "users", "edit"):
        # Branding belongs to the company admin, not the platform master.
        raise HTTPException(status_code=403, detail="Permissão negada")
    with get_connection() as conn:
        row = conn.execute(
            """UPDATE tenants
                  SET brand_name = %s, brand_color = %s, brand_theme = %s,
                      updated_at = now()
                WHERE id = %s RETURNING *""",
            (payload.brand_name, payload.brand_color, payload.brand_theme, user["tenant_id"]),
        ).fetchone()
    return _serialize(row)


@router.post("/branding/logo")
async def upload_logo(file: UploadFile = File(...), user: dict = Depends(current_user)):
    from app.permissions import has_permission
    from app.storage import save_bytes

    if user["is_master"] or not has_permission(user, "users", "edit"):
        raise HTTPException(status_code=403, detail="Permissão negada")
    content_type = file.content_type or ""
    if content_type not in ("image/png", "image/jpeg", "image/svg+xml", "image/webp"):
        raise HTTPException(status_code=400, detail="Logo deve ser PNG, JPEG, SVG ou WebP")
    data = await file.read()
    if not data or len(data) > 2 * 1024 * 1024:
        raise HTTPException(status_code=400, detail="Logo vazio ou maior que 2MB")
    path = save_bytes(
        f"tenants/{user['tenant_id']}/branding/logo", data, content_type
    )
    with get_connection() as conn:
        conn.execute(
            "UPDATE tenants SET brand_logo_url = %s, updated_at = now() WHERE id = %s",
            (f"{path}|{content_type}", user["tenant_id"]),
        )
    return {"status": "ok"}


@router.get("/branding/logo/{tenant_key}")
def get_logo(tenant_key: str):
    """Unauthenticated by design: logos are public assets (login screens,
    <img> tags can't send Authorization headers)."""
    with get_connection() as conn:
        row = conn.execute(
            "SELECT brand_logo_url FROM tenants WHERE tenant_key = %s", (tenant_key,)
        ).fetchone()
    if row is None or not row["brand_logo_url"]:
        raise HTTPException(status_code=404, detail="Sem logo")
    storage_path, _, content_type = row["brand_logo_url"].rpartition("|")
    from app.artifacts_io import load_bytes

    try:
        data = load_bytes(storage_path)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=404, detail="Logo indisponível") from exc
    return Response(content=data, media_type=content_type or "image/png")


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
