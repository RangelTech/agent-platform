"""Isolated MCP runner for tenant-authored Python tools.

The process is intentionally separate from both agent-platform backend and
kernel. Every MCP request authenticates an opaque per-tenant bearer token;
the tenant id in the URL is only a cross-check and is never trusted alone.
"""

import asyncio
import hashlib
import json
import os
import subprocess
import sys
import tempfile
import textwrap
from contextlib import asynccontextmanager
from contextvars import ContextVar
from pathlib import Path
from typing import Any

from cryptography.fernet import Fernet
from fastapi import FastAPI, HTTPException, Request
from mcp.server import Server
from mcp.server.fastmcp.server import StreamableHTTPASGIApp, StreamableHTTPSessionManager
from mcp.types import TextContent, Tool
from psycopg import connect

DATABASE_URL = os.environ.get("DATABASE_URL", "")
ENCRYPTION_KEY = os.environ.get("ENCRYPTION_KEY", "")
CURRENT_TENANT: ContextVar[str | None] = ContextVar("custom_tool_tenant", default=None)


def _db():
    if not DATABASE_URL:
        raise RuntimeError("DATABASE_URL não configurada")
    return connect(DATABASE_URL, row_factory=dict)


def _hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def _decrypt(value: str | None) -> dict[str, str]:
    if not value:
        return {}
    if not ENCRYPTION_KEY:
        raise RuntimeError("ENCRYPTION_KEY não configurada")
    return json.loads(Fernet(ENCRYPTION_KEY.encode()).decrypt(value.encode()).decode())


def _tool(tenant_id: str, name: str, enabled_only: bool = False) -> dict | None:
    extra = " AND enabled" if enabled_only else ""
    with _db() as conn:
        return conn.execute(
            f"SELECT * FROM custom_tools WHERE tenant_id = %s AND name = %s{extra}",
            (tenant_id, name),
        ).fetchone()


def _tools(tenant_id: str) -> list[dict]:
    with _db() as conn:
        return conn.execute(
            """SELECT name, description, input_schema FROM custom_tools
                 WHERE tenant_id = %s AND enabled ORDER BY name""",
            (tenant_id,),
        ).fetchall()


_WRAPPER = r"""
import ipaddress
import json
import os
import socket
import sys

BLOCKED_NETS = tuple(ipaddress.ip_network(value) for value in (
    "127.0.0.0/8", "10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16",
    "169.254.0.0/16", "::1/128", "fe80::/10",
))
_connect = socket.socket.connect
_create_connection = socket.create_connection

def _blocked(host):
    try:
        return any(ipaddress.ip_address(host) in network for network in BLOCKED_NETS)
    except ValueError:
        return host in {"metadata.google.internal", "metadata"}

def _safe_connect(self, address):
    host = address[0]
    if _blocked(host):
        raise OSError("destino interno/metadados bloqueado")
    return _connect(self, address)

def _safe_create_connection(address, *args, **kwargs):
    if _blocked(address[0]):
        raise OSError("destino interno/metadados bloqueado")
    return _create_connection(address, *args, **kwargs)

socket.socket.connect = _safe_connect
socket.create_connection = _safe_create_connection

payload = json.load(open(sys.argv[1], encoding="utf-8"))
namespace = {"__name__": "tenant_tool"}
exec(payload["code"], namespace, namespace)
context = {
    "tenant_id": payload["tenant_id"],
    "secrets": json.loads(os.environ.get("CUSTOM_TOOL_SECRETS_JSON", "{}")),
}
result = namespace["main"](payload["inputs"], context)
print(json.dumps({"success": True, "data": result, "error": None}, default=str))
"""


def _run_python(tool: dict, tenant_id: str, inputs: dict[str, Any]) -> dict[str, Any]:
    """Run one tool in a clean subprocess with only its own decrypted secrets."""
    secrets = _decrypt(tool["secrets_encrypted"])
    payload = {
        "code": tool["python_code"],
        "inputs": inputs,
        "tenant_id": tenant_id,
    }
    timeout = int(tool["timeout_seconds"])

    def limits() -> None:
        # Cloud Run Linux only. Wall time remains the authoritative watchdog;
        # these rlimits stop CPU loops and unreasonable address-space growth.
        if os.name != "posix":
            return
        import resource

        resource.setrlimit(resource.RLIMIT_CPU, (timeout, timeout + 1))
        resource.setrlimit(resource.RLIMIT_AS, (32 * 1024**3, 32 * 1024**3))

    with tempfile.TemporaryDirectory(prefix="custom-tool-") as temp:
        payload_path = Path(temp) / "payload.json"
        wrapper_path = Path(temp) / "runner.py"
        payload_path.write_text(json.dumps(payload), encoding="utf-8")
        wrapper_path.write_text(textwrap.dedent(_WRAPPER), encoding="utf-8")
        try:
            child_env = {
                "PATH": os.environ.get("PATH", ""),
                "PYTHONUNBUFFERED": "1",
                # This is the complete child environment: no database,
                # service account or runner credential can leak into code.
                "CUSTOM_TOOL_SECRETS_JSON": json.dumps(secrets),
            }
            process = subprocess.run(
                [sys.executable, "-I", str(wrapper_path), str(payload_path)],
                cwd=temp,
                env=child_env,
                stdin=subprocess.DEVNULL,
                capture_output=True,
                text=True,
                timeout=timeout,
                start_new_session=True,
                preexec_fn=limits if os.name == "posix" else None,
            )
        except subprocess.TimeoutExpired:
            return {
                "success": False,
                "data": None,
                "error": {"code": "timeout", "message": "Tempo limite excedido"},
            }
    if process.returncode:
        return {
            "success": False,
            "data": None,
            "error": {
                "code": "execution_error",
                "message": process.stderr[-1000:] or "Falha ao executar ferramenta",
            },
        }
    try:
        return json.loads(process.stdout)
    except json.JSONDecodeError:
        return {
            "success": False,
            "data": None,
            "error": {"code": "invalid_output", "message": "A ferramenta não retornou JSON"},
        }


server = Server("custom-tool-runner")


@server.list_tools()
async def list_tools():
    tenant_id = CURRENT_TENANT.get()
    if not tenant_id:
        return []
    return [
        Tool(name=row["name"], description=row["description"], inputSchema=row["input_schema"])
        for row in _tools(tenant_id)
    ]


@server.call_tool(validate_input=False)
async def call_tool(name: str, arguments: dict[str, Any]):
    tenant_id = CURRENT_TENANT.get()
    if not tenant_id:
        return [
            TextContent(
                type="text",
                text=json.dumps(
                    {
                        "success": False,
                        "error": {"code": "unauthorized", "message": "Não autenticado"},
                    }
                ),
            )
        ]
    tool = _tool(tenant_id, name, enabled_only=True)
    if tool is None:
        return [
            TextContent(
                type="text",
                text=json.dumps(
                    {
                        "success": False,
                        "error": {"code": "not_found", "message": "Ferramenta indisponível"},
                    }
                ),
            )
        ]
    result = await asyncio.to_thread(_run_python, tool, tenant_id, arguments)
    return [TextContent(type="text", text=json.dumps(result, ensure_ascii=False, default=str))]


manager = StreamableHTTPSessionManager(app=server, stateless=True)
mcp_asgi = StreamableHTTPASGIApp(manager)


@asynccontextmanager
async def lifespan(_: FastAPI):
    async with manager.run():
        yield


app = FastAPI(title="Custom Tool Runner", lifespan=lifespan)


@app.middleware("http")
async def authenticate_tenant(request: Request, call_next):
    if not request.url.path.startswith(("/mcp/", "/test/")):
        return await call_next(request)
    tenant_id = request.url.path.rsplit("/", 1)[-1]
    header = request.headers.get("authorization", "")
    _, _, token = header.partition(" ")
    if not token or not tenant_id:
        raise HTTPException(401, "Bearer token obrigatório")
    with _db() as conn:
        row = conn.execute(
            """SELECT tenant_id FROM tool_runner_tokens
                 WHERE token_hash = %s AND revoked_at IS NULL""",
            (_hash(token),),
        ).fetchone()
    if row is None or str(row["tenant_id"]) != tenant_id:
        raise HTTPException(401, "Token não pertence ao tenant solicitado")
    reset = CURRENT_TENANT.set(tenant_id)
    try:
        return await call_next(request)
    finally:
        CURRENT_TENANT.reset(reset)


@app.get("/health")
async def health():
    return {"status": "ok", "service": "custom-tool-runner"}


@app.post("/test/{tenant_id}")
async def test_tool(tenant_id: str, payload: dict[str, Any]):
    # Middleware already verified both the bearer and the path tenant id.
    active_tenant = CURRENT_TENANT.get()
    if active_tenant != tenant_id:
        raise HTTPException(401, "Tenant inválido")
    name = payload.get("name")
    inputs = payload.get("inputs") or {}
    if not isinstance(name, str) or not isinstance(inputs, dict):
        raise HTTPException(400, "Informe name e inputs")
    tool = _tool(tenant_id, name, enabled_only=False)
    if tool is None:
        raise HTTPException(404, "Ferramenta não encontrada")
    return await asyncio.to_thread(_run_python, tool, tenant_id, inputs)


app.mount("/mcp", mcp_asgi)
