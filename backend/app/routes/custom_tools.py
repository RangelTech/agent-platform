"""Tenant-scoped registry for Python Custom Tools.

The runner receives only encrypted-at-rest tool material through its private
service path; browser clients only use this CRUD API under normal RBAC.
"""
import json
import re

from fastapi import APIRouter, Depends, HTTPException
from psycopg.types.json import Json
from pydantic import BaseModel, Field

from app.auth import require
from app.crypto import encrypt
from app.db import get_connection

router = APIRouter(prefix="/api/custom-tools", tags=["custom-tools"])
_NAME = re.compile(r"^[a-z][a-z0-9_]{1,60}$")


class ToolIn(BaseModel):
    name: str = Field(min_length=2, max_length=61)
    description: str = Field(min_length=1, max_length=2000)
    input_schema: dict = Field(default_factory=dict)
    python_code: str = Field(min_length=1, max_length=200_000)
    secrets: dict[str, str] = Field(default_factory=dict)
    timeout_seconds: int = Field(default=60, ge=1, le=3600)
    enabled: bool = True


def _validate(payload: ToolIn) -> None:
    if not _NAME.fullmatch(payload.name):
        raise HTTPException(400, "Nome inválido: use snake_case")
    if not isinstance(payload.input_schema.get("properties", {}), dict):
        raise HTTPException(400, "input_schema precisa ser JSON Schema válido")
    if "def main(" not in payload.python_code:
        raise HTTPException(400, "O código precisa declarar def main(inputs, context)")


def _serialize(row: dict) -> dict:
    return {k: (str(row[k]) if k == "id" else row[k]) for k in (
        "id", "name", "description", "input_schema", "timeout_seconds", "enabled", "created_at", "updated_at"
    )}


def _owned(conn, tool_id: str, tenant_id: str) -> dict:
    row = conn.execute("SELECT * FROM custom_tools WHERE id=%s AND tenant_id=%s", (tool_id, tenant_id)).fetchone()
    if row is None:
        raise HTTPException(404, "Tool não encontrada")
    return row


@router.get("")
def list_tools(user: dict = Depends(require("templates", "view"))):
    if user["is_master"]:
        raise HTTPException(400, "Selecione uma empresa para consultar Custom Tools")
    with get_connection() as conn:
        rows = conn.execute("SELECT * FROM custom_tools WHERE tenant_id=%s ORDER BY name", (user["tenant_id"],)).fetchall()
    return [_serialize(row) for row in rows]


@router.post("", status_code=201)
def create_tool(payload: ToolIn, user: dict = Depends(require("templates", "edit"))):
    if user["is_master"]:
        raise HTTPException(400, "Selecione uma empresa para criar Custom Tools")
    _validate(payload)
    with get_connection() as conn:
        row = conn.execute(
            """INSERT INTO custom_tools (tenant_id,name,description,input_schema,python_code,secrets_encrypted,timeout_seconds,enabled)
               VALUES (%s,%s,%s,%s,%s,%s,%s,%s) RETURNING *""",
            (user["tenant_id"], payload.name, payload.description, Json(payload.input_schema), payload.python_code,
             encrypt(json.dumps(payload.secrets)) if payload.secrets else None, payload.timeout_seconds, payload.enabled),
        ).fetchone()
    return _serialize(row)


@router.put("/{tool_id}")
def update_tool(tool_id: str, payload: ToolIn, user: dict = Depends(require("templates", "edit"))):
    if user["is_master"]:
        raise HTTPException(400, "Selecione uma empresa para editar Custom Tools")
    _validate(payload)
    with get_connection() as conn:
        old = _owned(conn, tool_id, user["tenant_id"])
        encrypted = encrypt(json.dumps(payload.secrets)) if payload.secrets else old["secrets_encrypted"]
        row = conn.execute(
            """UPDATE custom_tools SET name=%s,description=%s,input_schema=%s,python_code=%s,secrets_encrypted=%s,timeout_seconds=%s,enabled=%s,updated_at=now()
               WHERE id=%s AND tenant_id=%s RETURNING *""",
            (payload.name,payload.description,Json(payload.input_schema),payload.python_code,encrypted,payload.timeout_seconds,payload.enabled,tool_id,user["tenant_id"]),
        ).fetchone()
    return _serialize(row)


@router.delete("/{tool_id}")
def delete_tool(tool_id: str, user: dict = Depends(require("templates", "edit"))):
    if user["is_master"]:
        raise HTTPException(400, "Selecione uma empresa para editar Custom Tools")
    with get_connection() as conn:
        _owned(conn, tool_id, user["tenant_id"])
        conn.execute("DELETE FROM custom_tools WHERE id=%s AND tenant_id=%s", (tool_id, user["tenant_id"]))
    return {"status": "ok"}
