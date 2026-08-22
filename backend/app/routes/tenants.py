import logging
import subprocess
import sys
from pathlib import Path

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

from app.auth import current_user
from app.config import settings
from app.db import get_connection
from app.permissions import ADMIN_PERMISSIONS, MEMBER_PERMISSIONS
from app.schemas import TenantIn, TenantUpdate
from app.security import hash_password

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/tenants", tags=["tenants"])

# backend/app/routes/tenants.py -> repo root -> scripts/provisionar_router.py.
# Kept as a standalone subprocess call (see `_provisionar_router_em_background`
# docstring) rather than importing the script's functions.
_PROVISIONAR_ROUTER_SCRIPT = (
    Path(__file__).resolve().parent.parent.parent.parent / "scripts" / "provisionar_router.py"
)


class TenantWithAdminIn(TenantIn):
    """Company + (optionally) its first admin in one step — with the admin,
    creating the company is the master's whole job."""

    admin_name: str | None = Field(default=None, min_length=1, max_length=200)
    admin_email: EmailStr | None = None
    admin_password: str | None = Field(default=None, min_length=8, max_length=200)


class BrandingIn(BaseModel):
    brand_name: str = Field(default="", max_length=200)
    brand_color: str = Field(default="", pattern=r"^(#[0-9a-fA-F]{6})?$")
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
        "brand_theme": row.get("brand_theme", "light"),
        "has_logo": bool(row.get("brand_logo_url")),
        "is_active": row["is_active"],
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


def _provisionar_router_em_background(tenant_id: str, tenant_key: str) -> None:
    """Runs `scripts/provisionar_router.py <tenant_key>` for a freshly created
    tenant, off the request path, and reflects the outcome on `tenants`.

    Architecture decision (subprocess, not an imported function): the script
    already owns the whole flow — SSH into the VPS, create container/volume,
    DNS, wait for TLS, and register the instance back on the platform via
    `PUT /api/ai-router/instancias`. Importing its internals would mean
    duplicating that wiring (env vars, retry loop, platform login) inside the
    request process instead of reusing the script that is also run by hand
    (`router-ia-por-tenant.md`) — two code paths for the same job, one of
    which is exercised far less. A subprocess also crashes independently: if
    it segfaults or hangs past the timeout, it cannot take the API worker
    down with it. The trade-off is that we only see this task's stdout/exit
    code, not fine-grained progress — acceptable since the screen only needs
    pending/provisioning/ready/failed, not a live log.

    Runs in FastAPI's `BackgroundTasks` thread pool (see `create_tenant`),
    so `create_tenant` itself returns immediately — DNS+TLS take minutes and
    must never hold the tenant-creation request open.

    Never raises: a broken/timed-out provisioning must leave the tenant
    exactly as it was (point 4 of the spec) — only the status column reflects
    the failure. The admin can re-run `scripts/provisionar_router.py` by hand
    once the underlying issue (VPS, DNS, image) is fixed; there is no
    automatic retry here.
    """
    if not settings.router_auto_provision_enabled:
        # Feature flag off (default in dev/tests/CI): leave status as
        # 'pending' — nothing ran, nothing to report as failed.
        return

    _set_provisioning_status(tenant_id, "provisioning")
    try:
        resultado = subprocess.run(
            [sys.executable, str(_PROVISIONAR_ROUTER_SCRIPT), tenant_key],
            capture_output=True,
            text=True,
            timeout=settings.router_provision_timeout_seconds,
        )
    except subprocess.TimeoutExpired:
        logger.error("provisionamento do router expirou para o tenant %s", tenant_key)
        _set_provisioning_status(tenant_id, "failed", "Tempo esgotado ao provisionar a instância")
        return
    except OSError as exc:  # script ausente, python inacessível, etc.
        logger.exception("falha ao iniciar o provisionamento do router para %s", tenant_key)
        _set_provisioning_status(tenant_id, "failed", str(exc)[:500])
        return

    if resultado.returncode != 0:
        detalhe = (resultado.stderr or resultado.stdout or "erro desconhecido").strip()[-500:]
        logger.error(
            "provisionamento do router falhou para %s (rc=%s): %s",
            tenant_key,
            resultado.returncode,
            detalhe,
        )
        _set_provisioning_status(tenant_id, "failed", detalhe)
        return

    # Sucesso: o próprio script já registrou a instância em `tenant_routers`
    # via `PUT /api/ai-router/instancias`. Só falta marcar o status aqui.
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
    path = save_bytes(f"tenants/{user['tenant_id']}/branding/logo", data, content_type)
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
def update_tenant(tenant_id: str, payload: TenantUpdate, user: dict = Depends(_master_only)):
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
