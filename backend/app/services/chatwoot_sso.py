"""Sessão sincronizada RAgentes<->RAtende (produto-05 seção 6c).

Best-effort de propósito: login/logout no RAgentes nunca pode falhar por
causa do RAtende estar fora do ar, ou por um usuário master (sem tenant, sem
conta no Chatwoot) tentar entrar. Qualquer problema aqui vira log, nunca
exceção pro chamador.
"""

import logging

import httpx

from app.config import settings

logger = logging.getLogger(__name__)

TIMEOUT = 10.0


def _configured() -> bool:
    return bool(settings.bridge_url and settings.bridge_admin_token)


async def login_url(tenant_id: str, user_id: str) -> str | None:
    """Link de SSO pra abrir em iframe oculto e já deixar a sessão do
    RAtende estampada no mesmo login do RAgentes."""
    if not _configured():
        return None
    try:
        async with httpx.AsyncClient(timeout=TIMEOUT) as client:
            response = await client.get(
                f"{settings.bridge_url.rstrip('/')}/admin/sso/{tenant_id}/{user_id}",
                headers={"Authorization": f"Bearer {settings.bridge_admin_token}"},
            )
        if response.status_code != 200:
            return None
        return response.json().get("url")
    except httpx.HTTPError as exc:
        logger.warning("sso login_url falhou (tenant=%s user=%s): %s", tenant_id, user_id, exc)
        return None


async def logout(tenant_id: str, user_id: str) -> None:
    """Derruba a sessão do RAtende junto do logout do RAgentes."""
    if not _configured():
        return
    try:
        async with httpx.AsyncClient(timeout=TIMEOUT) as client:
            await client.post(
                f"{settings.bridge_url.rstrip('/')}/admin/logout/{tenant_id}/{user_id}",
                headers={"Authorization": f"Bearer {settings.bridge_admin_token}"},
            )
    except httpx.HTTPError as exc:
        logger.warning("sso logout falhou (tenant=%s user=%s): %s", tenant_id, user_id, exc)
