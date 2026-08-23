"""Isolated MCP runner for tenant-authored Python tools.

The process is intentionally separate from both agent-platform backend and
kernel. Every MCP request authenticates an opaque per-tenant bearer token;
the tenant id in the URL is only a cross-check and is never trusted alone.
"""

import asyncio
import hashlib
import json
import os
import signal
import subprocess
import sys
import tempfile
import textwrap
from contextlib import asynccontextmanager, nullcontext
from contextvars import ContextVar
from pathlib import Path
from typing import Any

import httpx
from cryptography.fernet import Fernet
from fastapi import FastAPI, HTTPException, Request
from mcp.server import Server
from mcp.server.fastmcp.server import StreamableHTTPASGIApp, StreamableHTTPSessionManager
from mcp.types import TextContent, Tool
from psycopg import connect
from psycopg.rows import dict_row

DATABASE_URL = os.environ.get("DATABASE_URL", "")
ENCRYPTION_KEY = os.environ.get("ENCRYPTION_KEY", "")
KERNEL_URL = os.environ.get("KERNEL_URL", "")
KERNEL_INTERNAL_TOKEN = os.environ.get("KERNEL_INTERNAL_TOKEN", "")
CURRENT_TENANT: ContextVar[str | None] = ContextVar("custom_tool_tenant", default=None)
CURRENT_CHAT: ContextVar[str | None] = ContextVar("custom_tool_chat", default=None)
MAX_ARTIFACT_BYTES = 250 * 1024 * 1024


def _db():
    if not DATABASE_URL:
        raise RuntimeError("DATABASE_URL não configurada")
    return connect(DATABASE_URL, row_factory=dict_row)


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
import builtins
import importlib
import ipaddress
import json
import os
import socket
import sys

BLOCKED_NETS = tuple(ipaddress.ip_network(value) for value in (
    "127.0.0.0/8", "10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16",
    "169.254.0.0/16", "::1/128", "fe80::/10", "fc00::/7",
    # IPv4-mapped IPv6 can otherwise bypass the IPv4 metadata/private ranges.
    "::ffff:0:0/96",
))
_connect = socket.socket.connect
_create_connection = socket.create_connection
_getaddrinfo = socket.getaddrinfo

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

def _safe_getaddrinfo(host, *args, **kwargs):
    if _blocked(host):
        raise OSError("destino interno/metadados bloqueado")
    answers = _getaddrinfo(host, *args, **kwargs)
    allowed = [answer for answer in answers if not _blocked(answer[4][0])]
    if not allowed:
        raise OSError("destino interno/metadados bloqueado")
    return allowed

socket.socket.connect = _safe_connect
socket.create_connection = _safe_create_connection
socket.getaddrinfo = _safe_getaddrinfo

_ALLOWED_IMPORTS = {
    "base64", "csv", "datetime", "decimal", "hashlib", "httpx", "json", "math",
    "pydantic", "re", "requests", "statistics", "time", "typing", "uuid",
}
def _safe_import(name, globals=None, locals=None, fromlist=(), level=0):
    root = name.split(".", 1)[0]
    if root not in _ALLOWED_IMPORTS:
        raise ImportError(f"import de {root} nao permitido na Custom Tool")
    return importlib.import_module(name)

_WORKDIR = os.path.realpath(os.getcwd())
def _safe_open(path, *args, **kwargs):
    resolved = os.path.realpath(os.path.join(_WORKDIR, os.fspath(path)))
    if resolved != _WORKDIR and not resolved.startswith(_WORKDIR + os.sep):
        raise PermissionError("arquivo fora do diretorio isolado")
    return builtins.open(resolved, *args, **kwargs)

_SAFE_BUILTINS = {
    name: getattr(builtins, name) for name in (
        "abs", "all", "any", "bool", "bytes", "dict", "enumerate", "filter", "float",
        "int", "isinstance", "len", "list", "map", "max", "min", "next", "print",
        "range", "reversed", "round", "set", "sorted", "str", "sum", "tuple", "zip",
        "Exception", "ValueError", "KeyError", "TypeError", "RuntimeError",
    )
}
_SAFE_BUILTINS["__import__"] = _safe_import
_SAFE_BUILTINS["open"] = _safe_open

payload = json.load(open(sys.argv[1], encoding="utf-8"))
namespace = {"__name__": "tenant_tool", "__builtins__": _SAFE_BUILTINS}
exec(payload["code"], namespace, namespace)
context = {
    "tenant_id": payload["tenant_id"],
    "secrets": json.loads(os.environ.get("CUSTOM_TOOL_SECRETS_JSON", "{}")),
}
result = namespace["main"](payload["inputs"], context)
print(json.dumps({"success": True, "data": result, "error": None}, default=str))
"""


def _publish_artifact(
    artifact: dict[str, Any], temp: str, tenant_id: str, chat_id: str | None
) -> dict[str, Any]:
    """Upload a tool artifact without passing storage credentials to tenant code."""
    relative = str(artifact.get("path") or "")
    source = (Path(temp) / relative).resolve()
    if not relative or not source.is_file() or not source.is_relative_to(Path(temp).resolve()):
        raise ValueError("__artifact__.path deve apontar para um arquivo da execução")
    if source.stat().st_size > MAX_ARTIFACT_BYTES:
        raise ValueError("artifact excede o limite de 250MB")
    if not KERNEL_URL or not KERNEL_INTERNAL_TOKEN:
        raise RuntimeError("registro de artifacts não configurado")
    kind = str(artifact.get("kind") or "file")
    content_type = str(artifact.get("content_type") or "application/octet-stream")
    filename = str(artifact.get("filename") or source.name)
    extension = source.suffix.lstrip(".") or "bin"
    headers = {"Authorization": f"Bearer {KERNEL_INTERNAL_TOKEN}"}
    with httpx.Client(timeout=60.0) as client:
        init = client.post(
            f"{KERNEL_URL.rstrip('/')}/v1/artifacts/register-init",
            headers=headers,
            json={
                "tenant_id": tenant_id,
                "kind": kind,
                "extension": extension,
                "content_type": content_type,
            },
        )
        init.raise_for_status()
        target = init.json()
        with source.open("rb") as handle:
            upload = client.put(
                target["upload_url"],
                content=handle,
                headers={"Content-Type": content_type},
                timeout=3600.0,
            )
        upload.raise_for_status()
        complete = client.post(
            f"{KERNEL_URL.rstrip('/')}/v1/artifacts/register-complete",
            headers=headers,
            json={
                "artifact_id": target["artifact_id"],
                "tenant_id": tenant_id,
                "chat_id": chat_id,
                "agent_name": "custom_tool",
                "kind": kind,
                "extension": extension,
                "content_type": content_type,
                "title": filename,
                "schema_json": artifact.get("schema"),
                "preview_json": artifact.get("preview"),
                "row_count": artifact.get("row_count"),
            },
        )
        complete.raise_for_status()
        return complete.json()


def _run_python(
    tool: dict, tenant_id: str, inputs: dict[str, Any], chat_id: str | None = None
) -> dict[str, Any]:
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

    # Keep the directory alive until an optional artifact has been uploaded.
    # TemporaryDirectory cleans it at function exit, including error returns.
    temp_holder = tempfile.TemporaryDirectory(prefix="custom-tool-")
    with nullcontext(temp_holder.name) as temp:
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
            process = subprocess.Popen(
                [sys.executable, "-I", str(wrapper_path), str(payload_path)],
                cwd=temp,
                env=child_env,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                start_new_session=True,
                preexec_fn=limits if os.name == "posix" else None,
            )
            stdout, stderr = process.communicate(timeout=timeout)
        except subprocess.TimeoutExpired:
            if os.name == "posix":
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
            else:
                process.kill()
            process.communicate()
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
                "message": stderr[-1000:] or "Falha ao executar ferramenta",
            },
        }
    try:
        result = json.loads(stdout)
    except json.JSONDecodeError:
        return {
            "success": False,
            "data": None,
            "error": {"code": "invalid_output", "message": "A ferramenta não retornou JSON"},
        }

    marker = (
        result.get("data", {}).pop("__artifact__", None)
        if isinstance(result.get("data"), dict)
        else None
    )
    if marker is not None:
        try:
            result["data"]["artifact"] = _publish_artifact(marker, temp, tenant_id, chat_id)
        except (httpx.HTTPError, OSError, RuntimeError, ValueError) as exc:
            return {
                "success": False,
                "data": None,
                "error": {"code": "artifact_error", "message": str(exc)[:500]},
            }
    return result


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
    result = await asyncio.to_thread(_run_python, tool, tenant_id, arguments, CURRENT_CHAT.get())
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
    chat_reset = CURRENT_CHAT.set(request.query_params.get("chat_id") or None)
    try:
        return await call_next(request)
    finally:
        CURRENT_TENANT.reset(reset)
        CURRENT_CHAT.reset(chat_reset)


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
    return await asyncio.to_thread(_run_python, tool, tenant_id, inputs, CURRENT_CHAT.get())


app.mount("/mcp", mcp_asgi)
