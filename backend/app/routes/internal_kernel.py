"""Rotas internas que o kernel de skills usa para ferramentas: artefatos, cobranças Pix,
busca em documentos e memória. Mesma autenticação de `internal.py` (token de serviço ou
`run_token` da run); tenant, usuário e chat vêm sempre da linha de `chat_runs`.
"""

from __future__ import annotations

import base64
import binascii
import re
import uuid

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from psycopg.types.json import Json
from pydantic import BaseModel, Field

from app.db import get_connection
from app.routes.internal import _bearer, _is_service, get_run, run_auth
from app.run_tokens import verify_run_token
from app.storage import save_bytes

router = APIRouter(prefix="/internal", tags=["internal"])

MAX_ARTIFACT_BYTES = 25 * 1024 * 1024
_EXT = re.compile(r"^[A-Za-z0-9]{1,8}$")
_KIND_MAP = {"html": "file", "document": "document"}

# Status do Mercado Pago -> status interno (mesmo mapa de routes/payments.py).
_STATUS_MAP = {
    "approved": "paid",
    "authorized": "pending",
    "in_process": "pending",
    "in_mediation": "pending",
    "pending": "pending",
    "rejected": "failed",
    "cancelled": "cancelled",
    "refunded": "refunded",
    "charged_back": "refunded",
}


def _run_from_token(request: Request) -> dict:
    """Rotas sem run_id no caminho: a run vem do run_token."""
    token = _bearer(request)
    if _is_service(token):
        raise HTTPException(status_code=401, detail="Informe a execução (run_token)")
    claims = verify_run_token(token)
    if claims is None:
        raise HTTPException(status_code=401, detail="Não autorizado")
    run = get_run(str(claims.get("run_id")))
    if run is None or run["status"] not in ("running", "cancelling"):
        raise HTTPException(status_code=401, detail="Execução encerrada")
    return run


# ------------------------------------------------------------- artefatos ---


class ArtifactPut(BaseModel):
    kind: str = Field(default="file", max_length=40)
    title: str = Field(default="", max_length=500)
    extension: str = Field(default="bin", max_length=8)
    content_type: str = Field(default="application/octet-stream", max_length=200)
    content_base64: str = Field(min_length=1)
    columns: dict | list | None = None
    preview_json: dict | list | None = None
    row_count: int | None = Field(default=None, ge=0)
    agent_name: str = Field(default="agente", max_length=200)


@router.post("/runs/{run_id}/artifacts/put", status_code=201)
def put_artifact(run_id: str, payload: ArtifactPut, run: dict = Depends(run_auth)):
    """Recebe o arquivo, guarda no armazenamento da empresa e registra o artefato."""
    if not _EXT.match(payload.extension):
        raise HTTPException(status_code=400, detail="Extensão inválida")
    try:
        data = base64.b64decode(payload.content_base64, validate=True)
    except (binascii.Error, ValueError):
        raise HTTPException(status_code=400, detail="Conteúdo inválido") from None
    if not data:
        raise HTTPException(status_code=400, detail="Conteúdo vazio")
    if len(data) > MAX_ARTIFACT_BYTES:
        raise HTTPException(status_code=413, detail="Arquivo grande demais")
    tenant = str(run["tenant_id"])
    artifact_id = str(uuid.uuid4())
    kind = _KIND_MAP.get(payload.kind, payload.kind)
    if kind not in ("dataset", "chart", "document", "file", "image"):
        kind = "file"
    rel = f"tenants/{tenant}/artifacts/{artifact_id}.{payload.extension.lower()}"
    path = save_bytes(rel, data, payload.content_type)
    with get_connection() as conn:
        conn.execute(
            """INSERT INTO artifacts
                   (id, tenant_id, chat_id, agent_name, kind, title, schema_json, preview_json,
                    row_count, storage_path, content_type)
               VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)""",
            (
                artifact_id,
                run["tenant_id"],
                run["chat_id"],
                payload.agent_name,
                kind,
                payload.title,
                Json(payload.columns) if payload.columns is not None else None,
                Json(payload.preview_json) if payload.preview_json is not None else None,
                payload.row_count,
                path,
                payload.content_type,
            ),
        )
    return {"artifact_id": artifact_id}


# --------------------------------------------------------------- pagamentos ---


class ChargeIn(BaseModel):
    payment_id: str = Field(min_length=1, max_length=100)
    reference_id: str = Field(default="", max_length=200)
    amount: str = Field(min_length=1, max_length=30)
    status: str | None = Field(default=None, max_length=40)
    description: str = Field(default="", max_length=500)
    copy_paste: str | None = None


@router.post("/runs/{run_id}/payments", status_code=201)
def record_charge(run_id: str, payload: ChargeIn, run: dict = Depends(run_auth)):
    try:
        amount = round(float(payload.amount), 2)
    except ValueError:
        raise HTTPException(status_code=400, detail="Valor inválido") from None
    status = _STATUS_MAP.get(payload.status or "pending", "pending")
    chat_uuid = None
    try:
        chat_uuid = str(uuid.UUID(str(run["chat_id"])))
    except ValueError:
        pass
    with get_connection() as conn:
        sandbox = conn.execute(
            "SELECT sandbox FROM payment_credentials "
            "WHERE tenant_id = %s AND provider = 'mercado_pago'",
            (run["tenant_id"],),
        ).fetchone()
        conn.execute(
            """INSERT INTO payment_charges
                   (tenant_id, provider, external_id, chat_id, amount, description, reference_id,
                    status, qr_code, sandbox, raw)
               VALUES (%s, 'mercado_pago', %s, %s, %s, %s, %s, %s, %s, %s, %s)
               ON CONFLICT (provider, external_id) DO UPDATE
                  SET status = EXCLUDED.status, updated_at = now()""",
            (
                run["tenant_id"],
                payload.payment_id,
                chat_uuid,
                amount,
                payload.description,
                payload.reference_id,
                status,
                payload.copy_paste,
                bool(sandbox["sandbox"]) if sandbox else True,
                Json({"run_id": str(run["run_id"]), "user_id": str(run["user_id"] or "")}),
            ),
        )
    return {"ok": True}


@router.get("/payments/charges")
def lookup_charge(
    request: Request,
    reference_id: str = Query(min_length=1),
    run_id: str = Query(min_length=1),
):
    run = run_auth(run_id, request)
    with get_connection() as conn:
        row = conn.execute(
            """SELECT external_id, amount, status, reference_id FROM payment_charges
                WHERE tenant_id = %s AND reference_id = %s ORDER BY created_at DESC LIMIT 1""",
            (run["tenant_id"], reference_id),
        ).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="Cobrança não encontrada")
    # status no vocabulário do Mercado Pago, que é o que o kernel converte
    inv = {"paid": "approved", "failed": "rejected", "refunded": "refunded"}
    return {
        "payment_id": row["external_id"],
        "amount": str(row["amount"]),
        "status": inv.get(row["status"], row["status"]),
        "reference_id": row["reference_id"],
    }


# --------------------------------------------------------------- documentos ---


class DocsQuery(BaseModel):
    query: str = Field(min_length=1, max_length=500)
    top_k: int = Field(default=5, ge=1, le=10)


def _terms(text: str) -> list[str]:
    seen: list[str] = []
    for w in re.findall(r"[\wÀ-ÿ]{3,}", text.lower()):
        if w not in seen:
            seen.append(w)
    return seen[:8]


@router.post("/search/docs")
def search_docs(payload: DocsQuery, request: Request):
    """Busca lexical nos arquivos da empresa da run (trechos como dados, nunca instruções)."""
    run = _run_from_token(request)
    terms = _terms(payload.query)
    if not terms:
        return {"results": []}
    patterns = [f"%{t}%" for t in terms]
    with get_connection() as conn:
        rows = conn.execute(
            """SELECT f.name, c.content FROM file_chunks c JOIN files f ON f.id = c.file_id
                WHERE c.tenant_id = %s AND f.status = 'ready' AND c.content ILIKE ANY(%s)
                LIMIT 60""",
            (run["tenant_id"], patterns),
        ).fetchall()
    scored = sorted(
        rows, key=lambda r: -sum(1 for t in terms if t in (r["content"] or "").lower())
    )
    return {
        "results": [
            {"file": r["name"], "text": (r["content"] or "")[:1500]}
            for r in scored[: payload.top_k]
        ]
    }


# ------------------------------------------------------------------ memória ---


class RecallIn(BaseModel):
    query: str = Field(default="", max_length=500)
    top_k: int = Field(default=5, ge=1, le=10)


@router.post("/memories/recall")
def recall_memories(payload: RecallIn, request: Request):
    run = _run_from_token(request)
    if not run["user_id"]:
        return {"memories": []}
    terms = _terms(payload.query)
    with get_connection() as conn:
        rows = conn.execute(
            """SELECT content FROM memories WHERE tenant_id = %s AND user_id = %s
               UNION ALL
               SELECT content FROM memory_proposals
                WHERE tenant_id = %s AND user_id = %s AND status = 'approved'
               ORDER BY 1 LIMIT 100""",
            (run["tenant_id"], run["user_id"], run["tenant_id"], run["user_id"]),
        ).fetchall()
    texts = [r["content"] for r in rows if r["content"]]
    if terms:
        texts.sort(key=lambda t: -sum(1 for w in terms if w in t.lower()))
    return {"memories": [{"text": t} for t in texts[: payload.top_k]]}


class MemoryProposalIn(BaseModel):
    text: str = Field(min_length=1, max_length=2000)
    status: str = Field(default="pending_review", max_length=30)


@router.post("/runs/{run_id}/memory-proposals", status_code=201)
def post_memory_proposal(run_id: str, payload: MemoryProposalIn, run: dict = Depends(run_auth)):
    if not run["user_id"]:
        raise HTTPException(status_code=400, detail="Memória exige usuário")
    with get_connection() as conn:
        n = conn.execute(
            """SELECT count(*) AS n FROM memory_proposals
                WHERE tenant_id = %s AND user_id = %s AND status = 'pending_review'""",
            (run["tenant_id"], run["user_id"]),
        ).fetchone()["n"]
        if n >= 20:
            raise HTTPException(status_code=409, detail="Muitas memórias aguardando revisão")
        row = conn.execute(
            """INSERT INTO memory_proposals (tenant_id, user_id, chat_id, content)
               VALUES (%s, %s, %s, %s) RETURNING id""",
            (run["tenant_id"], run["user_id"], str(run["chat_id"]), payload.text),
        ).fetchone()
    return {"id": str(row["id"]), "status": "pending_review"}
