"""Extração de memórias de longo prazo, no motor.

Lê as mensagens novas da conversa em `chat_messages` (não mais o checkpoint do LangGraph),
pede os fatos ao kernel de skills (`/v1/memories/extract`), gera os embeddings e grava em
`memories` com deduplicação por similaridade. A marca de leitura segue em
`memory_extraction_state` (mesma tabela de antes: conversas antigas não são reprocessadas).
"""

from __future__ import annotations

import logging

import httpx

from app.db import get_connection
from app.embeddings import embed_texts
from app.kernel_router import kernel_headers, target_for_kind

logger = logging.getLogger(__name__)

DEDUPE_SIMILARITY = 0.92
WINDOW = 8


def _novas(chat_id: str) -> tuple[list[dict], int]:
    with get_connection() as conn:
        row = conn.execute(
            "SELECT messages_read FROM memory_extraction_state WHERE thread_id = %s", (chat_id,)
        ).fetchone()
        lidas = int(row["messages_read"]) if row else 0
        msgs = conn.execute(
            """SELECT role, content FROM chat_messages
                WHERE chat_id::text = %s ORDER BY created_at, id""",
            (chat_id,),
        ).fetchall()
    return [dict(m) for m in msgs[lidas:]], len(msgs)


def _marcar(chat_id: str, total: int) -> None:
    with get_connection() as conn:
        conn.execute(
            """INSERT INTO memory_extraction_state (thread_id, messages_read)
               VALUES (%s, %s)
               ON CONFLICT (thread_id) DO UPDATE
                   SET messages_read = GREATEST(memory_extraction_state.messages_read,
                                                EXCLUDED.messages_read),
                       updated_at = now()""",
            (chat_id, total),
        )


def _existentes(tenant_id, user_id) -> list[str]:
    with get_connection() as conn:
        rows = conn.execute(
            """SELECT content FROM memories WHERE tenant_id = %s AND user_id = %s
                ORDER BY created_at DESC LIMIT 200""",
            (tenant_id, user_id),
        ).fetchall()
    return [r["content"] for r in rows]


async def _facts(tenant_id, messages: list[dict], existing: list[str], model: str) -> list[str]:
    target = target_for_kind("skills")
    async with httpx.AsyncClient(timeout=90.0) as client:
        r = await client.post(
            f"{target.url.rstrip('/')}/v1/memories/extract",
            headers=await kernel_headers(target),
            json={
                "tenant_id": str(tenant_id),
                "messages": messages,
                "existing": existing,
                "model": {"alias": model},
            },
        )
    r.raise_for_status()
    return [f for f in r.json().get("facts", []) if isinstance(f, str) and f.strip()]


def _salvar(tenant_id, user_id, chat_id, facts: list[str], vectors: list[list[float]]) -> int:
    saved = 0
    with get_connection() as conn:
        for fact, vector in zip(facts, vectors, strict=True):
            dup = conn.execute(
                """SELECT 1 FROM memories
                    WHERE tenant_id = %s AND user_id = %s
                      AND 1 - (embedding <=> %s::vector) > %s LIMIT 1""",
                (tenant_id, user_id, str(vector), DEDUPE_SIMILARITY),
            ).fetchone()
            if dup:
                continue
            conn.execute(
                """INSERT INTO memories (tenant_id, user_id, content, embedding, source_chat)
                   VALUES (%s, %s, %s, %s, %s)""",
                (tenant_id, user_id, fact.strip(), str(vector), chat_id),
            )
            saved += 1
    return saved


async def extract_memories(
    *, chat_id: str, tenant_id, user_id, embedding: dict, model: str = ""
) -> int:
    """Quantas memórias novas foram gravadas. Nunca levanta: memória não derruba conversa."""
    try:
        novas, total = _novas(chat_id)
        if not novas:
            return 0
        facts = await _facts(tenant_id, novas[-WINDOW:], _existentes(tenant_id, user_id), model)
        if not facts:
            _marcar(chat_id, total)
            return 0
        vectors = await embed_texts(embedding or {"provider": "stub"}, facts)
        saved = _salvar(tenant_id, user_id, chat_id, facts, vectors)
        _marcar(chat_id, total)
        return saved
    except Exception:  # noqa: BLE001
        logger.exception("extração de memórias falhou (chat %s)", chat_id)
        return 0
