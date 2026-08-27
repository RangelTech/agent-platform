"""Motor de OAuth de assinatura (Claude/Codex/Antigravity/Gemini CLI/GitHub
Copilot/Kimi/Kilo Code/Cline) — produto-08 (mega-spec-reestrutura).

Portado do 9Router (`9router-fork/src/lib/oauth/providers/*.js` e
`open-sse/providers/registry/*.js`), que tinha o motor de OAuth já
testado e funcionando — só o *roteamento* entre múltiplas contas que
tinha o bug real (`selectionMutex` só serializava a escolha da conexão,
não o uso dela). Esta versão cobre só "1 conta por tenant por provedor,
guardar e renovar o token" — sem revezamento entre contas, então não
herda esse bug.

Os client_id abaixo são os mesmos client IDs PÚBLICOS que as ferramentas
oficiais (Claude Code, Codex CLI, gemini-cli, gh copilot, etc.) já usam
pros próprios fluxos locais — não é registro de app novo em provedor
nenhum, é o mesmo client_id que a ferramenta oficial usa.
"""

import base64
import hashlib
import json
import logging
import os
import secrets
import time
from dataclasses import dataclass
from datetime import UTC
from typing import Any

import httpx

logger = logging.getLogger(__name__)

_HTTP_TIMEOUT = 20.0


class OAuthError(RuntimeError):
    pass


def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def _gerar_pkce() -> tuple[str, str]:
    """(code_verifier, code_challenge) — S256, igual ao 9Router (`utils/pkce.js`)."""
    verifier = _b64url(secrets.token_bytes(32))
    challenge = _b64url(hashlib.sha256(verifier.encode()).digest())
    return verifier, challenge


def gerar_state() -> str:
    return _b64url(secrets.token_bytes(16))


@dataclass
class ResultadoToken:
    access_token: str
    refresh_token: str | None
    expires_in: int | None
    email: str | None = None
    provider_data: dict[str, Any] | None = None


# ---------------------------------------------------------------------------
# Config por provedor (fiel ao registry do 9Router, ver produto-08 seção 4b)
# ---------------------------------------------------------------------------

CLAUDE = {
    "client_id": "9d1c250a-e61b-44d9-88ed-5944d1962f5e",
    "authorize_url": "https://claude.ai/oauth/authorize",
    "token_url": "https://api.anthropic.com/v1/oauth/token",
    "scopes": "org:create_api_key user:profile user:inference",
    "flow": "redirect_pkce",
}

# 26/08/2026 (produto-08 §6): headers que o Claude Code CLI de verdade
# manda em toda chamada de inferência com token OAuth -- Anthropic exige
# isso além do `Authorization: Bearer`, não é opcional (fiel ao
# 9Router, que o dono usou em produção por meses:
# `open-sse/providers/shared.js#CLAUDE_CLI_SPOOF_HEADERS`).
CLAUDE_INFERENCE_HEADERS = {
    "Anthropic-Version": "2023-06-01",
    "Anthropic-Beta": (
        "claude-code-20250219,oauth-2025-04-20,interleaved-thinking-2025-05-14,"
        "context-management-2025-06-27,prompt-caching-scope-2026-01-05,"
        "advanced-tool-use-2025-11-20,effort-2025-11-24,structured-outputs-2025-12-15,"
        "fast-mode-2026-02-01,redact-thinking-2026-02-12,token-efficient-tools-2026-03-28"
    ),
    "Anthropic-Dangerous-Direct-Browser-Access": "true",
    "User-Agent": "claude-cli/2.1.92 (external, sdk-cli)",
    "X-App": "cli",
}

CODEX = {
    "client_id": "app_EMoamEEZ73f0CkXaXp7hrann",
    "authorize_url": "https://auth.openai.com/oauth/authorize",
    "token_url": "https://auth.openai.com/oauth/token",
    "scopes": "openid profile email offline_access",
    "flow": "redirect_pkce",
    "extra_authorize_params": {
        "id_token_add_organizations": "true",
        "codex_cli_simplified_flow": "true",
        "originator": "codex_cli_rs",
    },
}

ANTIGRAVITY = {
    "client_id": "1071006060591-tmhssin2h21lcre235vtolojh4g403ep.apps.googleusercontent.com",
    # Client secret de app "installed" (público por natureza -- é o mesmo
    # client_id/secret que o binário oficial da Antigravity embute e expõe a
    # qualquer um que decompile o executável). Ainda assim mora no Infisical,
    # não hardcoded: GitHub push protection sinaliza como segredo e a regra
    # do dono é Infisical sempre, sem exceção por "não ser secreto de verdade".
    "client_secret": os.environ.get("ANTIGRAVITY_OAUTH_CLIENT_SECRET", ""),
    "authorize_url": "https://accounts.google.com/o/oauth2/v2/auth",
    "token_url": "https://oauth2.googleapis.com/token",
    "userinfo_url": "https://www.googleapis.com/oauth2/v1/userinfo",
    "scopes": (
        "https://www.googleapis.com/auth/cloud-platform "
        "https://www.googleapis.com/auth/userinfo.email "
        "https://www.googleapis.com/auth/userinfo.profile "
        "https://www.googleapis.com/auth/cclog "
        "https://www.googleapis.com/auth/experimentsandconfigs"
    ),
    "flow": "redirect_google",
}

GEMINI_CLI = {
    "client_id": "681255809395-oo8ft2oprdrnp9e3aqf6av3hmdib135j.apps.googleusercontent.com",
    # Mesmo caso do Antigravity acima -- client secret público de app
    # "installed" do Google, mora no Infisical por regra, não por ser
    # segredo de verdade.
    "client_secret": os.environ.get("GEMINI_CLI_OAUTH_CLIENT_SECRET", ""),
    "authorize_url": "https://accounts.google.com/o/oauth2/v2/auth",
    "token_url": "https://oauth2.googleapis.com/token",
    "userinfo_url": "https://www.googleapis.com/oauth2/v1/userinfo",
    "scopes": (
        "https://www.googleapis.com/auth/cloud-platform "
        "https://www.googleapis.com/auth/userinfo.email "
        "https://www.googleapis.com/auth/userinfo.profile"
    ),
    "flow": "redirect_google",
}

GOOGLE_WORKSPACE = {
    # Client OAuth PRÓPRIO (não é client público de ferramenta oficial como
    # os acima) -- criado no projeto GCP do dono pra produto-11 seção 4.
    # GOOGLE_WORKSPACE_OAUTH_CLIENT_ID/SECRET pendentes: tela de
    # consentimento OAuth precisa ser criada manualmente no Console (não é
    # scriptável de forma confiável via gcloud) -- deixar em modo "Testing"
    # cobre o uso interno sem precisar de verificação do Google.
    "client_id": os.environ.get("GOOGLE_WORKSPACE_OAUTH_CLIENT_ID", ""),
    "client_secret": os.environ.get("GOOGLE_WORKSPACE_OAUTH_CLIENT_SECRET", ""),
    "authorize_url": "https://accounts.google.com/o/oauth2/v2/auth",
    "token_url": "https://oauth2.googleapis.com/token",
    "userinfo_url": "https://www.googleapis.com/oauth2/v1/userinfo",
    "scopes": (
        "https://www.googleapis.com/auth/calendar "
        "https://www.googleapis.com/auth/spreadsheets "
        "https://www.googleapis.com/auth/userinfo.email"
    ),
    "flow": "redirect_google",
}

MICROSOFT_GRAPH = {
    # Client OAuth PRÓPRIO -- produto-08 §12, app registrado no Azure Portal
    # (Entra ID) pelo dono, permissões Graph (Calendars.ReadWrite,
    # OnlineMeetings.ReadWrite, offline_access, User.Read) já concedidas com
    # consentimento de admin. Endpoint "common" aceita conta pessoal e
    # corporativa/escolar -- mesmo app serve qualquer tenant Microsoft do
    # cliente, não precisamos de app por organização.
    "client_id": os.environ.get("MS_OAUTH_CLIENT_ID", ""),
    "client_secret": os.environ.get("MS_OAUTH_CLIENT_SECRET", ""),
    "authorize_url": "https://login.microsoftonline.com/common/oauth2/v2.0/authorize",
    "token_url": "https://login.microsoftonline.com/common/oauth2/v2.0/token",
    "userinfo_url": "https://graph.microsoft.com/v1.0/me",
    "scopes": (
        "offline_access "
        "https://graph.microsoft.com/Calendars.ReadWrite "
        "https://graph.microsoft.com/OnlineMeetings.ReadWrite "
        "https://graph.microsoft.com/User.Read"
    ),
    "flow": "redirect_microsoft",
}

CLINE = {
    "authorize_url": "https://api.cline.bot/api/v1/auth/authorize",
    "token_exchange_url": "https://api.cline.bot/api/v1/auth/token",
    "flow": "redirect_cline",
}

GITHUB = {
    "client_id": "Iv1.b507a08c87ecfe98",
    "device_code_url": "https://github.com/login/device/code",
    "token_url": "https://github.com/login/oauth/access_token",
    "userinfo_url": "https://api.github.com/user",
    "copilot_token_url": "https://api.github.com/copilot_internal/v2/token",
    "scopes": "read:user",
    "api_version": "2022-11-28",
    "user_agent": "GitHubCopilotChat/0.26.7",
    "flow": "device_github",
}

KIMI = {
    "client_id": "17e5f671-d194-4dfb-9706-5516cb48c098",
    "device_code_url": "https://auth.kimi.com/api/oauth/device_authorization",
    "token_url": "https://auth.kimi.com/api/oauth/token",
    "authorize_device_url": "https://www.kimi.com/code/authorize_device",
    "flow": "device_kimi",
}

KILOCODE = {
    "initiate_url": "https://api.kilo.ai/api/device-auth/codes",
    "poll_url_base": "https://api.kilo.ai/api/device-auth/codes",
    "api_base_url": "https://api.kilo.ai",
    "flow": "device_kilocode",
}

PROVEDORES_OAUTH: dict[str, dict] = {
    "claude": CLAUDE,
    "codex": CODEX,
    "antigravity": ANTIGRAVITY,
    "gemini-cli": GEMINI_CLI,
    "google-workspace": GOOGLE_WORKSPACE,
    "microsoft-graph": MICROSOFT_GRAPH,
    "cline": CLINE,
    "github": GITHUB,
    "kimi": KIMI,
    "kilocode": KILOCODE,
}


def _config(provider: str) -> dict:
    cfg = PROVEDORES_OAUTH.get(provider)
    if cfg is None:
        raise OAuthError(f"provedor '{provider}' não tem OAuth de assinatura implementado")
    return cfg


# ---------------------------------------------------------------------------
# authorize (fluxo redirect: claude, codex, antigravity, gemini-cli, cline)
# ---------------------------------------------------------------------------


def iniciar_redirect(provider: str, redirect_uri: str) -> dict:
    """Devolve {auth_url, state, code_verifier, redirect_uri} pro front abrir numa aba.

    Achado real 24/08/2026 (issue conhecida do próprio Claude Code,
    github.com/anthropics/claude-code#36215, "Redirect URI is not
    supported by client"): o client_id público do Claude Code
    (`9d1c250a-...`) só aceita `https://console.anthropic.com/oauth/code/callback`
    como redirect_uri -- não é um client OAuth genérico que aceita
    qualquer URI registrada por nós. Mandar o nosso próprio domínio (como
    todo outro provedor `redirect_pkce` faz) derruba a autorização com
    "Invalid request format" antes mesmo do login. Console.anthropic.com
    então MOSTRA o código na tela pro usuário colar manualmente -- é
    exatamente o que o campo "Cole o que o provedor devolveu" do
    ContasIA.tsx já existia pra fazer, só a URL enviada estava errada.
    Por isso devolvemos aqui o redirect_uri realmente usado: o chamador
    precisa ecoar ESTE de volta na troca de token, não o que passou.

    Codex CLI tem a MESMA restrição, mas pior: o client_id público dele só
    aceita `http://localhost:1455/auth/callback` -- um endereço que só
    existe de verdade na máquina de quem está logando, nunca no nosso
    servidor. Não tem fallback de "colar código na mão" possível aqui (a
    página nunca carrega, é connection refused) -- só funciona via o
    navegador remoto (`oauth-browser`, produto-08 adendo), que roda o
    navegador E o listener dessa porta na mesma máquina de propósito.
    """
    cfg = _config(provider)
    state = gerar_state()
    code_verifier, code_challenge = _gerar_pkce()

    if cfg["flow"] == "redirect_pkce":
        if provider == "claude":
            redirect_uri = "https://console.anthropic.com/oauth/code/callback"
        elif provider == "codex":
            redirect_uri = "http://localhost:1455/auth/callback"
        params = {
            "response_type": "code",
            "client_id": cfg["client_id"],
            "redirect_uri": redirect_uri,
            "scope": cfg["scopes"],
            "code_challenge": code_challenge,
            "code_challenge_method": "S256",
            "state": state,
            **cfg.get("extra_authorize_params", {}),
        }
        if provider == "claude":
            params["code"] = "true"
        auth_url = f"{cfg['authorize_url']}?{httpx.QueryParams(params)}"
        return {
            "auth_url": auth_url,
            "state": state,
            "code_verifier": code_verifier,
            "redirect_uri": redirect_uri,
        }

    if cfg["flow"] == "redirect_google":
        params = {
            "client_id": cfg["client_id"],
            "response_type": "code",
            "redirect_uri": redirect_uri,
            "scope": cfg["scopes"],
            "state": state,
            "access_type": "offline",
            "prompt": "consent",
        }
        auth_url = f"{cfg['authorize_url']}?{httpx.QueryParams(params)}"
        return {
            "auth_url": auth_url,
            "state": state,
            "code_verifier": "",
            "redirect_uri": redirect_uri,
        }

    if cfg["flow"] == "redirect_microsoft":
        params = {
            "client_id": cfg["client_id"],
            "response_type": "code",
            "redirect_uri": redirect_uri,
            "scope": cfg["scopes"],
            "state": state,
            "response_mode": "query",
        }
        auth_url = f"{cfg['authorize_url']}?{httpx.QueryParams(params)}"
        return {
            "auth_url": auth_url,
            "state": state,
            "code_verifier": "",
            "redirect_uri": redirect_uri,
        }

    if cfg["flow"] == "redirect_cline":
        params = {
            "client_type": "extension",
            "callback_url": redirect_uri,
            "redirect_uri": redirect_uri,
        }
        auth_url = f"{cfg['authorize_url']}?{httpx.QueryParams(params)}"
        return {
            "auth_url": auth_url,
            "state": state,
            "code_verifier": "",
            "redirect_uri": redirect_uri,
        }

    raise OAuthError(f"provedor '{provider}' não usa fluxo redirect")


async def concluir_redirect(
    provider: str, code: str, redirect_uri: str, code_verifier: str
) -> ResultadoToken:
    cfg = _config(provider)
    async with httpx.AsyncClient(timeout=_HTTP_TIMEOUT) as client:
        if cfg["flow"] == "redirect_pkce":
            # Claude devolve "code#state" às vezes (paste manual da tela).
            auth_code = code.split("#", 1)[0] if "#" in code else code
            if provider == "claude":
                resp = await client.post(
                    cfg["token_url"],
                    json={
                        "code": auth_code,
                        "state": code.split("#", 1)[1] if "#" in code else "",
                        "grant_type": "authorization_code",
                        "client_id": cfg["client_id"],
                        "redirect_uri": redirect_uri,
                        "code_verifier": code_verifier,
                    },
                    headers={"Accept": "application/json"},
                )
            else:
                resp = await client.post(
                    cfg["token_url"],
                    data={
                        "grant_type": "authorization_code",
                        "client_id": cfg["client_id"],
                        "code": auth_code,
                        "redirect_uri": redirect_uri,
                        "code_verifier": code_verifier,
                    },
                    headers={"Accept": "application/json"},
                )
            if resp.status_code >= 400:
                raise OAuthError(f"troca de token falhou ({provider}): {resp.text[:300]}")
            dados = resp.json()
            return ResultadoToken(
                access_token=dados["access_token"],
                refresh_token=dados.get("refresh_token"),
                expires_in=dados.get("expires_in"),
            )

        if cfg["flow"] == "redirect_google":
            resp = await client.post(
                cfg["token_url"],
                data={
                    "grant_type": "authorization_code",
                    "client_id": cfg["client_id"],
                    "client_secret": cfg["client_secret"],
                    "code": code,
                    "redirect_uri": redirect_uri,
                },
                headers={"Accept": "application/json"},
            )
            if resp.status_code >= 400:
                raise OAuthError(f"troca de token falhou ({provider}): {resp.text[:300]}")
            dados = resp.json()
            email = None
            try:
                userinfo = await client.get(
                    cfg["userinfo_url"],
                    params={"alt": "json"},
                    headers={"Authorization": f"Bearer {dados['access_token']}"},
                )
                if userinfo.status_code < 400:
                    email = userinfo.json().get("email")
            except httpx.HTTPError:
                logger.warning("falha ao buscar userinfo do %s (não bloqueia a conexão)", provider)
            return ResultadoToken(
                access_token=dados["access_token"],
                refresh_token=dados.get("refresh_token"),
                expires_in=dados.get("expires_in"),
                email=email,
            )

        if cfg["flow"] == "redirect_microsoft":
            resp = await client.post(
                cfg["token_url"],
                data={
                    "grant_type": "authorization_code",
                    "client_id": cfg["client_id"],
                    "client_secret": cfg["client_secret"],
                    "code": code,
                    "redirect_uri": redirect_uri,
                    "scope": cfg["scopes"],
                },
                headers={"Accept": "application/json"},
            )
            if resp.status_code >= 400:
                raise OAuthError(f"troca de token falhou ({provider}): {resp.text[:300]}")
            dados = resp.json()
            email = None
            try:
                userinfo = await client.get(
                    cfg["userinfo_url"],
                    headers={"Authorization": f"Bearer {dados['access_token']}"},
                )
                if userinfo.status_code < 400:
                    corpo = userinfo.json()
                    email = corpo.get("mail") or corpo.get("userPrincipalName")
            except httpx.HTTPError:
                logger.warning("falha ao buscar userinfo do %s (não bloqueia a conexão)", provider)
            return ResultadoToken(
                access_token=dados["access_token"],
                refresh_token=dados.get("refresh_token"),
                expires_in=dados.get("expires_in"),
                email=email,
            )

        if cfg["flow"] == "redirect_cline":
            # Cline manda os tokens em base64 dentro do próprio "code" (achado
            # do 9Router) — só cai pra chamada HTTP se o decode falhar.
            try:
                padded = code + "=" * (-len(code) % 4)
                decoded = base64.b64decode(padded).decode("utf-8")
                fim = decoded.rfind("}")
                dados = json.loads(decoded[: fim + 1])
                return ResultadoToken(
                    access_token=dados["accessToken"],
                    refresh_token=dados.get("refreshToken"),
                    expires_in=_expires_in_de_iso(dados.get("expiresAt")),
                    email=dados.get("email"),
                )
            except (ValueError, KeyError, json.JSONDecodeError):
                resp = await client.post(
                    cfg["token_exchange_url"],
                    json={
                        "grant_type": "authorization_code",
                        "code": code,
                        "client_type": "extension",
                        "redirect_uri": redirect_uri,
                    },
                    headers={"Accept": "application/json"},
                )
                if resp.status_code >= 400:
                    raise OAuthError(f"troca de token falhou (cline): {resp.text[:300]}") from None
                dados = resp.json().get("data") or resp.json()
                return ResultadoToken(
                    access_token=dados["accessToken"],
                    refresh_token=dados.get("refreshToken"),
                    expires_in=_expires_in_de_iso(dados.get("expiresAt")),
                    email=(dados.get("userInfo") or {}).get("email"),
                )

    raise OAuthError(f"provedor '{provider}' não usa fluxo redirect")


def _expires_in_de_iso(expires_at: str | None) -> int | None:
    if not expires_at:
        return None
    try:
        from datetime import datetime

        dt = datetime.fromisoformat(expires_at.replace("Z", "+00:00"))
        return max(0, int((dt - datetime.now(UTC)).total_seconds()))
    except ValueError:
        return None


# ---------------------------------------------------------------------------
# device code (github, kimi, kilocode)
# ---------------------------------------------------------------------------


async def iniciar_device(provider: str) -> dict:
    cfg = _config(provider)
    async with httpx.AsyncClient(timeout=_HTTP_TIMEOUT) as client:
        if provider == "github":
            resp = await client.post(
                cfg["device_code_url"],
                data={"client_id": cfg["client_id"], "scope": cfg["scopes"]},
                headers={"Accept": "application/json"},
            )
            if resp.status_code >= 400:
                raise OAuthError(f"pedido de device code falhou (github): {resp.text[:300]}")
            dados = resp.json()
            return {
                "device_code": dados["device_code"],
                "user_code": dados["user_code"],
                "verification_uri": dados["verification_uri"],
                "expires_in": dados.get("expires_in", 900),
                "interval": dados.get("interval", 5),
            }

        if provider == "kimi":
            device_id = secrets.token_hex(16)
            resp = await client.post(
                cfg["device_code_url"],
                data={"client_id": cfg["client_id"]},
                headers={"Accept": "application/json", "X-Msh-Device-Id": device_id},
            )
            if resp.status_code >= 400:
                raise OAuthError(f"pedido de device code falhou (kimi): {resp.text[:300]}")
            dados = resp.json()
            uri = cfg["authorize_device_url"]
            return {
                "device_code": dados["device_code"],
                "user_code": dados["user_code"],
                "verification_uri": dados.get("verification_uri_complete")
                or f"{uri}?user_code={dados['user_code']}",
                "expires_in": dados.get("expires_in", 900),
                "interval": dados.get("interval", 5),
                "provider_extra": {"device_id": device_id},
            }

        if provider == "kilocode":
            resp = await client.post(
                cfg["initiate_url"], headers={"Content-Type": "application/json"}
            )
            if resp.status_code == 429:
                raise OAuthError(
                    "Muitos pedidos de autorização pendentes, tenta de novo em instantes."
                )
            if resp.status_code >= 400:
                raise OAuthError(f"pedido de device code falhou (kilocode): {resp.text[:300]}")
            dados = resp.json()
            return {
                "device_code": dados["code"],
                "user_code": dados["code"],
                "verification_uri": dados["verificationUrl"],
                "expires_in": dados.get("expiresIn", 300),
                "interval": 3,
            }

    raise OAuthError(f"provedor '{provider}' não usa fluxo device code")


class Pendente(Exception):
    """Ainda esperando o usuário confirmar no site do provedor."""


async def consultar_device(
    provider: str, device_code: str, provider_extra: dict | None = None
) -> ResultadoToken:
    cfg = _config(provider)
    provider_extra = provider_extra or {}
    async with httpx.AsyncClient(timeout=_HTTP_TIMEOUT) as client:
        if provider == "github":
            resp = await client.post(
                cfg["token_url"],
                data={
                    "client_id": cfg["client_id"],
                    "device_code": device_code,
                    "grant_type": "urn:ietf:params:oauth:grant-type:device_code",
                },
                headers={"Accept": "application/json"},
            )
            dados = resp.json()
            if dados.get("error") in ("authorization_pending", "slow_down"):
                raise Pendente
            if dados.get("error"):
                raise OAuthError(f"github: {dados.get('error_description', dados['error'])}")
            access_token = dados["access_token"]
            email = None
            try:
                userinfo = await client.get(
                    cfg["userinfo_url"],
                    headers={
                        "Authorization": f"Bearer {access_token}",
                        "X-GitHub-Api-Version": cfg["api_version"],
                        "User-Agent": cfg["user_agent"],
                    },
                )
                if userinfo.status_code < 400:
                    email = userinfo.json().get("email")
            except httpx.HTTPError:
                pass
            # GitHub access token não expira por si -- o que expira (minutos)
            # é o token interno do Copilot, renovado em `renovar()` via
            # copilot_token_url usando este access_token como refresh.
            return ResultadoToken(
                access_token=access_token, refresh_token=access_token, expires_in=1500, email=email
            )

        if provider == "kimi":
            device_id = provider_extra.get("device_id", "")
            resp = await client.post(
                cfg["token_url"],
                data={
                    "grant_type": "urn:ietf:params:oauth:grant-type:device_code",
                    "client_id": cfg["client_id"],
                    "device_code": device_code,
                },
                headers={"Accept": "application/json", "X-Msh-Device-Id": device_id},
            )
            dados = resp.json()
            if dados.get("error") in ("authorization_pending", "slow_down"):
                raise Pendente
            if dados.get("error"):
                raise OAuthError(f"kimi: {dados.get('error_description', dados['error'])}")
            return ResultadoToken(
                access_token=dados["access_token"],
                refresh_token=dados.get("refresh_token"),
                expires_in=dados.get("expires_in"),
                provider_data={"device_id": device_id},
            )

        if provider == "kilocode":
            resp = await client.get(f"{cfg['poll_url_base']}/{device_code}")
            if resp.status_code == 202:
                raise Pendente
            if resp.status_code == 403:
                raise OAuthError("autorização negada pelo usuário")
            if resp.status_code == 410:
                raise OAuthError("código de autorização expirado, tenta de novo")
            if resp.status_code >= 400:
                raise OAuthError(f"consulta de device code falhou (kilocode): {resp.text[:300]}")
            dados = resp.json()
            if dados.get("status") != "approved" or not dados.get("token"):
                raise Pendente
            org_id = None
            try:
                perfil = await client.get(
                    f"{cfg['api_base_url']}/api/profile",
                    headers={"Authorization": f"Bearer {dados['token']}"},
                )
                if perfil.status_code < 400:
                    orgs = perfil.json().get("organizations") or []
                    org_id = orgs[0]["id"] if orgs else None
            except httpx.HTTPError:
                pass
            return ResultadoToken(
                access_token=dados["token"],
                refresh_token=None,
                expires_in=None,
                email=dados.get("userEmail"),
                provider_data={"org_id": org_id} if org_id else None,
            )

    raise OAuthError(f"provedor '{provider}' não usa fluxo device code")


# ---------------------------------------------------------------------------
# Renovação (chamada sob demanda, protegida por lock de linha -- ver
# ai_router.py `_conta_com_token_valido`)
# ---------------------------------------------------------------------------


async def renovar(
    provider: str, refresh_token: str, provider_data: dict | None = None
) -> ResultadoToken:
    """Troca o refresh_token por um access_token novo. Nunca chamado sem
    lock de linha (ver produto-08 seção 4c) -- 2 renovações simultâneas da
    mesma conta podem invalidar o refresh token uma da outra."""
    cfg = _config(provider)
    provider_data = provider_data or {}
    async with httpx.AsyncClient(timeout=_HTTP_TIMEOUT) as client:
        if provider in ("claude", "codex"):
            corpo = {
                "grant_type": "refresh_token",
                "refresh_token": refresh_token,
                "client_id": cfg["client_id"],
            }
            headers = {"Accept": "application/json"}
            if provider == "claude":
                resp = await client.post(cfg["token_url"], json=corpo, headers=headers)
            else:
                resp = await client.post(cfg["token_url"], data=corpo, headers=headers)
            if resp.status_code >= 400:
                raise OAuthError(f"renovação falhou ({provider}): {resp.text[:300]}")
            dados = resp.json()
            return ResultadoToken(
                access_token=dados["access_token"],
                refresh_token=dados.get("refresh_token", refresh_token),
                expires_in=dados.get("expires_in"),
            )

        if provider in ("antigravity", "gemini-cli", "google-workspace", "microsoft-graph"):
            resp = await client.post(
                cfg["token_url"],
                data={
                    "grant_type": "refresh_token",
                    "refresh_token": refresh_token,
                    "client_id": cfg["client_id"],
                    "client_secret": cfg["client_secret"],
                },
                headers={"Accept": "application/json"},
            )
            if resp.status_code >= 400:
                raise OAuthError(f"renovação falhou ({provider}): {resp.text[:300]}")
            dados = resp.json()
            # Google não devolve refresh_token de novo na renovação -- mantém
            # o mesmo. Microsoft normalmente devolve um novo -- usar o que
            # vier, senão mantém o antigo.
            return ResultadoToken(
                access_token=dados["access_token"],
                refresh_token=dados.get("refresh_token", refresh_token),
                expires_in=dados.get("expires_in"),
            )

        if provider == "cline":
            resp = await client.post(
                CLINE["authorize_url"].replace("/authorize", "/refresh"),
                json={"refreshToken": refresh_token},
                headers={"Accept": "application/json"},
            )
            if resp.status_code >= 400:
                raise OAuthError(f"renovação falhou (cline): {resp.text[:300]}")
            dados = resp.json().get("data") or resp.json()
            return ResultadoToken(
                access_token=dados["accessToken"],
                refresh_token=dados.get("refreshToken", refresh_token),
                expires_in=_expires_in_de_iso(dados.get("expiresAt")),
            )

        if provider == "github":
            # Não é OAuth refresh de verdade -- o access_token do GitHub não
            # expira; o que precisa renovar é o token interno do Copilot
            # (minutos), usando o access_token como credencial.
            resp = await client.get(
                cfg["copilot_token_url"],
                headers={
                    "Authorization": f"Bearer {refresh_token}",
                    "Accept": "application/json",
                    "X-GitHub-Api-Version": cfg["api_version"],
                    "User-Agent": cfg["user_agent"],
                },
            )
            if resp.status_code >= 400:
                raise OAuthError(f"renovação do token do Copilot falhou: {resp.text[:300]}")
            dados = resp.json()
            return ResultadoToken(
                access_token=dados["token"],
                refresh_token=refresh_token,
                expires_in=max(
                    0, dados.get("expires_at", int(time.time()) + 1500) - int(time.time())
                ),
            )

    raise OAuthError(f"provedor '{provider}' não suporta renovação de token")
