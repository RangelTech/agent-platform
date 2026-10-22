"""Chave de gateway por run (Fase G-9).

Resolve o tenant do gateway (tenant próprio, ou `platform` quando o tenant não
tem o combo/alias), obtém ou cria a chave restrita àquele combo e devolve o
bloco `model.gateway` do contrato kernel<->motor. A identidade vem do banco do
motor, nunca do browser. Sem flag ou sem gateway configurado, devolve None e
o kernel cai no caminho antigo (env).
"""

from __future__ import annotations

import logging

from app import gateway_client
from app.config import settings
from app.crypto import decrypt, encrypt
from app.installation_secrets import resolver as resolver_segredo

logger = logging.getLogger(__name__)

PLATFORM_TENANT = "platform"


def _own_gateway_tenant(conn, tenant_id) -> str | None:
    row = conn.execute(
        "SELECT gateway_tenant_id FROM tenant_routers WHERE tenant_id = %s AND is_active",
        (tenant_id,),
    ).fetchone()
    return (row or {}).get("gateway_tenant_id")


def _stored_key(conn, gw_tenant: str, combo: str) -> str | None:
    row = conn.execute(
        "SELECT key_encrypted FROM gateway_run_keys WHERE gateway_tenant_id = %s AND combo = %s",
        (gw_tenant, combo),
    ).fetchone()
    return decrypt(row["key_encrypted"]) if row else None


def run_gateway(conn, tenant_id, combo: str) -> dict | None:
    if not settings.gateway_runs_enabled or not combo:
        return None
    try:
        base = resolver_segredo("GATEWAY_BASE_URL")
        token = resolver_segredo("GATEWAY_ADMIN_TOKEN")
        # tenant do gateway = o proprio tenant do motor (mesmo id do G-2); platform e o fallback
        own = _own_gateway_tenant(conn, tenant_id) or str(tenant_id)
        candidates = [t for t in (own, PLATFORM_TENANT) if t]
        for gw_tenant in dict.fromkeys(candidates):
            key = _stored_key(conn, gw_tenant, combo)
            if key is None:
                if combo not in gateway_client.combo_names(base, token, gw_tenant):
                    continue
                key, key_id = gateway_client.create_combo_key(base, token, gw_tenant, combo=combo)
                conn.execute(
                    """INSERT INTO gateway_run_keys
                           (gateway_tenant_id, combo, gateway_key_id, key_encrypted)
                       VALUES (%s, %s, %s, %s)
                       ON CONFLICT (gateway_tenant_id, combo) DO UPDATE
                           SET gateway_key_id = EXCLUDED.gateway_key_id,
                               key_encrypted = EXCLUDED.key_encrypted""",
                    (gw_tenant, combo, key_id, encrypt(key)),
                )
            return {"base_url": settings.gateway_run_base_url, "api_key": key}
    except Exception:  # noqa: BLE001 - nunca derruba a run; o kernel usa o caminho antigo
        logger.exception("gateway por run indisponível para o combo %s", combo)
        return None
    logger.warning("combo %s não existe no gateway (tenant %s nem platform)", combo, tenant_id)
    return None
