"""Cliente restrito do gateway multi-tenant de modelos.

Este módulo só usa o token de serviço e sempre envia o tenant resolvido no
motor. Ele nunca recebe ``tenant_id`` de uma requisição do navegador.
"""

import httpx

TIMEOUT = 30.0


class GatewayError(RuntimeError):
    """Erro seguro para apresentar ao operador sem expor segredo."""


async def _request(
    base_url: str,
    admin_token: str,
    tenant_id: str,
    method: str,
    path: str,
    *,
    json_body: dict | None = None,
) -> dict:
    if not base_url or not admin_token:
        raise GatewayError("Gateway de IA não configurado nesta instalação")
    try:
        async with httpx.AsyncClient(timeout=TIMEOUT) as client:
            response = await client.request(
                method,
                f"{base_url.rstrip('/')}{path}",
                headers={"x-admin-token": admin_token, "x-tenant-id": tenant_id},
                json=json_body,
            )
    except httpx.HTTPError as exc:
        raise GatewayError("Gateway de IA inacessível") from exc
    if response.status_code >= 400:
        detail = response.text[:300].replace("\n", " ")
        raise GatewayError(f"Gateway de IA respondeu {response.status_code}: {detail}")
    if not response.content:
        return {}
    try:
        return response.json()
    except ValueError:
        return {}


async def provision_tenant(
    base_url: str,
    admin_token: str,
    *,
    tenant_id: str,
    name: str,
) -> tuple[str, str]:
    """Cria (de forma idempotente) o tenant e as duas chaves internas.

    As chaves começam sem allow-list porque o tenant ainda não possui combos;
    a Fase G-8 as restringe no momento em que cada combo é publicado.
    """
    await _request(
        base_url,
        admin_token,
        tenant_id,
        "POST",
        "/api/tenants",
        json_body={"id": tenant_id, "name": name},
    )
    bridge = await _request(
        base_url,
        admin_token,
        tenant_id,
        "POST",
        "/api/keys",
        json_body={"name": f"{tenant_id}-bridge"},
    )
    assist = await _request(
        base_url,
        admin_token,
        tenant_id,
        "POST",
        "/api/keys",
        json_body={"name": f"{tenant_id}-ai-assist"},
    )
    if not bridge.get("key") or not assist.get("key"):
        raise GatewayError("Gateway não devolveu as chaves internas do tenant")
    return bridge["key"], assist["key"]


async def set_tenant_status(
    base_url: str,
    admin_token: str,
    *,
    tenant_id: str,
    active: bool,
) -> None:
    await _request(
        base_url,
        admin_token,
        tenant_id,
        "PATCH",
        f"/api/tenants/{tenant_id}",
        json_body={"status": "active" if active else "suspended"},
    )


def _sync(base_url, admin_token, tenant_id, method, path, body=None):
    if not base_url or not admin_token:
        raise GatewayError("Gateway de IA não configurado nesta instalação")
    try:
        with httpx.Client(timeout=TIMEOUT) as client:
            response = client.request(
                method,
                f"{base_url.rstrip('/')}{path}",
                headers={"x-admin-token": admin_token, "x-tenant-id": tenant_id},
                json=body,
            )
    except httpx.HTTPError as exc:
        raise GatewayError("Gateway de IA inacessível") from exc
    if response.status_code >= 400:
        raise GatewayError(
            f"Gateway de IA respondeu {response.status_code}: {response.text[:300]}"
        )
    try:
        return response.json() if response.content else {}
    except ValueError:
        return {}


def combo_names(base_url: str, admin_token: str, tenant_id: str) -> set[str]:
    data = _sync(base_url, admin_token, tenant_id, "GET", "/api/combos")
    return {c.get("name") for c in data.get("combos", []) if c.get("name")}


def create_combo_key(
    base_url: str, admin_token: str, tenant_id: str, *, combo: str
) -> tuple[str, str | None]:
    """Chave do tenant restrita a um único combo. Devolve (chave, id)."""
    data = _sync(
        base_url,
        admin_token,
        tenant_id,
        "POST",
        "/api/keys",
        {"name": f"run-{combo}"[:60], "allowedCombos": [combo]},
    )
    if not data.get("key"):
        raise GatewayError("Gateway não devolveu a chave do combo")
    return data["key"], data.get("id")
