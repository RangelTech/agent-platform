"""Navegador remoto pra fluxos OAuth que só aceitam redirect_uri fixo/loopback
(Claude: `console.anthropic.com/oauth/code/callback`; Codex CLI:
`http://localhost:1455/auth/callback`, um endereço que só existe de verdade
na máquina de quem está logando).

Processo intencionalmente separado do agent-platform backend: roda um
Chromium real (Playwright), controlado por CDP screencast/input, espelhado
pro admin do tenant via WebSocket dentro do modal do ContasIA.tsx -- ele
loga normal (email/senha/2FA), mas o navegador em si roda aqui. Pro Codex,
o "localhost:1455" do redirect_uri resolve certo porque o navegador E o
container que intercepta essa rota são a mesma máquina; não tem trapaça de
rede nenhuma, só o fato de os dois estarem juntos. Sessão é sempre um
contexto novo, descartado no fim (sucesso, erro ou timeout) -- nunca
reaproveita cookie/perfil de uma tentativa pra outra.
"""

import asyncio
import hmac
import logging
import os
import time
import uuid
from contextlib import asynccontextmanager
from urllib.parse import parse_qs, urlparse

from fastapi import FastAPI, HTTPException, Request, WebSocket, WebSocketDisconnect
from patchright.async_api import async_playwright
from pydantic import BaseModel

logger = logging.getLogger("oauth-browser")

ADMIN_TOKEN = os.environ.get("OAUTH_BROWSER_ADMIN_TOKEN", "")
ALLOWED_ORIGIN = os.environ.get("OAUTH_BROWSER_ALLOWED_ORIGIN", "")
SESSION_TTL_SECONDS = 5 * 60
VIEWPORT = {"width": 1280, "height": 800}
CODEX_CALLBACK_PREFIX = "http://localhost:1455/auth/callback"
CLAUDE_CALLBACK_MARK = "console.anthropic.com/oauth/code/callback"

# Produto-10 (25/08/2026): Facebook/Instagram "não oficial" não trocam
# código por token -- a credencial É a sessão do navegador. Em vez de
# esperar um `code` na URL, esperamos o cookie de sessão crítico aparecer
# no jar do contexto (mesmo teste que rodou ao vivo com o dono esta
# madrugada) e devolvemos os cookies inteiros, não um par code/state.
LOGIN_COOKIE_PROVEDORES = {
    "facebook_web": {
        "login_url": "https://www.facebook.com/login",
        "cookie_domain_match": "facebook.com",
        "cookie_chave": "c_user",
    },
    "instagram_web": {
        "login_url": "https://www.instagram.com/accounts/login/",
        "cookie_domain_match": "instagram.com",
        "cookie_chave": "sessionid",
    },
}

_playwright = None
_browser = None
sessions: dict[str, dict] = {}


def require_admin(request: Request) -> None:
    """Só o agent-platform backend chama /sessions -- nunca o navegador do
    admin do tenant direto (esse usa o `ws_token` de sessão, mais abaixo)."""
    if not ADMIN_TOKEN:
        raise HTTPException(status_code=503, detail="serviço sem token administrativo configurado")
    header = request.headers.get("authorization", "")
    scheme, _, token = header.partition(" ")
    if scheme.lower() != "bearer" or not hmac.compare_digest(token, ADMIN_TOKEN):
        raise HTTPException(status_code=401, detail="não autorizado")


@asynccontextmanager
async def lifespan(app: FastAPI):
    global _playwright, _browser
    _playwright = await async_playwright().start()
    # Achado real 25/08/2026, testado ao vivo: mesmo headful (Xvfb) +
    # `navigator.webdriver` escondido, o Cloudflare Turnstile chega no
    # desafio interativo mas nunca passa -- um clique real via CDP fica
    # preso em "Verifying..." e reseta sozinho. `navigator.webdriver` é só
    # o sinal mais óbvio; o vazamento real é a própria conexão CDP do
    # Playwright puro (`Runtime.enable`, entre outros). `patchright`
    # (fork mantido do Playwright, mesma API) fecha esses leaks
    # especificamente pra Cloudflare/Turnstile, e `channel="chrome"` usa o
    # Chrome de verdade (não o Chromium open-source, que tem fingerprint
    # diferente) -- ver Dockerfile pro install do Chrome patched.
    #
    # Importante: com patchright, NÃO reintroduzir os flags/scripts manuais
    # de antes (`--disable-blink-features=AutomationControlled`,
    # override de `navigator.webdriver`) -- o patchright já cobre isso
    # numa camada mais profunda, e sobrepor de novo por fora deixa o
    # fingerprint inconsistente (documentado pelo próprio projeto).
    _browser = await _playwright.chromium.launch(
        headless=False,
        channel="chrome",
        args=["--no-sandbox", "--disable-dev-shm-usage"],
    )
    yield
    for sessao in list(sessions.values()):
        await _fechar_contexto(sessao)
    await _browser.close()
    await _playwright.stop()


app = FastAPI(lifespan=lifespan)


@app.get("/health")
async def health():
    return {"status": "ok"}


class SessaoNovaIn(BaseModel):
    auth_url: str | None = None  # vazio pros provedores de login/cookie -- usam login_url própria
    provider: str  # "claude" | "codex" | "facebook_web" | "instagram_web"


async def _fechar_contexto(sessao: dict) -> None:
    try:
        await sessao["context"].close()
    except Exception:  # noqa: BLE001 -- encerramento best-effort, nunca deve derrubar o serviço
        logger.warning("falha ao fechar contexto da sessão %s", sessao.get("id"), exc_info=True)


async def _autodestruir_apos_ttl(session_id: str) -> None:
    await asyncio.sleep(SESSION_TTL_SECONDS)
    sessao = sessions.pop(session_id, None)
    if sessao and not sessao["resultado"].done():
        sessao["resultado"].cancel()
        await _fechar_contexto(sessao)


def _extrair_code_state(url: str) -> tuple[str, str]:
    qs = parse_qs(urlparse(url).query)
    return qs.get("code", [""])[0], qs.get("state", [""])[0]


async def _espera_cookie_critico(context, resultado, cookie_domain_match, cookie_chave):
    """Roda em paralelo enquanto o admin loga -- fica checando o jar do
    contexto até o cookie de sessão crítico aparecer. Não tem redirect_uri
    nem código pra interceptar aqui, a sessão inteira é a credencial."""
    for _ in range(SESSION_TTL_SECONDS // 3):
        if resultado.done():
            return
        try:
            cookies = await context.cookies()
        except Exception:  # noqa: BLE001 -- contexto pode ter sido fechado
            return
        do_dominio = [c for c in cookies if cookie_domain_match in c["domain"]]
        if any(c["name"] == cookie_chave for c in do_dominio):
            if not resultado.done():
                resultado.set_result({"cookies": do_dominio})
            return
        await asyncio.sleep(3)


@app.post("/sessions")
async def criar_sessao(payload: SessaoNovaIn, request: Request):
    require_admin(request)
    if payload.provider not in ("claude", "codex", *LOGIN_COOKIE_PROVEDORES):
        raise HTTPException(
            status_code=400, detail=f"provedor '{payload.provider}' não suportado aqui"
        )

    session_id = uuid.uuid4().hex
    ws_token = uuid.uuid4().hex
    # Sem `user_agent`/`add_init_script` manuais aqui de propósito: um UA
    # forjado que não bate com a versão real do Chrome patched (ver
    # lifespan acima) é em si um sinal de automação pra fingerprinting
    # mais avançado -- deixar o Chrome real anunciar o próprio UA, mais
    # consistente que qualquer valor fixo que a gente escolha.
    context = await _browser.new_context(viewport=VIEWPORT)
    page = await context.new_page()
    resultado: asyncio.Future = asyncio.get_event_loop().create_future()

    if payload.provider == "codex":
        # O client_id público do Codex CLI só aceita este redirect_uri --
        # ninguém escuta essa porta de verdade, a rota nunca chega a virar
        # tráfego de rede: Playwright intercepta a NAVEGAÇÃO em si.
        async def _intercepta_callback_local(route):
            code, state = _extrair_code_state(route.request.url)
            erro = parse_qs(urlparse(route.request.url).query).get("error", [""])[0]
            await route.fulfill(
                status=200,
                content_type="text/html",
                body="<html><body>Autorização concluída. Pode fechar esta janela.</body></html>",
            )
            if not resultado.done():
                if erro:
                    resultado.set_exception(RuntimeError(erro))
                else:
                    resultado.set_result({"code": code, "state": state})

        await page.route(f"{CODEX_CALLBACK_PREFIX}*", _intercepta_callback_local)
    elif payload.provider == "claude":
        # Claude pousa numa página real da Anthropic (não interceptamos --
        # é o servidor deles quem decide o que mostrar). Lemos o código da
        # URL final assim que a navegação chega lá.
        def _ao_navegar(frame):
            if frame != page.main_frame or CLAUDE_CALLBACK_MARK not in frame.url:
                return
            code, state = _extrair_code_state(frame.url)
            if code and not resultado.done():
                resultado.set_result({"code": code, "state": state})

        page.on("framenavigated", _ao_navegar)
    elif payload.provider in LOGIN_COOKIE_PROVEDORES:
        cfg = LOGIN_COOKIE_PROVEDORES[payload.provider]
        asyncio.create_task(
            _espera_cookie_critico(
                context, resultado, cfg["cookie_domain_match"], cfg["cookie_chave"]
            )
        )

    sessions[session_id] = {
        "id": session_id,
        "ws_token": ws_token,
        "context": context,
        "page": page,
        "resultado": resultado,
        "provider": payload.provider,
        "criada_em": time.time(),
    }
    asyncio.create_task(_autodestruir_apos_ttl(session_id))

    url_inicial = payload.auth_url or LOGIN_COOKIE_PROVEDORES.get(
        payload.provider, {}
    ).get("login_url")
    try:
        await page.goto(url_inicial, wait_until="domcontentloaded", timeout=30_000)
    except Exception as exc:  # noqa: BLE001
        await _fechar_contexto(sessions.pop(session_id, {"context": context}))
        raise HTTPException(
            status_code=502, detail=f"falha ao abrir a página de autorização: {exc}"
        ) from exc

    return {"session_id": session_id, "ws_token": ws_token}


class FacebookCookiesIn(BaseModel):
    cookies: list[dict]


class FacebookSendIn(BaseModel):
    cookies: list[dict]
    thread_id: str
    text: str


@asynccontextmanager
async def _contexto_com_cookies(cookies: list[dict]):
    """Contexto Playwright descartável carregado com a sessão salva --
    mesmo desenho do login (`_browser` global, contexto novo por chamada),
    só que aqui a sessão já existe (cookies do canal) em vez de fazer login.
    """
    context = await _browser.new_context(viewport=VIEWPORT)
    await context.add_cookies(cookies)
    try:
        yield context
    finally:
        await context.close()


@app.post("/facebook/inbox")
async def facebook_inbox(payload: FacebookCookiesIn, request: Request):
    """Lista as conversas do Messenger com a última mensagem de cada uma.

    Facebook não tem API HTTP não-oficial funcional (ao contrário do
    Instagram, ver produto-10 seção 6b) -- messenger.com é SPA JS-only, então
    isto navega de verdade e lê o DOM. Seletores por `aria-label`/`role`
    (mais estáveis que classes CSS, que o Facebook ofusca e troca sem
    aviso) -- ainda assim frágil por natureza; se o Facebook mudar o
    layout, isto quebra e precisa de ajuste manual.
    """
    require_admin(request)
    async with _contexto_com_cookies(payload.cookies) as context:
        page = await context.new_page()
        try:
            await page.goto(
                "https://www.facebook.com/messages/t/", wait_until="networkidle", timeout=30_000
            )
            await page.wait_for_selector('[aria-label="Chat list"], [role="grid"]', timeout=15_000)
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(
                status_code=502, detail=f"falha ao abrir a caixa de entrada: {exc}"
            ) from exc

        conversas = await page.evaluate(
            """() => {
                const linhas = document.querySelectorAll('[role="row"] a[href*="/messages/t/"]');
                const vistos = new Set();
                const out = [];
                for (const a of linhas) {
                    const href = a.getAttribute('href') || '';
                    const m = href.match(/\\/messages\\/t\\/([^/?]+)/);
                    if (!m || vistos.has(m[1])) continue;
                    vistos.add(m[1]);
                    const texto = (a.innerText || '').split('\\n').filter(Boolean);
                    out.push({
                        thread_id: m[1],
                        name: texto[0] || '',
                        snippet: texto.slice(1).join(' ') || '',
                    });
                }
                return out;
            }"""
        )
        return {"conversations": conversas}


@app.post("/facebook/send")
async def facebook_send(payload: FacebookSendIn, request: Request):
    """Envia uma mensagem de texto numa conversa existente do Messenger.

    Abre a thread, digita na caixa de composição e manda Enter -- interação
    de UI real, não chamada de API (Facebook não tem uma que funcione pra
    isto). NUNCA testado com contato real (produto-10 seção 6c) -- não
    envie mensagem de teste pra um contato desconhecido do dono.
    """
    require_admin(request)
    async with _contexto_com_cookies(payload.cookies) as context:
        page = await context.new_page()
        try:
            await page.goto(
                f"https://www.facebook.com/messages/t/{payload.thread_id}",
                wait_until="networkidle",
                timeout=30_000,
            )
            caixa = page.locator('[aria-label="Message"][contenteditable="true"]').first
            await caixa.wait_for(timeout=15_000)
            await caixa.click()
            await caixa.type(payload.text)
            await caixa.press("Enter")
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(status_code=502, detail=f"falha ao enviar: {exc}") from exc
        return {"status": "ok"}


@app.websocket("/sessions/{session_id}/stream")
async def stream(ws: WebSocket, session_id: str):
    token = ws.query_params.get("token", "")
    sessao = sessions.get(session_id)
    if ALLOWED_ORIGIN and ws.headers.get("origin") != ALLOWED_ORIGIN:
        await ws.close(code=4403)
        return
    if not sessao or not hmac.compare_digest(token, sessao["ws_token"]):
        await ws.close(code=4401)
        return

    await ws.accept()
    page = sessao["page"]
    cdp = await page.context.new_cdp_session(page)
    encerrando = False

    async def _mandar_frame(params: dict) -> None:
        try:
            await ws.send_json({"type": "frame", "data": params["data"]})
            await cdp.send("Page.screencastFrameAck", {"sessionId": params["sessionId"]})
        except Exception:  # noqa: BLE001 -- WS pode já ter caído do outro lado
            pass

    cdp.on("Page.screencastFrame", lambda params: asyncio.create_task(_mandar_frame(params)))
    await cdp.send(
        "Page.startScreencast",
        {
            "format": "jpeg",
            "quality": 60,
            "maxWidth": VIEWPORT["width"],
            "maxHeight": VIEWPORT["height"],
        },
    )

    async def _espera_resultado():
        nonlocal encerrando
        try:
            r = await sessao["resultado"]
            if "cookies" in r:
                await ws.send_json({"type": "done", "cookies": r["cookies"]})
            else:
                await ws.send_json({"type": "done", "code": r["code"], "state": r["state"]})
        except asyncio.CancelledError:
            try:
                await ws.send_json({"type": "erro", "mensagem": "tempo esgotado, tente de novo"})
            except Exception:  # noqa: BLE001 -- socket já pode ter caído do outro lado
                pass
        except Exception as exc:  # noqa: BLE001
            try:
                await ws.send_json({"type": "erro", "mensagem": str(exc)})
            except Exception:  # noqa: BLE001
                pass
        finally:
            encerrando = True
            sessions.pop(session_id, None)
            await _fechar_contexto(sessao)
            # `receive_json()` abaixo só percebe o encerramento quando o
            # socket fecha de verdade -- setar a flag sozinha deixaria o
            # laço travado esperando uma mensagem que pode nunca chegar.
            try:
                await ws.close()
            except Exception:  # noqa: BLE001
                pass

    espera_task = asyncio.create_task(_espera_resultado())

    try:
        while not encerrando:
            msg = await ws.receive_json()
            tipo = msg.get("type")
            if tipo == "mouse":
                await page.mouse.move(msg["x"], msg["y"])
                if msg.get("click"):
                    await page.mouse.click(msg["x"], msg["y"])
            elif tipo == "wheel":
                await page.mouse.wheel(msg.get("dx", 0), msg.get("dy", 0))
            elif tipo == "key" and msg.get("text"):
                await page.keyboard.type(msg["text"])
            elif tipo == "key" and msg.get("key"):
                await page.keyboard.press(msg["key"])
    except WebSocketDisconnect:
        pass
    finally:
        espera_task.cancel()
        try:
            await cdp.detach()
        except Exception:  # noqa: BLE001
            pass
        if session_id in sessions:
            sessions.pop(session_id, None)
            await _fechar_contexto(sessao)
