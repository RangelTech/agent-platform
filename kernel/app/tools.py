"""Platform tool catalog — a formal in-process MCP server.

Tools are defined once here (FastMCP); the runtime consumes them through a
real MCP client over the in-memory transport, so the contract is pure MCP and
the server can be split out later without touching callers. External MCP
servers declared on a template are reached with the streamable-HTTP client
and their tools are namespaced ext_<server>_<tool>.
"""

import json
import re
from contextlib import AsyncExitStack
from decimal import Decimal, InvalidOperation

import httpx
from mcp import ClientSession
from mcp.server.fastmcp import FastMCP
from mcp.shared.memory import create_connected_server_and_client_session

catalog = FastMCP("agent-platform-tools")

_SECRET_REF = re.compile(r"\{\{secret:([A-Za-z0-9_.-]+)\}\}")

# Set per-run by the runtime; secrets never travel through the LLM context.
_run_secrets: dict[str, str] = {}


def set_run_secrets(secrets: dict[str, str]) -> None:
    _run_secrets.clear()
    _run_secrets.update(secrets)


def _resolve_secrets(text: str) -> str:
    def sub(match: re.Match) -> str:
        return _run_secrets.get(match.group(1), match.group(0))

    return _SECRET_REF.sub(sub, text)


@catalog.tool()
def calculate(expression: str) -> str:
    """Calculadora determinística para aritmética exata. Use SEMPRE que
    precisar de contas (somas, porcentagens, juros). Aceita + - * / % ** e
    parênteses. Exemplo: (1500 * 1.05) - 200"""
    cleaned = expression.replace(",", ".").replace(" ", "")
    if not re.fullmatch(r"[0-9.+\-*/%()eE]+", cleaned):
        return "ERRO: expressão contém caracteres não permitidos"
    try:
        # Decimal-friendly eval without names/builtins; the regex above blocks
        # identifiers so this cannot reach arbitrary code.
        result = eval(cleaned, {"__builtins__": {}}, {})  # noqa: S307
        if isinstance(result, float):
            result = Decimal(str(result)).normalize()
        return str(result)
    except (SyntaxError, ZeroDivisionError, InvalidOperation, ArithmeticError) as exc:
        return f"ERRO: {exc}"


@catalog.tool()
async def call_http_api(
    url: str,
    method: str = "GET",
    headers_json: str = "{}",
    body_json: str = "",
    timeout_seconds: int = 30,
) -> str:
    """Chama uma API HTTP externa e retorna o corpo da resposta. Referências
    {{secret:NOME}} em url, headers ou body são substituídas por segredos da
    empresa sem passar pelo modelo. headers_json e body_json são strings JSON."""
    url = _resolve_secrets(url)
    try:
        headers = json.loads(_resolve_secrets(headers_json) or "{}")
    except json.JSONDecodeError:
        return "ERRO: headers_json não é JSON válido"
    body = _resolve_secrets(body_json) if body_json else None

    method = method.upper()
    if method not in ("GET", "POST", "PUT", "PATCH", "DELETE"):
        return f"ERRO: método {method} não suportado"
    try:
        async with httpx.AsyncClient(timeout=min(timeout_seconds, 60)) as client:
            response = await client.request(method, url, headers=headers, content=body)
        text = response.text[:20_000]
        return f"HTTP {response.status_code}\n{text}"
    except httpx.HTTPError as exc:
        return f"ERRO: {exc}"


def open_catalog_session():
    """Async context manager yielding a live MCP ClientSession over the
    in-memory transport, connected to the platform catalog."""
    return create_connected_server_and_client_session(catalog._mcp_server)


class ExternalServers:
    """Connects a template's external MCP servers (streamable HTTP) and maps
    their tools under ext_<server>_<tool>."""

    def __init__(self, servers: list[dict]):
        self._servers = servers
        self._stack: AsyncExitStack | None = None
        self.sessions: dict[str, ClientSession] = {}
        self.tools: dict[str, tuple[str, str, dict]] = {}  # public -> (server, tool, schema)

    async def __aenter__(self):
        from mcp.client.streamable_http import streamablehttp_client

        self._stack = AsyncExitStack()
        for server in self._servers:
            name = server["name"]
            headers = (
                {"Authorization": f"Bearer {server['auth_token']}"}
                if server.get("auth_token")
                else None
            )
            try:
                read, write, _ = await self._stack.enter_async_context(
                    streamablehttp_client(server["url"], headers=headers)
                )
                session = await self._stack.enter_async_context(ClientSession(read, write))
                await session.initialize()
                self.sessions[name] = session
                listed = await session.list_tools()
                for tool in listed.tools:
                    public = f"ext_{name}_{tool.name}"
                    self.tools[public] = (name, tool.name, tool.inputSchema or {})
            except Exception:  # noqa: BLE001 — a dead external server must not kill the run
                continue
        return self

    async def __aexit__(self, *exc):
        if self._stack:
            await self._stack.aclose()

    async def call(self, public_name: str, arguments: dict) -> str:
        server, tool, _ = self.tools[public_name]
        result = await self.sessions[server].call_tool(tool, arguments)
        parts = [c.text for c in result.content if getattr(c, "text", None)]
        return "\n".join(parts) or "(sem conteúdo)"
