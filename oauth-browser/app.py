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
from playwright.async_api import async_playwright
from pydantic import BaseModel

logger = logging.getLogger("oauth-browser")

ADMIN_TOKEN = os.environ.get("OAUTH_BROWSER_ADMIN_TOKEN", "")
ALLOWED_ORIGIN = os.environ.get("OAUTH_BROWSER_ALLOWED_ORIGIN", "")
SESSION_TTL_SECONDS = 5 * 60
VIEWPORT = {"width": 1280, "height": 800}
CODEX_CALLBACK_PREFIX = "http://localhost:1455/auth/callback"
CLAUDE_CALLBACK_MARK = "console.anthropic.com/oauth/code/callback"

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
    # Achado real 25/08/2026, testado ao vivo: `headless=True` trava na
    # verificação da Cloudflare ("Verify you are human") -- Chromium
    # headless carrega um fingerprint reconhecível (navigator.webdriver,
    # etc.) que os provedores por trás de Cloudflare (Claude, e
    # provavelmente OpenAI) bloqueiam. Rodando headful (via Xvfb, ver
    # Dockerfile) + escondendo o flag de automação passa na maioria dos
    # casos -- é exatamente o mesmo Chromium controlado, só sem o sinal
    # mais óbvio de bot.
    _browser = await _playwright.chromium.launch(
        headless=False,
        args=[
            "--no-sandbox",
            "--disable-dev-shm-usage",
            "--disable-blink-features=AutomationControlled",
        ],
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
    auth_url: str
    provider: str  # "claude" | "codex" -- decide a estratégia de captura do código


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


@app.post("/sessions")
async def criar_sessao(payload: SessaoNovaIn, request: Request):
    require_admin(request)
    if payload.provider not in ("claude", "codex"):
        raise HTTPException(
            status_code=400, detail=f"provedor '{payload.provider}' não suportado aqui"
        )

    session_id = uuid.uuid4().hex
    ws_token = uuid.uuid4().hex
    context = await _browser.new_context(
        viewport=VIEWPORT,
        user_agent=(
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36"
        ),
    )
    # `--disable-blink-features=AutomationControlled` já tira o principal
    # sinal, mas `navigator.webdriver` ainda pode sobreviver em alguns
    # builds -- forçar undefined aqui é o reforço padrão contra Cloudflare.
    await context.add_init_script(
        "Object.defineProperty(navigator, 'webdriver', {get: () => undefined})"
    )
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
    else:
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

    try:
        await page.goto(payload.auth_url, wait_until="domcontentloaded", timeout=30_000)
    except Exception as exc:  # noqa: BLE001
        await _fechar_contexto(sessions.pop(session_id, {"context": context}))
        raise HTTPException(
            status_code=502, detail=f"falha ao abrir a página de autorização: {exc}"
        ) from exc

    return {"session_id": session_id, "ws_token": ws_token}


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
