"""Ambiente (workspace) da empresa: estado, iniciar/parar/backup, apps e domínios.

Com `RUNTIME_MANAGER_URL` configurada, as operações são repassadas ao
gerenciador de execução e o estado é espelhado no Postgres. Sem ela, o backend
roda em modo simulado: só o Postgres muda (suficiente para desenvolver a tela e
testar permissões). Nenhum texto daqui cita componentes internos.
"""

from __future__ import annotations

import logging
import re
import secrets as pysecrets

import httpx
from fastapi import APIRouter, Depends, HTTPException
from psycopg.errors import UniqueViolation
from psycopg.types.json import Json
from pydantic import BaseModel, Field

from app.auth import require
from app.config import settings
from app.db import get_connection
from app.kernel_router import NOT_ENABLED, tenant_skills_enabled

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/environment", tags=["environment"])

_HOST = re.compile(r"^(?=.{4,253}$)([a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}$")
_EMPTY_USAGE = {
    "cpu_pct": 0,
    "ram_mb": 0,
    "ram_limit_mb": 0,
    "disk_gb": 0,
    "disk_limit_gb": 0,
    "egress_gb": 0,
}


class DomainIn(BaseModel):
    host: str = Field(min_length=4, max_length=253)
    app_id: str


def _tenant(user: dict):
    if user["tenant_id"] is None:
        raise HTTPException(status_code=400, detail="Usuário master não possui ambiente")
    return user["tenant_id"]


# --------------------------------------------------- gerenciador (opcional) ---


def _rm_call(method: str, path: str, tenant_id, body: dict | None = None) -> dict | None:
    """None = modo simulado. Levanta 502 se o gerenciador falhar."""
    if not settings.runtime_manager_url:
        return None
    headers = (
        {"Authorization": f"Bearer {settings.runtime_manager_token}"}
        if settings.runtime_manager_token
        else {}
    )
    url = f"{settings.runtime_manager_url.rstrip('/')}/v1/tenants/{tenant_id}/environment{path}"
    try:
        response = httpx.request(method, url, json=body, headers=headers, timeout=20.0)
    except httpx.HTTPError as exc:
        logger.warning("environment manager unreachable: %s", exc.__class__.__name__)
        raise HTTPException(status_code=502, detail="Ambiente indisponível no momento") from exc
    if response.status_code >= 400:
        raise HTTPException(status_code=502, detail="O ambiente não aceitou a operação")
    try:
        return response.json()
    except ValueError:
        return {}


def _event(conn, tenant_id, kind: str, user: dict, detail: dict | None = None) -> None:
    conn.execute(
        """INSERT INTO tenant_environment_event (tenant_id, kind, detail, actor_user_id)
           VALUES (%s, %s, %s, %s)""",
        (tenant_id, kind, Json(detail or {}), user["id"]),
    )


def _ensure_row(conn, tenant_id) -> dict:
    conn.execute(
        """INSERT INTO tenant_environment (tenant_id, enabled, state)
           VALUES (%s, true, 'stopped') ON CONFLICT (tenant_id) DO NOTHING""",
        (tenant_id,),
    )
    return conn.execute(
        "SELECT * FROM tenant_environment WHERE tenant_id = %s", (tenant_id,)
    ).fetchone()


def _require_enabled(conn, tenant_id) -> None:
    if not tenant_skills_enabled(conn, tenant_id):
        raise HTTPException(status_code=403, detail=NOT_ENABLED)


def _domain_view(r: dict) -> dict:
    return {
        "id": str(r["id"]),
        "host": r["host"],
        "status": r["status"],
        "txt_name": r["txt_name"],
        "txt_value": r["txt_value"],
    }


def _snapshot(conn, tenant_id) -> dict:
    if not tenant_skills_enabled(conn, tenant_id):
        return {
            "enabled": False,
            "state": "disabled",
            "plan": None,
            "usage": dict(_EMPTY_USAGE),
            "apps": [],
            "domains": [],
            "last_backup_at": None,
        }
    env = _ensure_row(conn, tenant_id)
    apps = conn.execute(
        "SELECT * FROM tenant_exposures WHERE tenant_id = %s ORDER BY name", (tenant_id,)
    ).fetchall()
    domains = conn.execute(
        """SELECT * FROM tenant_domains WHERE tenant_id = %s AND status <> 'removed'
            ORDER BY created_at""",
        (tenant_id,),
    ).fetchall()
    return {
        "enabled": bool(env["enabled"]),
        "state": env["state"],
        "plan": env["plan"],
        "usage": {**_EMPTY_USAGE, **(env["usage"] or {})},
        "apps": [
            {
                "id": str(a["id"]),
                "name": a["name"],
                "url": a["url"],
                "status": a["status"],
                "port": a["port"],
            }
            for a in apps
        ],
        "domains": [_domain_view(d) for d in domains],
        "last_backup_at": env["last_backup_at"].isoformat() if env["last_backup_at"] else None,
    }


def _mirror(conn, tenant_id, remote: dict) -> None:
    """Grava no Postgres o estado informado pelo gerenciador."""
    if not isinstance(remote, dict):
        return
    state = remote.get("state")
    if state in ("stopped", "starting", "running", "degraded", "suspended"):
        conn.execute(
            """UPDATE tenant_environment SET state = %s, updated_at = now()
                WHERE tenant_id = %s""",
            (state, tenant_id),
        )
    if isinstance(remote.get("usage"), dict):
        conn.execute(
            "UPDATE tenant_environment SET usage = %s WHERE tenant_id = %s",
            (Json(remote["usage"]), tenant_id),
        )
    if remote.get("plan"):
        conn.execute(
            "UPDATE tenant_environment SET plan = %s WHERE tenant_id = %s",
            (str(remote["plan"]), tenant_id),
        )


# --------------------------------------------------------------- rotas ---


@router.get("")
def get_environment(user: dict = Depends(require("environment", "view"))):
    if user["tenant_id"] is None:
        return {
            "enabled": False,
            "state": "disabled",
            "plan": None,
            "usage": dict(_EMPTY_USAGE),
            "apps": [],
            "domains": [],
            "last_backup_at": None,
        }
    tenant_id = user["tenant_id"]
    with get_connection() as conn:
        snapshot = _snapshot(conn, tenant_id)
        if snapshot["enabled"] and settings.runtime_manager_url:
            try:
                remote = _rm_call("GET", "", tenant_id)
            except HTTPException:
                snapshot["state"] = "degraded"
                return snapshot
            _mirror(conn, tenant_id, remote or {})
            snapshot = _snapshot(conn, tenant_id)
    return snapshot


def _action(user: dict, action: str, new_state: str | None):
    tenant_id = _tenant(user)
    with get_connection() as conn:
        _require_enabled(conn, tenant_id)
        env = _ensure_row(conn, tenant_id)
        if env["state"] == "suspended":
            raise HTTPException(status_code=409, detail="O ambiente está suspenso")
        remote = _rm_call("POST", f"/{action}", tenant_id)
        if remote is not None:
            _mirror(conn, tenant_id, remote)
        elif new_state:
            conn.execute(
                "UPDATE tenant_environment SET state = %s, updated_at = now() WHERE tenant_id = %s",
                (new_state, tenant_id),
            )
        if action == "backup":
            conn.execute(
                "UPDATE tenant_environment SET last_backup_at = now() WHERE tenant_id = %s",
                (tenant_id,),
            )
        _event(conn, tenant_id, action, user)
        state = conn.execute(
            "SELECT state FROM tenant_environment WHERE tenant_id = %s", (tenant_id,)
        ).fetchone()["state"]
    return {"state": state}


@router.post("/start")
def start_environment(user: dict = Depends(require("environment", "manage"))):
    return _action(user, "start", "running")


@router.post("/stop")
def stop_environment(user: dict = Depends(require("environment", "manage"))):
    return _action(user, "stop", "stopped")


@router.post("/backup")
def backup_environment(user: dict = Depends(require("environment", "manage"))):
    return _action(user, "backup", None)


@router.post("/domains", status_code=201)
def add_domain(payload: DomainIn, user: dict = Depends(require("environment", "manage"))):
    tenant_id = _tenant(user)
    host = payload.host.strip().lower().rstrip(".")
    if not _HOST.match(host):
        raise HTTPException(status_code=400, detail="Endereço de domínio inválido")
    if host.endswith("." + settings.apps_base_domain) or host == settings.apps_base_domain:
        raise HTTPException(status_code=400, detail="Use um domínio próprio da empresa")
    with get_connection() as conn:
        _require_enabled(conn, tenant_id)
        app = conn.execute(
            "SELECT * FROM tenant_exposures WHERE id::text = %s AND tenant_id = %s",
            (payload.app_id, tenant_id),
        ).fetchone()
        if app is None:
            raise HTTPException(status_code=404, detail="Aplicativo não encontrado")
        tenant_key = conn.execute(
            "SELECT tenant_key FROM tenants WHERE id = %s", (tenant_id,)
        ).fetchone()["tenant_key"]
        txt_value = "rt-verify=" + pysecrets.token_hex(16)
        try:
            row = conn.execute(
                """INSERT INTO tenant_domains (tenant_id, exposure_id, host, txt_name, txt_value)
                   VALUES (%s, %s, %s, %s, %s) RETURNING *""",
                (tenant_id, app["id"], host, f"_rt-verify.{host}", txt_value),
            ).fetchone()
        except UniqueViolation as exc:
            raise HTTPException(status_code=409, detail="Este domínio já está em uso") from exc
        _rm_call(
            "POST",
            "/domains",
            tenant_id,
            {"host": host, "app": app["name"], "txt_value": txt_value},
        )
        _event(conn, tenant_id, "domain_add", user, {"host": host})
    return {
        **_domain_view(row),
        "cname_name": host,
        "cname_value": f"{app['name']}-{tenant_key}.{settings.apps_base_domain}",
    }


@router.delete("/domains/{domain_id}")
def remove_domain(domain_id: str, user: dict = Depends(require("environment", "manage"))):
    tenant_id = _tenant(user)
    with get_connection() as conn:
        _require_enabled(conn, tenant_id)
        row = conn.execute(
            """UPDATE tenant_domains SET status = 'removed'
                WHERE id::text = %s AND tenant_id = %s AND status <> 'removed' RETURNING host""",
            (domain_id, tenant_id),
        ).fetchone()
        if row is None:
            raise HTTPException(status_code=404, detail="Domínio não encontrado")
        _rm_call("DELETE", f"/domains/{row['host']}", tenant_id)
        _event(conn, tenant_id, "domain_remove", user, {"host": row["host"]})
    return {"status": "ok"}


@router.delete("/apps/{app_id}")
def unpublish_app(app_id: str, user: dict = Depends(require("environment", "manage"))):
    tenant_id = _tenant(user)
    with get_connection() as conn:
        _require_enabled(conn, tenant_id)
        row = conn.execute(
            "SELECT * FROM tenant_exposures WHERE id::text = %s AND tenant_id = %s",
            (app_id, tenant_id),
        ).fetchone()
        if row is None:
            raise HTTPException(status_code=404, detail="Aplicativo não encontrado")
        conn.execute(
            """UPDATE tenant_domains SET status = 'removed'
                WHERE exposure_id = %s AND status <> 'removed'""",
            (row["id"],),
        )
        conn.execute("DELETE FROM tenant_exposures WHERE id = %s", (row["id"],))
        _rm_call("DELETE", f"/apps/{row['name']}", tenant_id)
        _event(conn, tenant_id, "app_unpublish", user, {"app": row["name"]})
    return {"status": "ok"}
