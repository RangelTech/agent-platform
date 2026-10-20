"""Named tenant secrets. Write-only values (encrypted at rest), referenced
from tool inputs as {{secret:NAME}} and resolved kernel-side per run."""

import json
import re

from fastapi import APIRouter, Depends, HTTPException
from psycopg.errors import UniqueViolation
from psycopg.types.json import Json
from pydantic import BaseModel, Field

from app.auth import require
from app.credential_env import secret_vars
from app.crypto import encrypt
from app.db import get_connection
from app.tenancy import resolve_target_tenant

router = APIRouter(prefix="/api/secrets", tags=["secrets"])

_NAME = re.compile(r"^[A-Za-z0-9_.-]{1,80}$")
KINDS = ("secret", "ssh", "gcp")


class SecretIn(BaseModel):
    name: str
    value: str = Field(min_length=1, max_length=20_000)
    # secret = token/API key; ssh = chave + host/porta/usuário; gcp = JSON da service account
    kind: str = "secret"
    meta: dict = Field(default_factory=dict)  # só dados não sensíveis (host, porta, usuário)
    tenant_id: str | None = None  # master only


class SecretUpdate(BaseModel):
    value: str | None = Field(default=None, min_length=1, max_length=20_000)
    meta: dict | None = None
    is_active: bool | None = None


def env_name(kind: str, name: str) -> str:
    """Nome da variável de ambiente do agente: SSH_<NOME>, GCP_<NOME>_JSON ou o nome dado."""
    if kind == "secret":
        return name
    base = re.sub(r"[^A-Za-z0-9]+", "_", name).strip("_").upper()
    if kind == "ssh":
        base = base.removeprefix("SSH_")
        return f"SSH_{base or 'SERVIDOR'}"
    base = base.removeprefix("GCP_").removesuffix("_JSON")
    return f"GCP_{base or 'SA'}_JSON"


def _clean_meta(kind: str, meta: dict) -> dict:
    """Metadados permitidos por tipo; o que não é conhecido é descartado."""
    if kind != "ssh":
        return {}
    host = str(meta.get("host") or "").strip()
    user = str(meta.get("user") or "").strip()
    if not host or not user:
        raise HTTPException(status_code=400, detail="Informe o servidor (host) e o usuário")
    try:
        port = int(meta.get("port") or 22)
    except (TypeError, ValueError):
        port = 0
    if not 1 <= port <= 65535:
        raise HTTPException(status_code=400, detail="Porta inválida")
    return {"host": host[:255], "port": port, "user": user[:100]}


def _check_value(kind: str, value: str) -> str:
    if kind == "ssh":
        if "PRIVATE KEY" not in value:
            raise HTTPException(
                status_code=400, detail="Cole a chave privada inteira (-----BEGIN ... KEY-----)"
            )
        return value.strip() + "\n"
    if kind == "gcp":
        try:
            data = json.loads(value)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail="O arquivo não é um JSON válido") from exc
        if (
            not isinstance(data, dict)
            or not data.get("client_email")
            or not data.get("private_key")
        ):
            raise HTTPException(
                status_code=400,
                detail="Não parece uma service account do Google (faltam client_email/private_key)",
            )
    return value


def _hint(kind: str, value: str) -> str | None:
    """Final do valor, só para tokens comuns longos o bastante para não entregar o segredo."""
    return value[-4:] if kind == "secret" and len(value) >= 12 else None


def _serialize(row: dict) -> dict:
    return {
        "id": str(row["id"]),
        "tenant_id": str(row["tenant_id"]),
        "name": row["name"],
        "kind": row["kind"],
        "meta": row["meta"] or {},
        "hint": row["hint"],
        "env_vars": secret_vars(row["kind"], row["name"]),
        "is_active": row["is_active"],
        "created_at": row["created_at"].isoformat(),
        "updated_at": row["updated_at"].isoformat(),
    }


@router.get("")
def list_secrets(user: dict = Depends(require("secrets", "view"))):
    with get_connection() as conn:
        scope = (
            " WHERE NOT is_deleted"
            if user["is_master"]
            else " WHERE NOT is_deleted AND tenant_id = %s"
        )
        params = () if user["is_master"] else (user["tenant_id"],)
        rows = conn.execute(f"SELECT * FROM secrets{scope} ORDER BY name", params).fetchall()
    return [_serialize(r) for r in rows]


@router.post("", status_code=201)
def create_secret(payload: SecretIn, user: dict = Depends(require("secrets", "create"))):
    if payload.kind not in KINDS:
        raise HTTPException(status_code=400, detail=f"Tipo inválido: {payload.kind}")
    name = env_name(payload.kind, payload.name)
    if not _NAME.match(name):
        raise HTTPException(status_code=400, detail="Nome inválido (A-Z, 0-9, _ . -)")
    value = _check_value(payload.kind, payload.value)
    meta = _clean_meta(payload.kind, payload.meta)
    tenant_id = resolve_target_tenant(user, payload.tenant_id)
    with get_connection() as conn:
        try:
            row = conn.execute(
                """INSERT INTO secrets (tenant_id, name, value_encrypted, kind, meta, hint)
                   VALUES (%s, %s, %s, %s, %s, %s) RETURNING *""",
                (
                    tenant_id,
                    name,
                    encrypt(value),
                    payload.kind,
                    Json(meta),
                    _hint(payload.kind, value),
                ),
            ).fetchone()
        except UniqueViolation as exc:
            raise HTTPException(
                status_code=409, detail="Já existe um segredo com esse nome"
            ) from exc
    return _serialize(row)


@router.put("/{secret_id}")
def update_secret(
    secret_id: str, payload: SecretUpdate, user: dict = Depends(require("secrets", "edit"))
):
    scope = "" if user["is_master"] else " AND tenant_id = %s"
    scope_params = [] if user["is_master"] else [user["tenant_id"]]
    with get_connection() as conn:
        current = conn.execute(
            f"SELECT * FROM secrets WHERE id = %s AND NOT is_deleted{scope}",
            tuple([secret_id] + scope_params),
        ).fetchone()
        if current is None:
            raise HTTPException(status_code=404, detail="Segredo não encontrado")
        kind = current["kind"]
        fields, values = [], []
        if payload.value is not None:
            value = _check_value(kind, payload.value)
            fields += ["value_encrypted = %s", "hint = %s"]
            values += [encrypt(value), _hint(kind, value)]
        if payload.meta is not None:
            fields.append("meta = %s")
            values.append(Json(_clean_meta(kind, payload.meta)))
        if payload.is_active is not None:
            fields.append("is_active = %s")
            values.append(payload.is_active)
        if not fields:
            raise HTTPException(status_code=400, detail="Nada para atualizar")
        fields.append("updated_at = now()")
        row = conn.execute(
            f"UPDATE secrets SET {', '.join(fields)} WHERE id = %s{scope} RETURNING *",
            tuple(values + [secret_id] + scope_params),
        ).fetchone()
    return _serialize(row)


@router.delete("/{secret_id}")
def archive_secret(secret_id: str, user: dict = Depends(require("secrets", "delete"))):
    scope = "" if user["is_master"] else " AND tenant_id = %s"
    params = [secret_id] + ([] if user["is_master"] else [user["tenant_id"]])
    with get_connection() as conn:
        row = conn.execute(
            # o nome é liberado para poder cadastrar de novo (UNIQUE tenant+nome)
            f"UPDATE secrets SET is_deleted = TRUE, is_active = FALSE,"
            f" name = name || '~del~' || left(id::text, 8), updated_at = now()"
            f" WHERE id = %s AND NOT is_deleted{scope} RETURNING id",
            tuple(params),
        ).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="Segredo não encontrado")
    return {"status": "ok"}
