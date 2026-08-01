"""Cliente da instância de 9Router de um tenant.

Cada empresa tem a **sua** instância. Isso não é preferência de deploy: o
9Router escolhe qual conta atende uma chamada por `(provider, priority)`, sem
nenhuma noção de dono, então duas empresas na mesma instância acabariam
usando a conta uma da outra — e nenhuma regra do nosso lado impediria, porque
a escolha acontece lá dentro.

Toda função aqui recebe o registro do router **já resolvido pelo tenant da
sessão**. Nenhuma rota aceita URL ou id de instância vindo do cliente.
"""

import logging

import httpx

logger = logging.getLogger(__name__)

TIMEOUT = 30.0


class RouterError(RuntimeError):
    pass


async def _login(base_url: str, password: str) -> str:
    """Sessão administrativa. O 9Router bloqueia após poucas tentativas, então
    a senha errada falha rápido e explícito em vez de repetir."""
    try:
        async with httpx.AsyncClient(timeout=TIMEOUT) as client:
            response = await client.post(
                f"{base_url.rstrip('/')}/api/auth/login", json={"password": password}
            )
    except httpx.HTTPError as exc:
        raise RouterError(f"instância de IA inacessível: {exc}") from exc
    if response.status_code != 200:
        raise RouterError(f"login na instância recusado: {response.text[:200]}")
    token = response.cookies.get("auth_token")
    if not token:
        raise RouterError("instância não devolveu sessão administrativa")
    return token


async def _request(
    router: dict,
    method: str,
    path: str,
    *,
    json_body: dict | None = None,
) -> dict:
    from app.crypto import decrypt

    base_url = router["base_url"]
    token = await _login(base_url, decrypt(router["admin_password_encrypted"]))
    try:
        async with httpx.AsyncClient(timeout=TIMEOUT) as client:
            response = await client.request(
                method,
                f"{base_url.rstrip('/')}{path}",
                headers={"Cookie": f"auth_token={token}"},
                json=json_body,
            )
    except httpx.HTTPError as exc:
        raise RouterError(f"instância de IA inacessível: {exc}") from exc
    if response.status_code >= 400:
        raise RouterError(f"instância respondeu {response.status_code}: {response.text[:300]}")
    if not response.content:
        return {}
    try:
        return response.json()
    except ValueError:
        return {}


async def health(router: dict) -> bool:
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.get(f"{router['base_url'].rstrip('/')}/api/health")
        return response.status_code == 200
    except httpx.HTTPError:
        return False


# --------------------------------------------------------------------------
# Contas de provedor (o que o cliente chama de "minhas contas de IA")
# --------------------------------------------------------------------------


async def list_connections(router: dict) -> list[dict]:
    body = await _request(router, "GET", "/api/providers")
    return body.get("connections") or []


async def create_api_key_connection(
    router: dict, *, provider: str, api_key: str, label: str
) -> dict:
    body = await _request(
        router,
        "POST",
        "/api/providers",
        json_body={"provider": provider, "apiKey": api_key, "name": label},
    )
    return body.get("connection") or body


async def delete_connection(router: dict, connection_id: str) -> None:
    await _request(router, "DELETE", f"/api/providers/{connection_id}")


# --------------------------------------------------------------------------
# Combos (o revezamento entre as contas do próprio tenant)
# --------------------------------------------------------------------------


async def list_combos(router: dict) -> list[dict]:
    body = await _request(router, "GET", "/api/combos")
    return body.get("combos") or []


async def create_combo(router: dict, *, name: str, models: list[str]) -> dict:
    return await _request(
        router, "POST", "/api/combos", json_body={"name": name, "models": models}
    )


async def delete_combo(router: dict, combo_id: str) -> None:
    await _request(router, "DELETE", f"/api/combos/{combo_id}")


async def list_models(router: dict) -> list[dict]:
    """Modelos que as contas do tenant habilitam — a lista que a UI oferece
    na hora de montar um combo."""
    from app.crypto import decrypt

    try:
        async with httpx.AsyncClient(timeout=TIMEOUT) as client:
            response = await client.get(
                f"{router['base_url'].rstrip('/')}/v1/models",
                headers={"Authorization": f"Bearer {decrypt(router['api_key_encrypted'])}"},
            )
    except httpx.HTTPError as exc:
        raise RouterError(f"instância de IA inacessível: {exc}") from exc
    if response.status_code >= 400:
        raise RouterError(f"instância respondeu {response.status_code}")
    return (response.json() or {}).get("data") or []


async def usage(router: dict) -> dict:
    """Consumo da instância. Como ela é de um tenant só, o número já vem
    filtrado por tenant — o painel nativo do 9Router não faria isso."""
    return await _request(router, "GET", "/api/usage")
