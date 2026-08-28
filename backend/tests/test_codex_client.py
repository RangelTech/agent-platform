"""Client direto do Codex (Responses API, sem LiteLLM no meio).

Mesma filosofia de `test_litellm_client.py` -- simula o endpoint real
(`chatgpt.com/backend-api/codex/responses`) com um servidor de verdade,
já que o formato SSE (evento por evento, erro embutido em HTTP 200) não
dá pra testar direito só mockando uma resposta estática."""

import threading

import pytest
import uvicorn
from app.codex_client import CodexError, executar_chamada_simples
from fastapi import FastAPI, Request
from fastapi.responses import StreamingResponse

pytestmark = pytest.mark.integration


def _fake_codex(sse_body: str, status_code: int = 200):
    app = FastAPI()
    recebido: dict = {}

    @app.post("/backend-api/codex/responses")
    async def responses(request: Request):
        recebido["headers"] = dict(request.headers)
        recebido["body"] = await request.json()

        async def gerar():
            yield sse_body.encode()

        return StreamingResponse(gerar(), status_code=status_code, media_type="text/event-stream")

    return app, recebido


def _subir_servidor(app: FastAPI):
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=0, log_level="critical"))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    import time

    while not server.started:
        time.sleep(0.01)
    porta = server.servers[0].sockets[0].getsockname()[1]
    return server, porta


async def _rodar(monkeypatch, sse_body: str, status_code: int = 200):
    app, recebido = _fake_codex(sse_body, status_code)
    server, porta = _subir_servidor(app)
    monkeypatch.setattr(
        "app.codex_client.CODEX_RESPONSES_URL",
        f"http://127.0.0.1:{porta}/backend-api/codex/responses",
    )
    try:
        resultado = await executar_chamada_simples("token-fake", "gpt-5.4", "oi")
        return resultado, recebido
    finally:
        server.should_exit = True


@pytest.mark.asyncio
async def test_extrai_texto_real(monkeypatch):
    sse = (
        'data: {"type":"response.output_text.delta","delta":"fun"}\n\n'
        'data: {"type":"response.output_text.delta","delta":"cionou"}\n\n'
        "data: [DONE]\n\n"
    )
    resultado, recebido = await _rodar(monkeypatch, sse)
    assert resultado.text == "funcionou"
    assert recebido["headers"]["authorization"] == "Bearer token-fake"
    assert recebido["headers"]["originator"] == "codex_cli_rs"
    assert recebido["body"]["stream"] is True
    assert recebido["body"]["store"] is False
    assert recebido["body"]["model"] == "gpt-5.4"


@pytest.mark.asyncio
async def test_erro_embutido_no_sse_apesar_de_http_200(monkeypatch):
    sse = 'data: {"type":"error","error":{"message":"server_is_overloaded"}}\n\n'
    with pytest.raises(CodexError, match="server_is_overloaded"):
        await _rodar(monkeypatch, sse)


@pytest.mark.asyncio
async def test_erro_de_capacidade_por_conta(monkeypatch):
    sse = (
        'data: {"type":"error","error":'
        '{"message":"model_at_capacity, selected model is at capacity"}}\n\n'
    )
    with pytest.raises(CodexError, match="model_at_capacity"):
        await _rodar(monkeypatch, sse)


@pytest.mark.asyncio
async def test_http_nao_200_vira_erro(monkeypatch):
    with pytest.raises(CodexError, match="HTTP 404"):
        await _rodar(monkeypatch, '{"detail":"not found"}', status_code=404)


def test_role_system_vira_developer_no_corpo():
    from app import codex_client

    body = codex_client._build_body("gpt-5.4", "oi", "instrucoes")
    body["input"].insert(0, {"type": "message", "role": "system", "content": []})
    codex_client._convert_system_to_developer(body["input"])
    assert body["input"][0]["role"] == "developer"
