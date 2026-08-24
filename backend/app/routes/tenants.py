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

from app import litellm_client
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
    """Cria o Team LiteLLM de um tenant recém-criado, fora do caminho da
    requisição, e reflete o resultado em `tenants.router_provisioning_status`.

    Achado real 24/08/2026: este hook chamava `scripts/provisionar_router.py`
    (9Router) até agora — mas o 9Router foi 100% desligado em 21/08/2026
    (infra-04, ver `concluidas/infra-04-litellm-substitui-9router.md`).
    Nenhum tenant criado depois da migração jamais recebeu um Team de
    verdade: o script rodava contra uma infra que não existe mais (ou nem
    rodava, com a flag desligada), e a tela "Contas de IA" ficava presa em
    "empresa ainda não conectada" pra sempre — não havia caminho de
    self-service nenhum, só o script manual `provisionar_litellm.py`
    (pensado pra uso do dono via terminal, não pro fluxo automático).

    Agora chama `litellm_client` direto (3 chamadas HTTP: criar Team, liberar
    o fallback local pro Team, gerar as 2 virtual keys) e grava o resultado
    em `tenant_routers` na mesma transação lógica do `/instancias-litellm`
    (`ai_router.py`) -- sem SSH, sem DNS, sem subprocess: o LiteLLM é uma
    instância só, compartilhada, criar um Team é rápido o bastante pra rodar
    em processo mesmo dentro do `BackgroundTasks`.

    Nunca levanta exceção: uma falha de provisionamento deixa o tenant como
    estava, só a coluna de status reflete o problema. Sem retry automático.
    """
    if not settings.router_auto_provision_enabled:
        # Feature flag off (default em dev/tests/CI): mantém 'pending' --
        # nada rodou, nada pra reportar como falha.
        return

    _set_provisioning_status(tenant_id, "provisioning")
    try:
        base_url = resolver_segredo("LITELLM_BASE_URL")
        master_key = resolver_segredo("LITELLM_MASTER_KEY")
        if not base_url or not master_key:
            raise RuntimeError("LiteLLM não configurado nesta instalação")

        async def _provisionar() -> tuple[str, str, str]:
            team = await litellm_client.create_team(base_url, master_key, team_alias=tenant_key)
            team_id = team["team_id"]
            # Fallback local liberado desde já pro Team (mesmo motivo de
            # `provisionar_litellm.py`: sem isso, a virtual key leva 403
            # `team_model_access_denied` na primeira vez que o fallback
            # precisar entrar em ação).
            await litellm_client.add_model_to_team(
                base_url, master_key, team_id=team_id, model_name="ragentes-local-fallback"
            )
            bridge_key = await litellm_client.generate_key(
                base_url, master_key, team_id=team_id, key_alias=f"{tenant_key}-bridge"
            )
            ai_assist_key = await litellm_client.generate_key(
                base_url, master_key, team_id=team_id, key_alias=f"{tenant_key}-ai-assist"
            )
            return team_id, bridge_key, ai_assist_key

        # `litellm-router` roda com min-instances=0 (achado real 23/08/2026,
        # decisão deliberada -- menos tráfego direto de usuário que os outros
        # serviços) e seu cold start real (~28s) fica na borda do timeout de
        # 30s de `litellm_client` (`TIMEOUT`, por request). Uma tentativa
        # de retry absorve exatamente esse caso: a 2ª chamada já bate numa
        # instância quente e responde em <1s -- sem isso, todo tenant criado
        # com o serviço frio falharia o provisionamento por um problema de
        # timing, não de configuração.
        try:
            team_id, bridge_key, ai_assist_key = await asyncio.wait_for(
                _provisionar(), timeout=settings.router_provision_timeout_seconds
            )
        except litellm_client.LiteLLMError:
            # Retry chama `_provisionar()` do zero -- se o cold start acontecer
            # já depois do `create_team` (raro, é a 1ª chamada da sequência),
            # sobra um Team órfão no LiteLLM sem custo/tráfego associado, só
            # id desperdiçado. Aceitável: o caso comum (timeout na 1ª chamada,
            # antes de qualquer Team existir) é o que isto resolve de verdade.
            logger.warning(
                "1ª tentativa de provisionar %s falhou (provável cold start do "
                "litellm-router) -- tentando de novo",
                tenant_key,
            )
            team_id, bridge_key, ai_assist_key = await asyncio.wait_for(
                _provisionar(), timeout=settings.router_provision_timeout_seconds
            )
    except TimeoutError:
        logger.error("provisionamento do LiteLLM expirou para o tenant %s", tenant_key)
        _set_provisioning_status(tenant_id, "failed", "Tempo esgotado ao provisionar o Team de IA")
        return
    except Exception as exc:  # LiteLLM fora do ar, master key errada, etc.
        logger.exception("falha ao provisionar o Team LiteLLM para %s", tenant_key)
        _set_provisioning_status(tenant_id, "failed", str(exc)[:500])
        return

    with get_connection() as conn:
        conn.execute(
            """INSERT INTO tenant_routers
                   (tenant_id, litellm_team_id, bridge_key_encrypted, ai_assist_key_encrypted)
               VALUES (%s, %s, %s, %s)
               ON CONFLICT (tenant_id) DO UPDATE
                   SET litellm_team_id = EXCLUDED.litellm_team_id,
                       bridge_key_encrypted = EXCLUDED.bridge_key_encrypted,
                       ai_assist_key_encrypted = EXCLUDED.ai_assist_key_encrypted,
                       is_active = TRUE,
                       updated_at = now()""",
            (tenant_id, team_id, encrypt(bridge_key), encrypt(ai_assist_key)),
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
