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
from datetime import UTC

import httpx
from fastapi import APIRouter, Depends, HTTPException
from psycopg.types.json import Json
from pydantic import BaseModel, Field

from app import litellm_client, oauth_engine, router_catalog, router_client
from app.auth import require
from app.config import settings
from app.crypto import decrypt, encrypt
from app.db import get_connection
from app.installation_secrets import resolver as resolver_segredo

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


def _modo_litellm(registro: dict) -> bool:
    """Tenant no LiteLLM (infra-04) não tem instância própria de 9Router —
    `tenant_routers` distingue os dois modos pela coluna que veio preenchida
    (migration 0028, CHECK garante que é sempre um dos dois, nunca nenhum)."""
    return registro.get("litellm_team_id") is not None


def _config_litellm() -> tuple[str, str]:
    base_url = resolver_segredo("LITELLM_BASE_URL")
    master_key = resolver_segredo("LITELLM_MASTER_KEY")
    if not base_url or not master_key:
        raise HTTPException(status_code=500, detail="LiteLLM não configurado nesta instalação")
    return base_url, master_key


@router.get("/status")
def status(user: dict = Depends(require("ai_router", "view"))):
    """Se a empresa já tem instância dedicada e se ela está de pé.

    `provisionamento` reflete o que `create_tenant` disparou em background
    (infra-06): pending/provisioning/ready/failed. Um tenant pode estar
    'ready' aqui e ainda `registro is None` por um instante — o script marca
    o status só depois de registrar a instância — mas nunca o contrário.
    """
    if not user["tenant_id"]:
        return {"provisionado": False, "motivo": "master"}
    with get_connection() as conn:
        registro = _router_do_tenant(conn, user["tenant_id"])
        tenant = conn.execute(
            """SELECT router_provisioning_status, router_provisioning_error
                 FROM tenants WHERE id = %s""",
            (user["tenant_id"],),
        ).fetchone()
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
        "provisionamento": (tenant or {}).get("router_provisioning_status", "pending"),
        "provisionamento_erro": (tenant or {}).get("router_provisioning_error"),
        "contas": contas,
        "combos": combos,
    }


@router.get("/contas")
async def listar_contas(user: dict = Depends(require("ai_router", "view"))):
    registro = _exigir_router(user)
    with get_connection() as conn:
        linhas = conn.execute(
            """SELECT * FROM tenant_ai_accounts
                WHERE tenant_id = %s AND is_active ORDER BY created_at""",
            (user["tenant_id"],),
        ).fetchall()

    if _modo_litellm(registro):
        # Sem instância própria não há "conexão remota" pra checar saúde —
        # a conta só existe cifrada aqui até um combo criar um deployment
        # de verdade com ela (infra-04 seção 2d).
        return [
            {
                "id": str(r["id"]),
                "provider": r["provider"],
                "provider_nome": (router_catalog.POR_ID.get(r["provider"], {})).get("nome")
                or r["provider"],
                "auth_type": r["auth_type"],
                "label": r["label"],
                "conectada": True,
                "situacao": "armazenada",
            }
            for r in linhas
        ]

    try:
        conexoes = {c["id"]: c for c in await router_client.list_connections(registro)}
    except router_client.RouterError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc

    def _saude(remota: dict | None) -> dict:
        """Estado real da conta, como a instância enxerga.

        Sem isto, uma conta que bateu no limite (429) ou cuja sessão expirou
        continuaria aparecendo como saudável, e o cliente só descobriria pelo
        agente falhando no meio de um atendimento.
        """
        if remota is None:
            return {"conectada": False, "situacao": "ausente"}
        estado = remota.get("testStatus") or "unknown"
        return {
            "conectada": True,
            "situacao": estado,
            "ativa": bool(remota.get("isActive")),
            "prioridade": remota.get("priority"),
            "expira_em": remota.get("expiresAt"),
            "ultimo_uso": remota.get("lastUsedAt"),
            "ultimo_erro": (remota.get("lastError") or "")[:200] or None,
        }

    return [
        {
            "id": str(r["id"]),
            "provider": r["provider"],
            "provider_nome": (router_catalog.POR_ID.get(r["provider"], {})).get("nome")
            or r["provider"],
            "auth_type": r["auth_type"],
            "label": r["label"],
            # Conta que sumiu da instância aparece como pendente em vez de
            # desaparecer silenciosamente da tela.
            **_saude(conexoes.get(r["router_connection_id"])),
        }
        for r in linhas
    ]


@router.post("/contas", status_code=201)
async def criar_conta(payload: ContaIn, user: dict = Depends(require("ai_router", "create"))):
    """Conecta uma conta por API key. Conta por OAuth é conectada na própria
    instância do tenant (o consentimento tem que acontecer lá) e depois
    sincronizada por `POST /sincronizar`."""
    registro = _exigir_router(user)

    if _modo_litellm(registro):
        # Sem instância própria pra guardar a key: a plataforma guarda a
        # credencial cifrada e só cria o deployment no LiteLLM quando um
        # combo passar a usá-la (infra-04 seção 2d — decisão de adiar pra
        # não inflar o catálogo com modelo que nenhum combo usa).
        with get_connection() as conn:
            linha = conn.execute(
                """INSERT INTO tenant_ai_accounts
                       (tenant_id, provider, auth_type, label, api_key_encrypted)
                   VALUES (%s, %s, 'apikey', %s, %s) RETURNING *""",
                (user["tenant_id"], payload.provider, payload.label, encrypt(payload.api_key)),
            ).fetchone()
        return {"id": str(linha["id"]), "provider": linha["provider"], "label": linha["label"]}

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
    if not _modo_litellm(registro):
        try:
            await router_client.delete_connection(registro, conta["router_connection_id"])
        except router_client.RouterError as exc:
            logger.warning("conta removida da plataforma mas não da instância: %s", exc)
    # Modo LiteLLM: não apaga deployment nenhum automaticamente (mesmo
    # princípio de `provisionar_litellm.py.remover` — apagar de vez é ação
    # deliberada separada, infra-04 seção 2d).
    with get_connection() as conn:
        conn.execute("DELETE FROM tenant_ai_accounts WHERE id = %s", (conta_id,))
    return {"status": "ok"}


@router.get("/catalogo")
def catalogo(user: dict = Depends(require("ai_router", "view"))):
    """Provedores que a empresa pode conectar, e como cada um conecta."""
    return router_catalog.PROVEDORES


@router.get("/modelos")
async def listar_modelos(user: dict = Depends(require("ai_router", "view"))):
    """Modelos que as contas conectadas desta empresa liberam.

    Não é o catálogo da imagem: é o cruzamento dele com as contas que existem
    na instância. Modelo sem conta correspondente não aparece, porque combo
    montado com ele seria aceito pela instância e falharia só na resposta.
    """
    registro = _exigir_router(user)
    try:
        modelos = await router_client.available_models(registro)
    except router_client.RouterError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    return [
        {
            "id": m["id"],
            "provider": router_catalog.provedor_de_modelo(m["id"]),
            "provider_nome": (
                router_catalog.POR_ID.get(router_catalog.provedor_de_modelo(m["id"]) or "", {})
            ).get("nome"),
        }
        for m in modelos
    ]



class OAuthInicioIn(BaseModel):
    provider: str = Field(min_length=1, max_length=40)


class OAuthFimIn(BaseModel):
    provider: str = Field(min_length=1, max_length=40)
    label: str = Field(min_length=1, max_length=120)
    # Fluxo redirect: o cliente cola a URL de retorno (ou só o código).
    code: str | None = None
    redirect_uri: str | None = None
    code_verifier: str | None = None
    state: str | None = None
    # Fluxo device: o código que a etapa de início devolveu.
    device_code: str | None = None
    # Fluxo device de alguns provedores (ex.: kimi) precisa de um dado extra
    # gerado na etapa de início (device_id) — trafega de volta aqui porque
    # não temos onde guardar estado entre as duas chamadas além do cliente.
    provider_extra: dict | None = None


def _provedor_do_catalogo(provider_id: str) -> dict:
    provedor = router_catalog.POR_ID.get(provider_id)
    if provedor is None:
        raise HTTPException(status_code=400, detail="Provedor não suportado")
    return provedor


def _codigo_de(texto: str) -> str:
    """Aceita a URL de retorno inteira ou só o código.

    O provedor devolve algo como `https://…/callback?code=abc#state=xyz`, e o
    cliente costuma colar a barra de endereços inteira. Exigir que ele recorte
    o código à mão é onde esse fluxo costuma quebrar.
    """
    from urllib.parse import parse_qs, urlparse

    texto = texto.strip()
    if "://" not in texto:
        return texto.split("#", 1)[0].split("&", 1)[0]
    partes = urlparse(texto)
    consulta = parse_qs(partes.query) | parse_qs(partes.fragment)
    valor = consulta.get("code", [""])[0]
    return valor or texto


def _registrar_conta(user: dict, conexao: dict, provider: str, label: str) -> dict:
    with get_connection() as conn:
        linha = conn.execute(
            """INSERT INTO tenant_ai_accounts
                   (tenant_id, router_connection_id, provider, auth_type, label)
               VALUES (%s, %s, %s, %s, %s)
               ON CONFLICT (tenant_id, router_connection_id) DO UPDATE
                   SET label = EXCLUDED.label, is_active = TRUE
               RETURNING *""",
            (user["tenant_id"], conexao["id"], provider, conexao.get("authType", "oauth"), label),
        ).fetchone()
    return {"id": str(linha["id"]), "provider": linha["provider"], "label": linha["label"]}


@router.post("/contas/oauth/iniciar")
async def oauth_iniciar(
    payload: OAuthInicioIn, user: dict = Depends(require("ai_router", "create"))
):
    """Primeira metade da conexão por assinatura.

    Devolve o que a tela precisa mostrar. No fluxo `redirect` é uma URL para
    abrir; no `device`, um código curto para digitar no site do provedor.
    `code_verifier` volta para a tela porque é ela que devolve no fim — é um
    verificador PKCE de uso único, que existe justamente para trafegar assim.
    """
    provedor = _provedor_do_catalogo(payload.provider)
    if provedor["modo"] == "apikey":
        raise HTTPException(status_code=400, detail="Este provedor conecta por chave de API")
    registro = _exigir_router(user)

    if _modo_litellm(registro):
        return await _oauth_iniciar_litellm(payload.provider, provedor["modo"])

    try:
        if provedor["modo"] == "device":
            dados = await router_client.oauth_device_code(registro, payload.provider)
        else:
            dados = await router_client.oauth_authorize(registro, payload.provider)
    except router_client.RouterError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc

    # A instância mistura convenções: o fluxo redirect responde em camelCase,
    # o device em snake_case. A tela recebe um formato só.
    return {
        "modo": provedor["modo"],
        "auth_url": (
            dados.get("verification_uri_complete")
            or dados.get("verification_uri")
            or dados.get("authUrl")
        ),
        "user_code": dados.get("user_code"),
        "device_code": dados.get("device_code"),
        "redirect_uri": dados.get("redirectUri"),
        "code_verifier": dados.get("codeVerifier"),
        "state": dados.get("state"),
    }


async def _oauth_iniciar_litellm(provider: str, modo: str) -> dict:
    """Produto-08 — motor de OAuth próprio (`app/oauth_engine.py`), portado
    do 9Router, sem depender de instância nenhuma."""
    try:
        if modo == "device":
            dados = await oauth_engine.iniciar_device(provider)
            return {
                "modo": "device",
                "auth_url": dados["verification_uri"],
                "user_code": dados["user_code"],
                "device_code": dados["device_code"],
                "redirect_uri": None,
                "code_verifier": None,
                "state": None,
                "provider_extra": dados.get("provider_extra"),
            }

        redirect_uri = f"{settings.public_base_url.rstrip('/')}/oauth/callback"
        dados = oauth_engine.iniciar_redirect(provider, redirect_uri)
        return {
            "modo": "redirect",
            "auth_url": dados["auth_url"],
            "user_code": None,
            "device_code": None,
            # Achado real 24/08/2026: pra claude, `iniciar_redirect` troca o
            # redirect_uri pro fixo do console.anthropic.com (só ele é aceito
            # pelo client_id público do Claude Code) -- devolver aqui a
            # variável local em vez de `dados["redirect_uri"]` ecoaria a URL
            # ERRADA de volta na troca de token e quebraria de novo.
            "redirect_uri": dados["redirect_uri"],
            "code_verifier": dados["code_verifier"],
            "state": dados["state"],
        }
    except oauth_engine.OAuthError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


# Achado real 24/08/2026: o client_id público de Claude e Codex CLI só
# aceita redirect_uri fixo/loopback (console.anthropic.com/oauth/code/
# callback e http://localhost:1455/auth/callback) -- Claude ainda dá pra
# colar o código na mão, mas Codex NUNCA teria como funcionar puramente
# pela nossa própria tela hospedada (localhost é sempre a máquina de quem
# tá logando, nunca a nossa). Solução: um navegador de verdade (Playwright,
# serviço `oauth-browser`) rodando NA MESMA máquina do "listener" que
# intercepta esse redirect, espelhado pro admin do tenant via WebSocket --
# ver oauth-browser/app.py.
_NAVEGADOR_MIRROR_PROVEDORES = {"claude", "codex"}


@router.post("/contas/oauth/navegador/iniciar")
async def oauth_navegador_iniciar(
    payload: OAuthInicioIn, user: dict = Depends(require("ai_router", "create"))
):
    if payload.provider not in _NAVEGADOR_MIRROR_PROVEDORES:
        raise HTTPException(
            status_code=400, detail=f"provedor '{payload.provider}' não usa o navegador espelhado"
        )
    if not settings.oauth_browser_url:
        raise HTTPException(status_code=503, detail="navegador remoto não configurado")

    _exigir_router(user)
    redirect_uri = f"{settings.public_base_url.rstrip('/')}/oauth/callback"
    try:
        dados = oauth_engine.iniciar_redirect(payload.provider, redirect_uri)
    except oauth_engine.OAuthError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc

    try:
        async with httpx.AsyncClient(timeout=35) as client:
            resp = await client.post(
                f"{settings.oauth_browser_url.rstrip('/')}/sessions",
                headers={"Authorization": f"Bearer {settings.oauth_browser_admin_token}"},
                json={"auth_url": dados["auth_url"], "provider": payload.provider},
            )
        resp.raise_for_status()
        sessao = resp.json()
    except httpx.HTTPError as exc:
        raise HTTPException(
            status_code=502, detail=f"falha ao abrir o navegador remoto: {exc}"
        ) from exc

    ws_base = (
        settings.oauth_browser_url.rstrip("/")
        .replace("https://", "wss://")
        .replace("http://", "ws://")
    )
    return {
        "modo": "navegador",
        "ws_url": f"{ws_base}/sessions/{sessao['session_id']}/stream?token={sessao['ws_token']}",
        "redirect_uri": dados["redirect_uri"],
        "code_verifier": dados["code_verifier"],
        "state": dados["state"],
    }


@router.post("/contas/oauth/concluir", status_code=201)
async def oauth_concluir(
    payload: OAuthFimIn, user: dict = Depends(require("ai_router", "create"))
):
    """Segunda metade: troca o código pela sessão e registra a conta."""
    provedor = _provedor_do_catalogo(payload.provider)
    registro = _exigir_router(user)

    if _modo_litellm(registro):
        return await _oauth_concluir_litellm(payload, provedor["modo"], user)

    try:
        if provedor["modo"] == "device":
            if not payload.device_code:
                raise HTTPException(status_code=400, detail="Código do dispositivo ausente")
            resultado = await router_client.oauth_poll(
                registro,
                payload.provider,
                device_code=payload.device_code,
                code_verifier=payload.code_verifier,
            )
            # Enquanto o cliente não confirma no site do provedor, isso não é
            # erro: a tela continua esperando.
            if not resultado.get("success"):
                return {
                    "pendente": bool(resultado.get("pending")),
                    "erro": None if resultado.get("pending") else resultado.get("error"),
                }
            conexao = resultado["connection"]
        else:
            if not payload.code:
                raise HTTPException(status_code=400, detail="Código de autorização ausente")
            resultado = await router_client.oauth_exchange(
                registro,
                payload.provider,
                code=_codigo_de(payload.code),
                redirect_uri=payload.redirect_uri or "http://localhost:8080/callback",
                code_verifier=payload.code_verifier,
                state=payload.state,
            )
            conexao = resultado["connection"]
    except router_client.RouterError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc

    conta = _registrar_conta(user, conexao, payload.provider, payload.label)
    return {"pendente": False, "conta": conta}


def _registrar_conta_oauth(
    user: dict, provider: str, label: str, resultado: oauth_engine.ResultadoToken
) -> dict:
    """Grava uma conta de assinatura nova (produto-08). Sempre INSERT — várias
    contas do mesmo provedor por tenant são o caso normal (revezamento), não
    um erro a evitar como era com `router_connection_id` (que vinha de uma
    instância 9Router única por tenant)."""
    expira_em = None
    if resultado.expires_in is not None:
        from datetime import datetime, timedelta

        expira_em = datetime.now(UTC) + timedelta(seconds=resultado.expires_in)

    with get_connection() as conn:
        linha = conn.execute(
            """INSERT INTO tenant_ai_accounts
                   (tenant_id, provider, auth_type, label, access_token_encrypted,
                    refresh_token_encrypted, token_expires_at, provider_data)
               VALUES (%s, %s, 'oauth', %s, %s, %s, %s, %s)
               RETURNING *""",
            (
                user["tenant_id"],
                provider,
                label,
                encrypt(resultado.access_token),
                encrypt(resultado.refresh_token) if resultado.refresh_token else None,
                expira_em,
                Json(resultado.provider_data or {}),
            ),
        ).fetchone()
    return {"id": str(linha["id"]), "provider": linha["provider"], "label": linha["label"]}


async def _oauth_concluir_litellm(payload: "OAuthFimIn", modo: str, user: dict) -> dict:
    if modo == "device":
        if not payload.device_code:
            raise HTTPException(status_code=400, detail="Código do dispositivo ausente")
        try:
            resultado = await oauth_engine.consultar_device(
                payload.provider, payload.device_code, payload.provider_extra
            )
        except oauth_engine.Pendente:
            return {"pendente": True, "erro": None}
        except oauth_engine.OAuthError as exc:
            # Erro real (negado, expirado) — não é "erro" de rede, é o
            # provedor respondendo; a tela mostra inline, sem toast.
            return {"pendente": False, "erro": str(exc)}
    else:
        if not payload.code:
            raise HTTPException(status_code=400, detail="Código de autorização ausente")
        if not payload.redirect_uri or not payload.code_verifier:
            raise HTTPException(status_code=400, detail="Dados da autorização incompletos")
        try:
            resultado = await oauth_engine.concluir_redirect(
                payload.provider, payload.code, payload.redirect_uri, payload.code_verifier
            )
        except oauth_engine.OAuthError as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc

    conta = _registrar_conta_oauth(user, payload.provider, payload.label, resultado)
    return {"pendente": False, "conta": conta}


class ContaPatch(BaseModel):
    priority: int | None = Field(default=None, ge=1, le=99)
    is_active: bool | None = None


@router.patch("/contas/{conta_id}")
async def ajustar_conta(
    conta_id: str, payload: ContaPatch, user: dict = Depends(require("ai_router", "edit"))
):
    """Ordem do revezamento e liga/desliga.

    A prioridade é o que faz a conta 2 assumir quando a 1 bate no limite —
    é o motivo de existir mais de uma conta do mesmo provedor.
    """
    registro = _exigir_router(user)
    with get_connection() as conn:
        conta = conn.execute(
            "SELECT * FROM tenant_ai_accounts WHERE id = %s AND tenant_id = %s",
            (conta_id, user["tenant_id"]),
        ).fetchone()
    if conta is None:
        raise HTTPException(status_code=404, detail="Conta não encontrada")

    campos = {}
    if payload.priority is not None:
        campos["priority"] = payload.priority
    if payload.is_active is not None:
        campos["isActive"] = payload.is_active
    if not campos:
        return {"status": "ok"}
    try:
        await router_client.update_connection(registro, conta["router_connection_id"], **campos)
    except router_client.RouterError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    return {"status": "ok"}


class EstrategiaIn(BaseModel):
    provider: str = Field(min_length=1, max_length=40)
    # "round-robin" reveza a cada chamada; "fallback" só troca quando a conta
    # da vez falha. Round-robin é o que distribui limite entre contas.
    estrategia: str = Field(pattern="^(round-robin|fallback)$")


@router.get("/estrategia")
async def ler_estrategia(user: dict = Depends(require("ai_router", "view"))):
    registro = _exigir_router(user)
    try:
        config = await router_client.settings(registro)
    except router_client.RouterError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    por_provedor = config.get("providerStrategies") or {}
    return {
        provedor: (por_provedor.get(provedor) or {}).get("fallbackStrategy", "fallback")
        for provedor in {p["id"] for p in router_catalog.PROVEDORES}
    }


@router.put("/estrategia")
async def definir_estrategia(
    payload: EstrategiaIn, user: dict = Depends(require("ai_router", "edit"))
):
    """Liga o revezamento entre as contas de um provedor."""
    _provedor_do_catalogo(payload.provider)
    registro = _exigir_router(user)
    try:
        config = await router_client.settings(registro)
        estrategias = dict(config.get("providerStrategies") or {})
        estrategias[payload.provider] = {
            **(estrategias.get(payload.provider) or {}),
            "fallbackStrategy": payload.estrategia,
        }
        await router_client.update_settings(registro, {"providerStrategies": estrategias})
    except router_client.RouterError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    return {"status": "ok", "provider": payload.provider, "estrategia": payload.estrategia}


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


async def _criar_combo_litellm(payload: ComboIn, user: dict, registro: dict, tenant: dict) -> dict:
    """Um combo É o grupo de revezamento (infra-04 seção 2d, decisão final):
    1 combo -> 1 `model_name` do LiteLLM, 1 deployment por modelo do combo,
    usando a conta ativa do tenant pra aquele provider. O Router do LiteLLM
    reveza entre os deployments do `model_name` na hora da chamada, mesmo
    comportamento que o combo tinha no 9Router."""
    nome_grupo = f"tenant_{_slug(tenant['tenant_key'])}_combo_{_slug(payload.name)}"
    base_url, master_key = _config_litellm()

    with get_connection() as conn:
        contas = conn.execute(
            """SELECT * FROM tenant_ai_accounts
                WHERE tenant_id = %s AND is_active AND api_key_encrypted IS NOT NULL""",
            (user["tenant_id"],),
        ).fetchall()
    conta_por_provider = {}
    for conta in contas:
        conta_por_provider.setdefault(conta["provider"], conta)

    fora = []
    para_criar = []
    for modelo in payload.models:
        provider = router_catalog.provedor_de_modelo(modelo)
        conta = conta_por_provider.get(provider) if provider else None
        if conta is None:
            fora.append(modelo)
        else:
            para_criar.append((modelo, conta))
    if fora:
        raise HTTPException(
            status_code=400,
            detail=f"Modelos não disponíveis nas contas desta empresa: {', '.join(fora)}",
        )

    try:
        for modelo, conta in para_criar:
            await litellm_client.create_deployment(
                base_url,
                master_key,
                model_name=nome_grupo,
                provider_model=modelo,
                api_key=decrypt(conta["api_key_encrypted"]),
                tenant_id=str(user["tenant_id"]),
            )
        await litellm_client.add_model_to_team(
            base_url, master_key, team_id=registro["litellm_team_id"], model_name=nome_grupo
        )
    except litellm_client.LiteLLMError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc

    with get_connection() as conn:
        servico = conn.execute(
            """INSERT INTO ai_services (tenant_id, name, provider, model, api_base,
                                        api_key_encrypted, is_active)
               VALUES (%s, %s, 'openai-compatible', %s, %s, %s, TRUE) RETURNING *""",
            (
                user["tenant_id"],
                f"Combo: {payload.name}",
                nome_grupo,
                f"{base_url.rstrip('/')}/v1",
                registro["bridge_key_encrypted"],
            ),
        ).fetchone()
        combo = conn.execute(
            """INSERT INTO tenant_ai_combos
                   (tenant_id, name, router_combo_name, models, ai_service_id)
               VALUES (%s, %s, %s, %s, %s) RETURNING *""",
            (
                user["tenant_id"],
                payload.name,
                nome_grupo,
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

    if _modo_litellm(registro):
        return await _criar_combo_litellm(payload, user, registro, tenant)

    nome_no_router = f"t_{_slug(tenant['tenant_key'])}_{_slug(payload.name)}"

    # Um combo só pode usar modelo que as contas do próprio tenant habilitam.
    # A instância aceitaria qualquer string (testado: modelo inexistente entra
    # com 201 e só falha ao responder), então a validação de verdade é aqui.
    try:
        disponiveis = {m["id"] for m in await router_client.available_models(registro)}
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

    if _modo_litellm(registro):
        # Não apaga deployment nenhum automaticamente (mesmo princípio da
        # remoção de conta acima — infra-04 seção 2d).
        pass
    else:
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


class LiteLLMRouterIn(BaseModel):
    tenant_id: str
    litellm_team_id: str
    bridge_key: str
    ai_assist_key: str


@router.put("/instancias-litellm", status_code=201)
def registrar_instancia_litellm(
    payload: LiteLLMRouterIn, user: dict = Depends(require("ai_router", "edit"))
):
    """Registra o Team LiteLLM de um tenant. **Só o master.**

    Rota nova, separada de `/instancias` (9Router) — de propósito, não um
    campo discriminador no mesmo payload: os dois formatos não têm nenhum
    campo em comum de verdade (`base_url`/`admin_password` vs.
    `litellm_team_id`/2 keys), então misturar os dois na mesma rota só
    obrigaria campos opcionais fantasmas e validação cruzada a mais.

    O provisionamento em si roda via `scripts/provisionar_litellm.py`
    (3 chamadas HTTP pro LiteLLM — sem SSH, sem DNS, sem container, ao
    contrário do 9Router); aqui a plataforma só guarda o resultado, cifrado.

    **Limitação conhecida, registrada em `memoria.md`**: as rotas de contas/
    combos/OAuth abaixo ainda assumem o formato 9Router (`registro["base_url"]`
    via `router_client`) — um tenant registrado só por aqui ainda não
    consegue conectar conta própria pela tela até essas rotas ganharem o
    branch pro `litellm_client`. Fica registrado como próximo passo, não
    bloqueia esta rota em si."""
    if not user.get("is_master"):
        raise HTTPException(status_code=403, detail="Somente o administrador da plataforma")
    with get_connection() as conn:
        linha = conn.execute(
            """INSERT INTO tenant_routers
                   (tenant_id, litellm_team_id, bridge_key_encrypted, ai_assist_key_encrypted)
               VALUES (%s, %s, %s, %s)
               ON CONFLICT (tenant_id) DO UPDATE
                   SET litellm_team_id = EXCLUDED.litellm_team_id,
                       bridge_key_encrypted = EXCLUDED.bridge_key_encrypted,
                       ai_assist_key_encrypted = EXCLUDED.ai_assist_key_encrypted,
                       is_active = TRUE,
                       updated_at = now()
               RETURNING *""",
            (
                payload.tenant_id,
                payload.litellm_team_id,
                encrypt(payload.bridge_key),
                encrypt(payload.ai_assist_key),
            ),
        ).fetchone()
    return {
        "id": str(linha["id"]),
        "tenant_id": str(linha["tenant_id"]),
        "litellm_team_id": linha["litellm_team_id"],
    }
