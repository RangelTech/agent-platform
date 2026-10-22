"""Provisionamento do tenant no Gateway de IA (chaves internas), disparado
pela projecao Chatwoot -> motor (`/internal/ui/provision`)."""

import asyncio
import logging

from app import gateway_client
from app.config import settings
from app.crypto import encrypt
from app.db import get_connection
from app.installation_secrets import resolver as resolver_segredo

logger = logging.getLogger(__name__)


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
