"""Cliente da instância compartilhada de LiteLLM (substitui o 9Router).

Diferença estrutural em relação ao `router_client.py` antigo: o 9Router tinha
**uma instância por tenant** (isolamento físico); o LiteLLM é **uma instância
só, compartilhada por todos os tenants** (Cloud Run), e o isolamento passa a
ser lógico — 1 Team do LiteLLM por tenant, round-robin só acontece dentro do
grupo de deployments daquele Team (`infra-04-litellm-substitui-9router.md`,
seção 2).

Por isso as funções aqui recebem `base_url`/`master_key` (config do serviço,
igual pra todo mundo) e `team_id`/`model_name` (o que muda por tenant) em vez
de um `router` dict com `base_url` próprio — não existe mais URL por tenant.

Toda função ainda segue a mesma regra do cliente antigo: nenhuma rota aceita
`team_id`/`model_name` vindo direto do cliente HTTP sem passar pela resolução
do tenant da sessão primeiro (isso continua sendo responsabilidade da camada
de rotas, não deste módulo).
"""

import logging

import httpx

logger = logging.getLogger(__name__)

TIMEOUT = 30.0


class LiteLLMError(RuntimeError):
    pass


async def _request(
    base_url: str,
    master_key: str,
    method: str,
    path: str,
    *,
    json_body: dict | None = None,
    params: dict | None = None,
) -> dict:
    try:
        async with httpx.AsyncClient(timeout=TIMEOUT) as client:
            response = await client.request(
                method,
                f"{base_url.rstrip('/')}{path}",
                headers={"Authorization": f"Bearer {master_key}"},
                json=json_body,
                params=params,
            )
    except httpx.HTTPError as exc:
        raise LiteLLMError(f"LiteLLM inacessível: {exc}") from exc
    if response.status_code >= 400:
        raise LiteLLMError(f"LiteLLM respondeu {response.status_code}: {response.text[:300]}")
    if not response.content:
        return {}
    try:
        return response.json()
    except ValueError:
        return {}


async def health(base_url: str) -> bool:
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.get(f"{base_url.rstrip('/')}/health/liveliness")
        return response.status_code == 200
    except httpx.HTTPError:
        return False


# --------------------------------------------------------------------------
# Team (equivalente a "a instância do tenant" no modelo antigo — aqui é um
# registro lógico dentro da instância compartilhada, não infra nova)
# --------------------------------------------------------------------------


async def create_team(base_url: str, master_key: str, *, team_alias: str) -> dict:
    """Cria o Team do tenant. Não recebe `models` aqui de propósito — a lista
    de model_names permitidos cresce conforme deployments são criados
    (`add_model_to_team`), pra não ter que saber os nomes de antemão."""
    return await _request(
        base_url, master_key, "POST", "/team/new", json_body={"team_alias": team_alias}
    )


async def add_model_to_team(
    base_url: str, master_key: str, *, team_id: str, model_name: str
) -> dict:
    """Autoriza o Team a enxergar mais um `model_name` (ex.: um combo novo).

    Idempotente na prática: se `model_name` já estiver na lista, o LiteLLM
    aceita de novo sem duplicar — não precisa checar antes de chamar."""
    team = await _request(base_url, master_key, "GET", "/team/info", params={"team_id": team_id})
    existentes = (team.get("team_info") or team).get("models") or []
    if model_name in existentes:
        return team
    return await _request(
        base_url,
        master_key,
        "POST",
        "/team/update",
        json_body={"team_id": team_id, "models": [*existentes, model_name]},
    )


async def delete_team(base_url: str, master_key: str, *, team_id: str) -> None:
    await _request(base_url, master_key, "POST", "/team/delete", json_body={"team_ids": [team_id]})


# --------------------------------------------------------------------------
# Deployments (equivalente às "contas de provedor" — cada BYOK key do tenant
# vira 1 deployment dentro do `model_name` daquele combo)
# --------------------------------------------------------------------------


async def create_deployment(
    base_url: str,
    master_key: str,
    *,
    model_name: str,
    provider_model: str,
    api_key: str,
    api_base: str | None = None,
    tenant_id: str,
    extra_headers: dict | None = None,
    custom_llm_provider: str | None = None,
    model_info_extra: dict | None = None,
    num_retries: int | None = None,
) -> dict:
    """`provider_model` é o identificador que o LiteLLM entende (ex.
    `gemini/gemini-flash-latest`, `openai/gpt-4o`) — quem decide isso é a
    camada de rotas, com base no provider escolhido na tela, igual o
    `router_client.create_api_key_connection` recebia `provider` solto.
    Pra assinatura Claude (`anthropic/...`), o LiteLLM já detecta sozinho
    que `api_key` é um token OAuth (prefixo `sk-ant-oat`) e troca
    `x-api-key` por `Authorization: Bearer` automaticamente (confirmado
    lendo `litellm/llms/anthropic/common_utils.py#get_anthropic_headers`)
    -- não mexer nisso, `api_key` continua sendo o token real puro.

    `extra_headers` (26-27/08/2026, achado ao vivo real): a detecção
    automática do LiteLLM seta Authorization/anthropic-beta/
    anthropic-dangerous-direct-browser-access, mas NÃO manda
    `User-Agent`/`X-Stainless-*` -- headers que fazem a chamada parecer
    o Claude Code CLI de verdade. Sem eles, autenticação passa mas a
    Anthropic aplica um rate limit mais agressivo em tráfego que não
    parece vir do cliente oficial (confirmado: dono usa o Claude normal
    sem rate limit nenhum, só a chamada via LiteLLM sem esses headers
    tomava 429). São ADITIVOS aos headers automáticos do LiteLLM, não
    substituem o Authorization -- nunca colocar `Authorization` aqui de
    novo (foi tentado antes, conflitava com o x-api-key que o LiteLLM
    também manda e quebrava a autenticação).

    `model_info.tenant_id` fica salvo pra permitir filtrar/auditar depois
    quais deployments são de qual tenant sem depender só do agrupamento por
    `model_name` (mesmo padrão de auditoria já usado no sandbox de teste desta
    mega-spec, `infra-04`, Fase A)."""
    litellm_params = {"model": provider_model, "api_key": api_key}
    if api_base:
        litellm_params["api_base"] = api_base
    if extra_headers:
        litellm_params["extra_headers"] = extra_headers
    if custom_llm_provider:
        litellm_params["custom_llm_provider"] = custom_llm_provider
    if num_retries is not None:
        litellm_params["num_retries"] = num_retries
    model_info = {"tenant_id": tenant_id}
    if model_info_extra:
        model_info.update(model_info_extra)
    try:
        return await _request(
            base_url,
            master_key,
            "POST",
            "/model/new",
            json_body={
                "model_name": model_name,
                "litellm_params": litellm_params,
                "model_info": model_info,
            },
        )
    except LiteLLMError as exc:
        # 26/08/2026, achado ao vivo: LiteLLM devolve 500 aqui MESMO QUANDO
        # a escrita deu certo -- a mensagem eh literalmente "Model create
        # was saved to the database, but the model id(s) [...] are not
        # live in this pod's router after the reload". Confirmado na doc
        # deles (Model Management): convergencia entre pods eh por polling
        # (~30s por padrao, sem push), entao checar "esta vivo AGORA" logo
        # apos o POST sempre perde essa corrida, mesmo com 1 instancia so
        # (o proprio pod que recebeu o write ainda nao rodou o proximo
        # ciclo de polling). Nao eh erro de verdade -- trata como sucesso
        # (o deployment aparece em poucos segundos), so um erro de
        # transporte real (rede, 4xx de payload invalido) continua
        # propagando.
        msg = str(exc)
        if "was saved to the database" in msg and "not live in this pod" in msg:
            return {
                "model_name": model_name,
                "aviso": "escrito no banco, aguardando proximo ciclo de polling do LiteLLM",
            }
        raise


async def list_deployments(base_url: str, master_key: str, *, model_name: str) -> list[dict]:
    body = await _request(base_url, master_key, "GET", "/model/info")
    return [m for m in body.get("data") or [] if m.get("model_name") == model_name]


async def delete_deployment(base_url: str, master_key: str, *, deployment_id: str) -> None:
    await _request(base_url, master_key, "POST", "/model/delete", json_body={"id": deployment_id})


# --------------------------------------------------------------------------
# Virtual keys (2 por tenant — bridge/kernel e AI Assist, nunca a mesma,
# decisão fechada em infra-04 seção 2c: vazamento de uma não compromete a
# outra, cada uma guardada num banco diferente)
# --------------------------------------------------------------------------


async def generate_key(
    base_url: str, master_key: str, *, team_id: str, key_alias: str, model_name: str | None = None
) -> str:
    """Devolve só a key em si (string) — quem chama decide onde/como
    encriptar e guardar, igual já era com `api_key_encrypted` no modelo
    antigo.

    `model_name` é opcional de propósito: no provisionamento (`registrar
    a instância`), o tenant ainda não conectou nenhuma conta própria — o Team
    nasce sem `models` (seção "Team", acima) e a key tem que existir mesmo
    assim, herdando o que o Team for ganhando depois (`add_model_to_team`),
    sem precisar regenerar a key toda vez que um deployment novo é criado.
    Só passar `model_name` quando quiser restringir a key a um subconjunto
    específico do que o Team já tem acesso."""
    body_json = {"team_id": team_id, "key_alias": key_alias}
    if model_name is not None:
        body_json["models"] = [model_name]
    body = await _request(base_url, master_key, "POST", "/key/generate", json_body=body_json)
    key = body.get("key")
    if not key:
        raise LiteLLMError("LiteLLM não devolveu a virtual key gerada")
    return key


async def delete_key(base_url: str, master_key: str, *, key: str) -> None:
    await _request(base_url, master_key, "POST", "/key/delete", json_body={"keys": [key]})
