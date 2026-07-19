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
from contextvars import ContextVar
from decimal import Decimal, InvalidOperation

import httpx
from mcp import ClientSession
from mcp.server.fastmcp import FastMCP
from mcp.shared.memory import create_connected_server_and_client_session

catalog = FastMCP("agent-platform-tools")

_SECRET_REF = re.compile(r"\{\{secret:([A-Za-z0-9_.-]+)\}\}")

# Per-run execution context. A ContextVar (not a module global) because the
# kernel serves concurrent runs in one process — a global would leak one
# tenant's secrets/datasources into another tenant's run.
RUN_CONTEXT: ContextVar[dict] = ContextVar("run_context")


def _context() -> dict:
    try:
        return RUN_CONTEXT.get()
    except LookupError:
        return {}


def set_run_context(
    *, secrets: dict[str, str], datasources: list[dict], tenant_id, chat_id
) -> None:
    RUN_CONTEXT.set(
        {
            "secrets": secrets,
            "datasources": {d["name"]: d for d in datasources},
            "tenant_id": tenant_id,
            "chat_id": chat_id,
            "agent": "",
        }
    )


def set_current_agent(agent_name: str) -> None:
    context = dict(_context())
    context["agent"] = agent_name
    RUN_CONTEXT.set(context)


def _resolve_secrets(text: str) -> str:
    secrets = _context().get("secrets", {})

    def sub(match: re.Match) -> str:
        return secrets.get(match.group(1), match.group(0))

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


@catalog.tool()
async def describe_datasources() -> str:
    """Lista as fontes de dados disponíveis nesta conversa, com suas tabelas e
    colunas. Chame antes de escrever SQL para saber o que existe."""
    context = _context()
    datasources = context.get("datasources", {})
    if not datasources:
        return "Nenhuma fonte de dados vinculada a este template."

    from app.datasources import list_tables

    output = []
    for name, datasource in datasources.items():
        entry = {"datasource": name, "kind": datasource["kind"]}
        try:
            entry["tables"] = await list_tables(datasource)
        except Exception as exc:  # noqa: BLE001 — reported to the model
            entry["error"] = str(exc)[:300]
        output.append(entry)
    return json.dumps(output, ensure_ascii=False, default=str)


@catalog.tool()
async def run_sql_query(datasource: str, query: str, title: str = "") -> str:
    """Executa uma consulta SQL de LEITURA (SELECT/WITH) na fonte de dados e
    materializa o resultado como um dataset artifact. Retorna o artifact_id,
    o schema e uma amostra das primeiras linhas — use o artifact_id para
    encadear com outras tools (gráfico, planilha) sem reexecutar a consulta.
    `datasource` é o nome da fonte (veja describe_datasources)."""
    from app.config import settings
    from app.datasources import execute_query
    from app.storage import register_artifact

    context = _context()
    source = context.get("datasources", {}).get(datasource)
    if source is None:
        available = ", ".join(context.get("datasources", {})) or "(nenhuma)"
        return f"ERRO: fonte '{datasource}' não existe. Disponíveis: {available}"
    try:
        columns, rows = await execute_query(source, query, settings.sql_max_rows)
    except Exception as exc:  # noqa: BLE001 — the model needs the error to retry
        return f"ERRO na consulta: {exc}"

    preview = rows[: settings.artifact_preview_rows]
    descriptor = await register_artifact(
        tenant_id=context.get("tenant_id"),
        chat_id=context.get("chat_id"),
        agent_name=context.get("agent", ""),
        kind="dataset",
        title=title or f"Consulta em {datasource}",
        schema_json=columns,
        preview_json=preview,
        row_count=len(rows),
        payload=json.dumps(
            {"columns": columns, "rows": rows}, ensure_ascii=False, default=str
        ).encode(),
    )
    return json.dumps(descriptor, ensure_ascii=False, default=str)


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
