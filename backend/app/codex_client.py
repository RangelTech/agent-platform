"""Client HTTP direto pro Codex (ChatGPT backend), sem passar pelo LiteLLM.

28/08/2026 (produto-08 sec6): o provider nativo `chatgpt/` do LiteLLM
gerencia OAuth proprio via arquivo local (single-account), incompativel com
multi-tenant. A tentativa alternativa `custom_llm_provider: "openai"` +
`api_base` generico deu 404 -- o endpoint real
(`https://chatgpt.com/backend-api/codex/responses`) usa a "Responses API"
da OpenAI, formato bem diferente de chat/completions, que o LiteLLM
generico nao traduz sozinho.

Este modulo replica exatamente `open-sse/executors/codex.js` do 9Router
(o dono usou em producao por meses) -- mesma URL, mesmos headers, mesma
transformacao de corpo (role system->developer, remocao de ids
server-side, instructions default, stream obrigatorio), mesmo parsing de
erro embutido no SSE mesmo com HTTP 200.

Uso pretendido: chamado direto pelas rotas de inferencia do agent-platform
pra contas Codex, sem passar pelo LiteLLM pra esse provider especifico
(LiteLLM continua sendo usado pros outros providers -- BYOK, Claude etc).
"""

import json
import logging
from dataclasses import dataclass

import httpx

logger = logging.getLogger(__name__)

CODEX_RESPONSES_URL = "https://chatgpt.com/backend-api/codex/responses"

# Texto identico ao que o Codex CLI real manda (open-sse/config/codexInstructions.js)
# -- nao confirmado se o backend exige bater exatamente com isso, mas e o que
# o unico cliente conhecido funcionando (9Router, refletindo o CLI oficial) manda.
CODEX_DEFAULT_INSTRUCTIONS = """You are Codex, based on GPT-5. You are running as a coding \
agent in the Codex CLI on a user's computer.

## General

- When searching for text or files, prefer using `rg` or `rg --files` respectively because \
`rg` is much faster than alternatives like `grep`. (If the `rg` command is not found, then \
use alternatives.)

## Presenting your work and final message

- Plain text; be concise and factual.
"""
# Versao resumida das instrucoes -- a integra (119 linhas no 9Router) cobre
# sandboxing/aprovacao/formatacao que nao se aplicam ao nosso uso (nao somos
# um harness de CLI local). Testar primeiro se a Responses API aceita
# instructions mais curtas antes de replicar tudo; se rejeitar/comportar
# diferente, trocar pelo texto completo.

CODEX_SSE_RETRY_PATTERNS = ("server_is_overloaded", "service_unavailable_error")
CODEX_SSE_ACCOUNT_FALLBACK_PATTERNS = ("selected model is at capacity", "model_at_capacity")


class CodexError(Exception):
    pass


@dataclass
class CodexResult:
    text: str
    raw_events: list[dict]


def _convert_system_to_developer(input_items: list[dict]) -> None:
    for item in input_items:
        eh_mensagem = isinstance(item, dict) and item.get("type", "message") == "message"
        if eh_mensagem and item.get("role") == "system":
            item["role"] = "developer"


def _build_body(model: str, user_text: str, instructions: str) -> dict:
    body = {
        "model": model,
        "input": [
            {
                "type": "message",
                "role": "user",
                "content": [{"type": "input_text", "text": user_text}],
            },
        ],
        "instructions": instructions,
        "stream": True,
        "store": False,
        "reasoning": {"effort": "low", "summary": "auto"},
    }
    _convert_system_to_developer(body["input"])
    return body


def _build_headers(access_token: str, session_id: str, chatgpt_account_id: str | None) -> dict:
    headers = {
        "Content-Type": "application/json",
        "Accept": "text/event-stream",
        "Authorization": f"Bearer {access_token}",
        "originator": "codex_cli_rs",
        "User-Agent": "codex_cli_rs/0.136.0",
        "session_id": session_id,
    }
    if chatgpt_account_id:
        headers["ChatGPT-Account-ID"] = chatgpt_account_id
    return headers


def _find_nested_message(value, depth: int = 0) -> str | None:
    if value is None or depth > 6 or isinstance(value, str):
        return None
    if isinstance(value, list):
        for item in value:
            found = _find_nested_message(item, depth + 1)
            if found:
                return found
        return None
    if not isinstance(value, dict):
        return None
    if isinstance(value.get("message"), str) and value["message"].strip():
        return value["message"]
    err = value.get("error")
    if isinstance(err, dict) and isinstance(err.get("message"), str) and err["message"].strip():
        return err["message"]
    for child in value.values():
        found = _find_nested_message(child, depth + 1)
        if found:
            return found
    return None


async def executar_chamada_simples(
    access_token: str,
    model: str,
    user_text: str,
    *,
    session_id: str = "agent-platform-teste",
    chatgpt_account_id: str | None = None,
    instructions: str = CODEX_DEFAULT_INSTRUCTIONS,
    timeout: float = 60.0,
) -> CodexResult:
    """1 chamada simples, sem tools, sem historico -- replica o corpo minimo
    que `codex.js#transformRequest` monta. Le o stream SSE inteiro (nao so
    espia os primeiros bytes como o 9Router faz p/ deteccao antecipada de
    erro -- suficiente pra uma chamada de teste, nao pra produção com
    streaming real pro cliente)."""
    body = _build_body(model, user_text, instructions)
    headers = _build_headers(access_token, session_id, chatgpt_account_id)

    texto = []
    eventos: list[dict] = []
    async with httpx.AsyncClient(timeout=timeout) as client:
        async with client.stream("POST", CODEX_RESPONSES_URL, headers=headers, json=body) as resp:
            corpo_bruto = b""
            async for chunk in resp.aiter_bytes():
                corpo_bruto += chunk
            if resp.status_code != 200:
                raise CodexError(f"HTTP {resp.status_code}: {corpo_bruto[:2000]!r}")

            texto_bruto = corpo_bruto.decode("utf-8", errors="replace")
            lower = texto_bruto.lower()
            for pat in CODEX_SSE_ACCOUNT_FALLBACK_PATTERNS + CODEX_SSE_RETRY_PATTERNS:
                if pat in lower:
                    msg = _extrair_mensagem_erro(texto_bruto) or pat
                    raise CodexError(f"erro embutido no SSE (HTTP 200): {pat} -- {msg}")

            for linha in texto_bruto.splitlines():
                if not linha.startswith("data:"):
                    continue
                dado = linha[5:].strip()
                if not dado or dado == "[DONE]":
                    continue
                try:
                    evento = json.loads(dado)
                except json.JSONDecodeError:
                    continue
                eventos.append(evento)
                if evento.get("type") == "response.output_text.delta":
                    texto.append(evento.get("delta", ""))

    return CodexResult(text="".join(texto), raw_events=eventos)


def _extrair_mensagem_erro(texto_sse: str) -> str | None:
    for linha in texto_sse.splitlines():
        if not linha.startswith("data:"):
            continue
        dado = linha[5:].strip()
        if not dado or dado == "[DONE]":
            continue
        try:
            msg = _find_nested_message(json.loads(dado))
        except json.JSONDecodeError:
            continue
        if msg:
            return msg
    return None
