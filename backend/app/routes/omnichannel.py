"""Camada omnichannel: caixas de entrada e status (Chatwoot).

O agent-platform continua sendo o cadastro principal. Estas rotas apenas
delegam para a ponte (`chatwoot-rt`), que fala com o Chatwoot — o backend
nunca guarda token de Platform API nem cria conta lá diretamente.
"""

import logging
import uuid

import httpx
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from app.auth import require
from app.config import settings
from app.db import get_connection
from app.tenancy import resolve_target_tenant

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/omnichannel", tags=["omnichannel"])

TIMEOUT = 60.0


def _bridge_headers() -> dict:
    if not settings.bridge_url or not settings.bridge_admin_token:
        raise HTTPException(
            status_code=503, detail="Camada omnichannel não configurada nesta instalação"
        )
    return {"Authorization": f"Bearer {settings.bridge_admin_token}"}


async def _bridge(method: str, path: str, **kwargs):
    headers = _bridge_headers()
    try:
        async with httpx.AsyncClient(timeout=TIMEOUT) as client:
            response = await client.request(
                method, f"{settings.bridge_url.rstrip('/')}{path}", headers=headers, **kwargs
            )
    except httpx.HTTPError as exc:
        raise HTTPException(status_code=502, detail=f"ponte inacessível: {exc}") from exc
    if response.status_code >= 400:
        raise HTTPException(
            status_code=response.status_code, detail=f"ponte: {response.text[:300]}"
        )
    return response.json() if response.content else {}


@router.get("/status")
def status(user: dict = Depends(require("omnichannel", "view"))):
    """Se a empresa já tem conta no Chatwoot, e se a camada está ligada."""
    configured = bool(settings.bridge_url and settings.bridge_admin_token)
    account_id = None
    if user["tenant_id"]:
        with get_connection() as conn:
            row = conn.execute(
                "SELECT chatwoot_account_id FROM tenants WHERE id = %s", (user["tenant_id"],)
            ).fetchone()
        account_id = row["chatwoot_account_id"] if row else None
    return {"configured": configured, "chatwoot_account_id": account_id}


class CaixaIaIn(BaseModel):
    """Qual agente atende esta caixa."""

    template_id: str | None = None
    autopilot: bool = True
    handoff_team_id: int | None = None
    # Canal externo não tem stream: com True a ponte manda UMA mensagem curta de progresso antes
    # da resposta; com False (padrão) entrega só a resposta final. None = não mexe no que já está.
    show_progress: bool | None = None
    tenant_id: str | None = None  # master only


@router.get("/inboxes")
async def list_inboxes(user: dict = Depends(require("omnichannel", "view"))):
    """Caixas de atendimento da empresa e o template que atende cada uma.

    Uma empresa tem várias caixas — WhatsApp, Instagram, site — e cada uma pode
    ser atendida por um agente diferente do mesmo tenant. O vínculo por caixa já
    existia na ponte; sem esta rota só dava para configurá-lo chamando a API na
    mão, o que na prática significava que ninguém configurava.
    """
    tenant_id = resolve_target_tenant(user, None)
    dados = await _bridge("GET", f"/admin/ai-config/{tenant_id}")

    # O nome do template vem do nosso banco: a ponte guarda só o id, e uma tela
    # que mostra UUID não ajuda ninguém a escolher.
    with get_connection() as conn:
        nomes = {
            str(r["id"]): r["name"]
            for r in conn.execute(
                "SELECT id, name FROM templates WHERE tenant_id = %s AND NOT is_deleted",
                (tenant_id,),
            ).fetchall()
        }
        locais = {
            r["chatwoot_inbox_id"]: r
            for r in conn.execute(
                "SELECT * FROM inbox_ai_links WHERE tenant_id = %s", (tenant_id,)
            ).fetchall()
        }
    for caixa in dados.get("inboxes", []):
        local = locais.get(caixa.get("chatwoot_inbox_id"))
        ia = caixa.get("ai")
        if local is not None:
            # O que foi salvo aqui vale sobre o que a ponte guarda.
            ia = {
                **(ia or {}),
                "template_id": str(local["template_id"]) if local["template_id"] else None,
                "autopilot": local["autopilot"],
                "handoff_team_id": local["handoff_team_id"],
                "show_progress": bool(local["show_progress"]),
                "degraded": not local["bridge_synced"],
                "degraded_reason": local["bridge_error"],
            }
            caixa["ai"] = ia
        if ia:
            ia["template_name"] = nomes.get(ia.get("template_id") or "", "")
    return dados


@router.put("/inboxes/{inbox_id}/ia")
async def set_inbox_ai(
    inbox_id: int,
    payload: CaixaIaIn,
    user: dict = Depends(require("omnichannel", "edit")),
):
    """Liga (ou desliga) um template nesta caixa.

    O vínculo é gravado aqui, no motor, e é isso que a tela mostra. A ponte ainda roteia as
    mensagens pela configuração dela, então o motor a avisa depois de gravar. Falha da
    ponte não derruba o salvamento (antes virava 502 mesmo com `template_id` nulo): o
    vínculo fica salvo e a resposta marca `status: "degraded"` com o motivo, para a tela
    avisar e para uma nova tentativa refazer só o aviso.
    """
    tenant_id = resolve_target_tenant(user, payload.tenant_id)

    # O template precisa ser do próprio tenant. Sem esta checagem, um id de
    # outra empresa colado no request faria a IA de um cliente atender no canal
    # de outro — o pior vazamento possível nesta camada.
    if payload.template_id:
        try:
            uuid.UUID(str(payload.template_id))
        except ValueError:
            raise HTTPException(status_code=400, detail="Template inválido") from None
        with get_connection() as conn:
            existe = conn.execute(
                """SELECT 1 FROM templates
                    WHERE id = %s AND tenant_id = %s AND NOT is_deleted""",
                (payload.template_id, tenant_id),
            ).fetchone()
        if existe is None:
            raise HTTPException(status_code=404, detail="Template não é desta empresa")

    with get_connection() as conn:
        saved = conn.execute(
            """INSERT INTO inbox_ai_links (tenant_id, chatwoot_inbox_id, template_id, autopilot,
                                           handoff_team_id, show_progress, bridge_synced,
                                           bridge_error)
               VALUES (%s, %s, %s, %s, %s, COALESCE(%s, FALSE), FALSE, NULL)
               ON CONFLICT (tenant_id, chatwoot_inbox_id) DO UPDATE
                   SET template_id = EXCLUDED.template_id, autopilot = EXCLUDED.autopilot,
                       handoff_team_id = EXCLUDED.handoff_team_id,
                       show_progress = COALESCE(%s, inbox_ai_links.show_progress),
                       bridge_synced = FALSE, bridge_error = NULL, updated_at = now()
               RETURNING show_progress""",
            (
                tenant_id,
                inbox_id,
                payload.template_id,
                payload.autopilot,
                payload.handoff_team_id,
                payload.show_progress,
                payload.show_progress,
            ),
        ).fetchone()
        show_progress = bool(saved["show_progress"])

    sync_error = None
    bridge_out: dict = {}
    try:
        bridge_out = await _bridge(
            "POST",
            "/admin/ai-config",
            json={
                "tenant_id": str(tenant_id),
                "chatwoot_inbox_id": inbox_id,
                "template_id": payload.template_id,
                "autopilot": payload.autopilot,
                "handoff_team_id": payload.handoff_team_id,
                "show_progress": show_progress,
            },
        )
    except HTTPException as exc:
        sync_error = str(exc.detail)[:300]
    except Exception as exc:  # noqa: BLE001 - resposta fora do formato, ponte caindo no meio
        sync_error = f"{type(exc).__name__}: {exc}"[:300]
    else:
        if bridge_out.get("degraded"):  # gravou, mas não conseguiu ligar o bot na caixa
            sync_error = str(bridge_out.get("agent_bot") or "bot não associado")[:300]

    with get_connection() as conn:
        conn.execute(
            """UPDATE inbox_ai_links SET bridge_synced = %s, bridge_error = %s
                WHERE tenant_id = %s AND chatwoot_inbox_id = %s""",
            (sync_error is None, sync_error, tenant_id, inbox_id),
        )
    if sync_error:
        logger.warning("ponte não aceitou o vínculo da caixa %s: %s", inbox_id, sync_error)
        return {
            "status": "degraded",
            "saved": True,
            "chatwoot_inbox_id": inbox_id,
            "template_id": payload.template_id,
            "autopilot": payload.autopilot,
            "show_progress": show_progress,
            "detail": "Vínculo salvo, mas o atendimento automático ainda não foi atualizado "
            "nesta caixa. Salve de novo em instantes.",
        }
    return {
        **bridge_out,
        "status": "ok",
        "saved": True,
        "chatwoot_inbox_id": inbox_id,
        "template_id": payload.template_id,
        "autopilot": payload.autopilot,
        "show_progress": show_progress,
    }
