import asyncio
import logging

from fastapi import (
    APIRouter,
    BackgroundTasks,
    Depends,
    File,
    HTTPException,
    Query,
    Response,
    UploadFile,
)
from psycopg.errors import UniqueViolation
from psycopg.types.json import Json
from pydantic import BaseModel, EmailStr, Field

from app import gateway_client
from app.auth import current_user
from app.config import settings
from app.crypto import encrypt
from app.db import get_connection
from app.installation_secrets import resolver as resolver_segredo
from app.permissions import ADMIN_PERMISSIONS, MEMBER_PERMISSIONS
from app.schemas import TenantIn, TenantUpdate
from app.security import hash_password

logger = logging.getLogger(__name__)

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
    brand_secondary_color: str = Field(default="", pattern=r"^(#[0-9a-fA-F]{6})?$")
    brand_theme: str = Field(default="light", pattern="^(dark|light)$")


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
        "brand_secondary_color": row.get("brand_secondary_color", ""),
        "brand_theme": row.get("brand_theme", "light"),
        "branding_version": row.get("branding_version", 1),
        "branding_sync_status": row.get("branding_sync_status", "pending"),
        "has_logo": bool(row.get("brand_logo_url")),
        "is_active": row["is_active"],
        "harness_enabled": bool(row.get("harness_enabled", False)),
        "graph_enabled": bool(row.get("graph_enabled", True)),
        "router_provisioning_status": row.get("router_provisioning_status", "pending"),
        "router_provisioning_error": row.get("router_provisioning_error"),
    }


def _set_provisioning_status(tenant_id, status: str, error: str | None = None) -> None:
    with get_connection() as conn:
        conn.execute(
            """UPDATE tenants
                  SET router_provisioning_status = %s, router_provisioning_error = %s,
                      updated_at = now()
                WHERE id = %s""",
            (status, error, tenant_id),
        )


async def _provisionar_router_em_background(tenant_id: str, tenant_key: str) -> None:
    """Provisiona o tenant no gateway e guarda as duas chaves internas.

    Roda fora da requisição do Chatwoot. A identidade vem exclusivamente do
    banco do motor: nenhum identificador de tenant do browser chega aqui.
    """
    if not settings.router_auto_provision_enabled:
        # Feature flag off (default em dev/tests/CI): mantém 'pending' --
        # nada rodou, nada pra reportar como falha.
        return

    _set_provisioning_status(tenant_id, "provisioning")
    try:
        base_url = resolver_segredo("GATEWAY_BASE_URL")
        admin_token = resolver_segredo("GATEWAY_ADMIN_TOKEN")
        bridge_key, ai_assist_key = await asyncio.wait_for(
            gateway_client.provision_tenant(
                base_url,
                admin_token,
                tenant_id=tenant_id,
                name=tenant_key,
            ),
            timeout=settings.router_provision_timeout_seconds,
        )
    except TimeoutError:
        logger.error("provisionamento do gateway expirou para o tenant %s", tenant_key)
        _set_provisioning_status(
            tenant_id, "failed", "Tempo esgotado ao provisionar o Gateway de IA"
        )
        return
    except Exception as exc:
        logger.exception("falha ao provisionar o Gateway de IA para %s", tenant_key)
        _set_provisioning_status(tenant_id, "failed", str(exc)[:500])
        return

    with get_connection() as conn:
        conn.execute(
            """INSERT INTO tenant_routers
                   (tenant_id, gateway_tenant_id, gateway_bridge_key_encrypted,
                    gateway_ai_assist_key_encrypted)
               VALUES (%s, %s, %s, %s)
               ON CONFLICT (tenant_id) DO UPDATE
                   SET gateway_tenant_id = EXCLUDED.gateway_tenant_id,
                       gateway_bridge_key_encrypted = EXCLUDED.gateway_bridge_key_encrypted,
                       gateway_ai_assist_key_encrypted = EXCLUDED.gateway_ai_assist_key_encrypted,
                       is_active = TRUE,
                       updated_at = now()""",
            (tenant_id, tenant_id, encrypt(bridge_key), encrypt(ai_assist_key)),
        )
    _set_provisioning_status(tenant_id, "ready")


@router.get("")
def list_tenants(
    q: str = Query(default="", max_length=200),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=25, ge=1, le=100),
    user: dict = Depends(_master_only),
):
    query = q.strip()
    pattern = f"%{query}%"
    with get_connection() as conn:
        total = conn.execute(
            "SELECT count(*) AS n FROM tenants WHERE name ILIKE %s OR tenant_key ILIKE %s",
            (pattern, pattern),
        ).fetchone()["n"]
        active_total = conn.execute(
            "SELECT count(*) AS n FROM tenants WHERE is_active = true"
        ).fetchone()["n"]
        pages = max(1, (total + page_size - 1) // page_size)
        page = min(page, pages)
        rows = conn.execute(
            """SELECT * FROM tenants WHERE name ILIKE %s OR tenant_key ILIKE %s
               ORDER BY lower(name), tenant_key LIMIT %s OFFSET %s""",
            (pattern, pattern, page_size, (page - 1) * page_size),
        ).fetchall()
    return {
        "items": [_serialize(r) for r in rows],
        "total": total,
        "active_total": active_total,
        "page": page,
        "page_size": page_size,
        "total_pages": pages,
    }


@router.post("", status_code=201)
def create_tenant(
    payload: TenantWithAdminIn,
    background: BackgroundTasks,
    user: dict = Depends(_master_only),
):
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

        from app.ragentes_guide import ensure_for_tenant

        ensure_for_tenant(conn, row["id"])

    # Every tenant gets its own 9Router instance (isolation, see 0020's
    # comment) — dispatched in the background so DNS/TLS provisioning
    # (minutes) never holds this request open. The tenant exists regardless
    # of whether provisioning succeeds; see `_provisionar_router_em_background`.
    background.add_task(_provisionar_router_em_background, str(row["id"]), payload.tenant_key)
    return _serialize(row)


async def _sync_branding_to_ratende(tenant: dict) -> tuple[bool, str]:
    """Entrega a cópia de consumo ao bridge sem abrir credencial ao browser."""
    if not tenant.get("chatwoot_account_id"):
        return False, "RAtende ainda não foi provisionado"
    from app.routes.omnichannel import _bridge

    try:
        await _bridge(
            "PUT",
            "/admin/branding",
            json={
                "tenant_id": str(tenant["id"]),
                "brand_name": tenant["brand_name"] or tenant["name"],
                "primary_color": tenant["brand_color"] or "#1f93ff",
                "secondary_color": tenant["brand_secondary_color"] or "#0f766e",
                "theme": tenant["brand_theme"],
                "logo_url": (
                    f"/api/tenants/branding/logo/{tenant['tenant_key']}"
                    if tenant.get("brand_logo_url")
                    else ""
                ),
                "version": tenant["branding_version"],
            },
        )
    except Exception as exc:  # a atualização local continua sendo a verdade
        return False, str(getattr(exc, "detail", exc))[:500]
    return True, ""


@router.put("/branding")
async def update_branding(payload: BrandingIn, user: dict = Depends(current_user)):
    """Tenant-scoped: the company admin styles their own workspace."""
    from app.permissions import has_permission

    if user["is_master"] or not has_permission(user, "users", "edit"):
        # Branding belongs to the company admin, not the platform master.
        raise HTTPException(status_code=403, detail="Permissão negada")
    with get_connection() as conn:
        row = conn.execute(
            """UPDATE tenants
                  SET brand_name = %s, brand_color = %s,
                      brand_secondary_color = %s, brand_theme = %s,
                      branding_version = branding_version + 1,
                      branding_sync_status = 'pending', branding_sync_error = '',
                      updated_at = now()
                WHERE id = %s RETURNING *""",
            (
                payload.brand_name,
                payload.brand_color,
                payload.brand_secondary_color,
                payload.brand_theme,
                user["tenant_id"],
            ),
        ).fetchone()
    synced, detail = await _sync_branding_to_ratende(row)
    with get_connection() as conn:
        row = conn.execute(
            """UPDATE tenants SET branding_sync_status = %s, branding_sync_error = %s,
                      branding_synced_at = CASE WHEN %s THEN now() ELSE branding_synced_at END
                 WHERE id = %s RETURNING *""",
            ("ok" if synced else "pending", detail, synced, row["id"]),
        ).fetchone()
    return _serialize(row)


@router.post("/branding/reconcile")
async def reconcile_branding(user: dict = Depends(current_user)):
    from app.permissions import has_permission

    if user["is_master"] or not has_permission(user, "users", "edit"):
        raise HTTPException(status_code=403, detail="Permissão negada")
    with get_connection() as conn:
        tenant = conn.execute(
            "SELECT * FROM tenants WHERE id = %s", (user["tenant_id"],)
        ).fetchone()
    synced, detail = await _sync_branding_to_ratende(tenant)
    with get_connection() as conn:
        row = conn.execute(
            """UPDATE tenants SET branding_sync_status = %s, branding_sync_error = %s,
                      branding_synced_at = CASE WHEN %s THEN now() ELSE branding_synced_at END
                 WHERE id = %s RETURNING *""",
            ("ok" if synced else "pending", detail, synced, tenant["id"]),
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
    path = save_bytes(f"tenants/{user['tenant_id']}/branding/logo", data, content_type)
    with get_connection() as conn:
        tenant = conn.execute(
            """UPDATE tenants SET brand_logo_url = %s, branding_version = branding_version + 1,
                      branding_sync_status = 'pending', branding_sync_error = '', updated_at = now()
                 WHERE id = %s RETURNING *""",
            (f"{path}|{content_type}", user["tenant_id"]),
        ).fetchone()
    synced, detail = await _sync_branding_to_ratende(tenant)
    with get_connection() as conn:
        row = conn.execute(
            """UPDATE tenants SET branding_sync_status = %s, branding_sync_error = %s,
                      branding_synced_at = CASE WHEN %s THEN now() ELSE branding_synced_at END
                 WHERE id = %s RETURNING *""",
            ("ok" if synced else "pending", detail, synced, tenant["id"]),
        ).fetchone()
    return {"status": "ok" if synced else "pending", "branding": _serialize(row)}


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
def update_tenant(tenant_id: str, payload: TenantUpdate, user: dict = Depends(_master_only)):
    fields, values = [], []
    if payload.name is not None:
        fields.append("name = %s")
        values.append(payload.name)
    if payload.is_active is not None:
        fields.append("is_active = %s")
        values.append(payload.is_active)
    if payload.harness_enabled is not None:
        fields.append("harness_enabled = %s")
        values.append(payload.harness_enabled)
    if payload.graph_enabled is not None:
        fields.append("graph_enabled = %s")
        values.append(payload.graph_enabled)
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
