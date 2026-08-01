"""Contas de IA e combos do tenant, sobre a instância dedicada de 9Router.

O cliente nunca vê o 9Router: ele conecta contas, monta combos e escolhe o
combo no template. A instância, a senha administrativa e a chave de consumo
ficam do nosso lado, cifradas.

Regra de isolamento em uma frase: **a instância é resolvida pelo tenant da
sessão**, nunca por identificador vindo do cliente. Como cada tenant tem a
própria instância, não existe caminho para alcançar conta ou combo alheio.
"""

import logging
import re

from fastapi import APIRouter, Depends, HTTPException
from psycopg.types.json import Json
from pydantic import BaseModel, Field

from app import router_client
from app.auth import require
from app.crypto import encrypt
from app.db import get_connection

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/ai-router", tags=["ai-router"])

_SLUG = re.compile(r"[^a-z0-9]+")


class ContaIn(BaseModel):
    provider: str = Field(min_length=1, max_length=40)
    api_key: str = Field(min_length=1, max_length=5_000)
    label: str = Field(min_length=1, max_length=120)


class ComboIn(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    # Modelos na ordem de revezamento, no formato do 9Router: "provider/modelo".
    models: list[str] = Field(min_length=1)


def _router_do_tenant(conn, tenant_id) -> dict | None:
    return conn.execute(
        "SELECT * FROM tenant_routers WHERE tenant_id = %s AND is_active", (tenant_id,)
    ).fetchone()


def _exigir_router(user: dict) -> dict:
    if not user["tenant_id"]:
        raise HTTPException(status_code=400, detail="Usuário master não tem contas de IA")
    with get_connection() as conn:
        registro = _router_do_tenant(conn, user["tenant_id"])
    if registro is None:
        raise HTTPException(
            status_code=409,
            detail="Esta empresa ainda não tem instância de IA provisionada",
        )
    return registro


def _slug(valor: str) -> str:
    return _SLUG.sub("_", valor.strip().lower()).strip("_")[:40] or "combo"


@router.get("/status")
def status(user: dict = Depends(require("ai_router", "view"))):
    """Se a empresa já tem instância dedicada e se ela está de pé."""
    if not user["tenant_id"]:
        return {"provisionado": False, "motivo": "master"}
    with get_connection() as conn:
        registro = _router_do_tenant(conn, user["tenant_id"])
        contas = conn.execute(
            "SELECT count(*) AS n FROM tenant_ai_accounts WHERE tenant_id = %s AND is_active",
            (user["tenant_id"],),
        ).fetchone()["n"]
        combos = conn.execute(
            "SELECT count(*) AS n FROM tenant_ai_combos WHERE tenant_id = %s AND is_active",
            (user["tenant_id"],),
        ).fetchone()["n"]
    return {
        "provisionado": registro is not None,
        "contas": contas,
        "combos": combos,
    }


@router.get("/contas")
async def listar_contas(user: dict = Depends(require("ai_router", "view"))):
    registro = _exigir_router(user)
    try:
        conexoes = {c["id"]: c for c in await router_client.list_connections(registro)}
    except router_client.RouterError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc

    with get_connection() as conn:
        linhas = conn.execute(
            """SELECT * FROM tenant_ai_accounts
                WHERE tenant_id = %s AND is_active ORDER BY created_at""",
            (user["tenant_id"],),
        ).fetchall()
    return [
        {
            "id": str(r["id"]),
            "provider": r["provider"],
            "auth_type": r["auth_type"],
            "label": r["label"],
            # Conta que sumiu da instância aparece como pendente em vez de
            # desaparecer silenciosamente da tela.
            "conectada": r["router_connection_id"] in conexoes,
        }
        for r in linhas
    ]


@router.post("/contas", status_code=201)
async def criar_conta(payload: ContaIn, user: dict = Depends(require("ai_router", "create"))):
    """Conecta uma conta por API key. Conta por OAuth é conectada na própria
    instância do tenant (o consentimento tem que acontecer lá) e depois
    sincronizada por `POST /sincronizar`."""
    registro = _exigir_router(user)
    try:
        conexao = await router_client.create_api_key_connection(
            registro, provider=payload.provider, api_key=payload.api_key, label=payload.label
        )
    except router_client.RouterError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc

    with get_connection() as conn:
        linha = conn.execute(
            """INSERT INTO tenant_ai_accounts
                   (tenant_id, router_connection_id, provider, auth_type, label)
               VALUES (%s, %s, %s, %s, %s) RETURNING *""",
            (
                user["tenant_id"],
                conexao["id"],
                payload.provider,
                conexao.get("authType", "apikey"),
                payload.label,
            ),
        ).fetchone()
    return {"id": str(linha["id"]), "provider": linha["provider"], "label": linha["label"]}


@router.post("/sincronizar")
async def sincronizar(user: dict = Depends(require("ai_router", "edit"))):
    """Traz para a plataforma as contas conectadas direto na instância —
    é assim que uma conta OAuth (Claude, Codex) entra na lista."""
    registro = _exigir_router(user)
    try:
        conexoes = await router_client.list_connections(registro)
    except router_client.RouterError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc

    novas = 0
    with get_connection() as conn:
        for conexao in conexoes:
            resultado = conn.execute(
                """INSERT INTO tenant_ai_accounts
                       (tenant_id, router_connection_id, provider, auth_type, label)
                   VALUES (%s, %s, %s, %s, %s)
                   ON CONFLICT (tenant_id, router_connection_id) DO NOTHING
                   RETURNING id""",
                (
                    user["tenant_id"],
                    conexao["id"],
                    conexao.get("provider", "desconhecido"),
                    conexao.get("authType", "apikey"),
                    conexao.get("name") or conexao.get("email") or conexao.get("provider", "conta"),
                ),
            ).fetchone()
            if resultado is not None:
                novas += 1
    return {"status": "ok", "novas": novas, "total": len(conexoes)}


@router.delete("/contas/{conta_id}")
async def remover_conta(conta_id: str, user: dict = Depends(require("ai_router", "delete"))):
    registro = _exigir_router(user)
    with get_connection() as conn:
        conta = conn.execute(
            "SELECT * FROM tenant_ai_accounts WHERE id = %s AND tenant_id = %s",
            (conta_id, user["tenant_id"]),
        ).fetchone()
    if conta is None:
        raise HTTPException(status_code=404, detail="Conta não encontrada")
    try:
        await router_client.delete_connection(registro, conta["router_connection_id"])
    except router_client.RouterError as exc:
        logger.warning("conta removida da plataforma mas não da instância: %s", exc)
    with get_connection() as conn:
        conn.execute("DELETE FROM tenant_ai_accounts WHERE id = %s", (conta_id,))
    return {"status": "ok"}


@router.get("/modelos")
async def listar_modelos(user: dict = Depends(require("ai_router", "view"))):
    """Modelos disponíveis para montar combo — vêm das contas do tenant."""
    registro = _exigir_router(user)
    try:
        modelos = await router_client.list_models(registro)
    except router_client.RouterError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    # Combos também aparecem em /v1/models; não se monta combo de combo.
    return [
        {"id": m["id"], "provider": m.get("owned_by")}
        for m in modelos
        if m.get("owned_by") != "combo"
    ]


@router.get("/combos")
def listar_combos(user: dict = Depends(require("ai_router", "view"))):
    with get_connection() as conn:
        linhas = conn.execute(
            """SELECT * FROM tenant_ai_combos
                WHERE tenant_id = %s AND is_active ORDER BY created_at""",
            (user["tenant_id"],),
        ).fetchall()
    return [
        {
            "id": str(r["id"]),
            "name": r["name"],
            "models": r["models"],
            "ai_service_id": str(r["ai_service_id"]) if r["ai_service_id"] else None,
        }
        for r in linhas
    ]


@router.post("/combos", status_code=201)
async def criar_combo(payload: ComboIn, user: dict = Depends(require("ai_router", "create"))):
    """Cria o combo na instância do tenant e o publica como serviço de IA.

    Publicar como `ai_service` é o que faz o combo aparecer no editor de
    template sem que o editor precise saber que 9Router existe.
    """
    registro = _exigir_router(user)

    with get_connection() as conn:
        tenant = conn.execute(
            "SELECT tenant_key FROM tenants WHERE id = %s", (user["tenant_id"],)
        ).fetchone()
    nome_no_router = f"t_{_slug(tenant['tenant_key'])}_{_slug(payload.name)}"

    # Um combo só pode usar modelo que as contas do próprio tenant habilitam.
    try:
        modelos = await router_client.list_models(registro)
        disponiveis = {m["id"] for m in modelos if m.get("owned_by") != "combo"}
    except router_client.RouterError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    fora = [m for m in payload.models if m not in disponiveis]
    if fora:
        raise HTTPException(
            status_code=400,
            detail=f"Modelos não disponíveis nas contas desta empresa: {', '.join(fora)}",
        )

    try:
        await router_client.create_combo(registro, name=nome_no_router, models=payload.models)
    except router_client.RouterError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc

    with get_connection() as conn:
        servico = conn.execute(
            """INSERT INTO ai_services (tenant_id, name, provider, model, api_base,
                                        api_key_encrypted, is_active)
               VALUES (%s, %s, 'openai-compatible', %s, %s, %s, TRUE) RETURNING *""",
            (
                user["tenant_id"],
                f"Combo: {payload.name}",
                nome_no_router,
                f"{registro['base_url'].rstrip('/')}/v1",
                registro["api_key_encrypted"],
            ),
        ).fetchone()
        combo = conn.execute(
            """INSERT INTO tenant_ai_combos
                   (tenant_id, name, router_combo_name, models, ai_service_id)
               VALUES (%s, %s, %s, %s, %s) RETURNING *""",
            (
                user["tenant_id"],
                payload.name,
                nome_no_router,
                Json(payload.models),
                servico["id"],
            ),
        ).fetchone()
    return {
        "id": str(combo["id"]),
        "name": combo["name"],
        "models": combo["models"],
        "ai_service_id": str(servico["id"]),
    }


@router.delete("/combos/{combo_id}")
async def remover_combo(combo_id: str, user: dict = Depends(require("ai_router", "delete"))):
    registro = _exigir_router(user)
    with get_connection() as conn:
        combo = conn.execute(
            "SELECT * FROM tenant_ai_combos WHERE id = %s AND tenant_id = %s",
            (combo_id, user["tenant_id"]),
        ).fetchone()
    if combo is None:
        raise HTTPException(status_code=404, detail="Combo não encontrado")

    try:
        remotos = await router_client.list_combos(registro)
        alvo = next((c for c in remotos if c["name"] == combo["router_combo_name"]), None)
        if alvo:
            await router_client.delete_combo(registro, alvo["id"])
    except router_client.RouterError as exc:
        logger.warning("combo removido da plataforma mas não da instância: %s", exc)

    with get_connection() as conn:
        if combo["ai_service_id"]:
            conn.execute(
                "UPDATE ai_services SET is_deleted = TRUE, is_active = FALSE WHERE id = %s",
                (combo["ai_service_id"],),
            )
        conn.execute("DELETE FROM tenant_ai_combos WHERE id = %s", (combo_id,))
    return {"status": "ok"}


@router.get("/uso")
async def uso(user: dict = Depends(require("ai_router", "view"))):
    """Consumo da empresa. Como a instância é dela, o número já nasce
    filtrado — não há como misturar com o de outro tenant."""
    registro = _exigir_router(user)
    try:
        return await router_client.usage(registro)
    except router_client.RouterError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


class RouterIn(BaseModel):
    tenant_id: str
    base_url: str
    admin_password: str
    api_key: str


@router.put("/instancias", status_code=201)
def registrar_instancia(payload: RouterIn, user: dict = Depends(require("ai_router", "edit"))):
    """Registra a instância provisionada de um tenant. **Só o master.**

    O provisionamento em si roda na VPS (`scripts/provisionar_router.py`);
    aqui a plataforma só guarda onde a instância está e como falar com ela.
    """
    if not user.get("is_master"):
        raise HTTPException(status_code=403, detail="Somente o administrador da plataforma")
    with get_connection() as conn:
        linha = conn.execute(
            """INSERT INTO tenant_routers
                   (tenant_id, base_url, admin_password_encrypted, api_key_encrypted)
               VALUES (%s, %s, %s, %s)
               ON CONFLICT (tenant_id) DO UPDATE
                   SET base_url = EXCLUDED.base_url,
                       admin_password_encrypted = EXCLUDED.admin_password_encrypted,
                       api_key_encrypted = EXCLUDED.api_key_encrypted,
                       is_active = TRUE,
                       updated_at = now()
               RETURNING *""",
            (
                payload.tenant_id,
                payload.base_url,
                encrypt(payload.admin_password),
                encrypt(payload.api_key),
            ),
        ).fetchone()
    return {
        "id": str(linha["id"]),
        "tenant_id": str(linha["tenant_id"]),
        "base_url": linha["base_url"],
    }
