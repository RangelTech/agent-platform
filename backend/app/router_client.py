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
    params: dict | None = None,
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
                params=params,
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


async def update_connection(router: dict, connection_id: str, **campos) -> dict:
    """Prioridade e liga/desliga de uma conta.

    A prioridade é o que decide a ordem do revezamento entre contas do mesmo
    provedor — é por ela que a conta 2 assume quando a 1 bate no limite.
    """
    return await _request(
        router, "PUT", f"/api/providers/{connection_id}", json_body=campos
    )


# --------------------------------------------------------------------------
# Contas por assinatura (OAuth)
# --------------------------------------------------------------------------
#
# São dois fluxos, e a diferença importa para a tela:
#
#   redirect — `authorize` devolve uma URL. O cliente autoriza no provedor e
#              volta com um código. Quem guarda `codeVerifier`/`state` entre as
#              duas etapas é a tela, e devolve os dois no `exchange`.
#   device   — `authorize` devolve um código curto e uma URL. O cliente digita
#              o código lá, e a instância vai sendo consultada (`poll`) até ele
#              confirmar.


async def oauth_authorize(
    router: dict, provider: str, *, redirect_uri: str | None = None
) -> dict:
    """Fluxo redirect: devolve a URL para o cliente autorizar."""
    params = {"redirect_uri": redirect_uri} if redirect_uri else None
    return await _request(
        router, "GET", f"/api/oauth/{provider}/authorize", params=params
    )


async def oauth_device_code(router: dict, provider: str) -> dict:
    """Fluxo device: devolve o código curto e onde digitá-lo.

    É uma ação separada de `authorize` — chamar `authorize` num provedor de
    device devolve só material PKCE, sem código nenhum, e a tela ficaria
    mostrando um campo vazio.
    """
    return await _request(router, "GET", f"/api/oauth/{provider}/device-code")


async def oauth_exchange(
    router: dict,
    provider: str,
    *,
    code: str,
    redirect_uri: str,
    code_verifier: str | None,
    state: str | None = None,
) -> dict:
    return await _request(
        router,
        "POST",
        f"/api/oauth/{provider}/exchange",
        json_body={
            "code": code,
            "redirectUri": redirect_uri,
            "codeVerifier": code_verifier,
            "state": state,
        },
    )


async def oauth_poll(
    router: dict, provider: str, *, device_code: str, code_verifier: str | None
) -> dict:
    """Enquanto o cliente não confirma, devolve `pending`. Não é erro."""
    return await _request(
        router,
        "POST",
        f"/api/oauth/{provider}/poll",
        json_body={"deviceCode": device_code, "codeVerifier": code_verifier},
    )


# --------------------------------------------------------------------------
# Combos (o revezamento entre as contas do próprio tenant)
# --------------------------------------------------------------------------


async def list_combos(router: dict) -> list[dict]:
    body = await _request(router, "GET", "/api/combos")
    return body.get("combos") or []


async def create_combo(router: dict, *, name: str, models: list[str]) -> dict:
    """O nome só aceita letras, números, `-`, `_` e `.` — sem espaço e sem
    acento. Quem monta o nome interno é `ai_router._slug`.

    A instância **não valida os modelos**: um combo com modelo inexistente é
    aceito com 201 e só falha na hora de responder. Por isso a validação de
    verdade acontece do nosso lado, antes desta chamada."""
    return await _request(
        router, "POST", "/api/combos", json_body={"name": name, "models": models}
    )


async def update_combo(router: dict, combo_id: str, *, models: list[str]) -> dict:
    return await _request(
        router, "PUT", f"/api/combos/{combo_id}", json_body={"models": models}
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


async def available_models(router: dict) -> list[dict]:
    """Modelos que as contas conectadas do tenant realmente liberam.

    `/v1/models` devolve o catálogo inteiro da imagem (752 modelos), conectado
    ou não — usar aquilo como "disponível" deixaria o cliente montar um combo
    que nunca responde. Aqui o catálogo é cruzado com as contas que existem na
    instância, pelo prefixo do modelo.
    """
    from app.router_catalog import provedor_de_modelo

    conexoes = await list_connections(router)
    conectados = {c.get("provider") for c in conexoes if c.get("isActive")}
    return [
        m
        for m in await list_models(router)
        if provedor_de_modelo(m["id"]) in conectados and m.get("owned_by") != "combo"
    ]


async def settings(router: dict) -> dict:
    return await _request(router, "GET", "/api/settings")


async def update_settings(router: dict, campos: dict) -> dict:
    return await _request(router, "PATCH", "/api/settings", json_body=campos)


async def usage(router: dict) -> dict:
    """Consumo da instância. Como ela é de um tenant só, o número já vem
    filtrado por tenant — o painel nativo do 9Router não faria isso.

    O caminho é `/api/usage/stats`; `/api/usage` sozinho é 404.
    """
    return await _request(router, "GET", "/api/usage/stats")
