"""Ingestão de arquivos do RAG: extrai texto, fatia, gera embeddings e grava os trechos.

Roda no próprio motor (BackgroundTasks) e escreve o andamento em `files.status`,
como o kernel-llm fazia. As tabelas `files`/`file_chunks` não mudaram: arquivos já
ingeridos continuam válidos e os embeddings novos são compatíveis com os antigos
(mesmo modelo, 768 dimensões).
"""

from __future__ import annotations

import asyncio
import io
import logging

from app.artifacts_io import load_bytes
from app.db import get_connection
from app.embeddings import embed_texts

logger = logging.getLogger(__name__)

CHUNK_CHARS = 1200
CHUNK_OVERLAP = 150


def extract_text(data: bytes, content_type: str, name: str) -> str:
    lower = name.lower()
    if lower.endswith(".pdf") or content_type == "application/pdf":
        import fitz  # pymupdf

        with fitz.open(stream=data, filetype="pdf") as doc:
            return "\n\n".join(page.get_text() for page in doc)
    if lower.endswith(".docx"):
        from docx import Document

        return "\n".join(p.text for p in Document(io.BytesIO(data)).paragraphs)
    if lower.endswith((".xlsx", ".xlsm")):
        from openpyxl import load_workbook

        workbook = load_workbook(io.BytesIO(data), read_only=True, data_only=True)
        parts = []
        for sheet in workbook.worksheets:
            for row in sheet.iter_rows(values_only=True):
                cells = [str(c) for c in row if c is not None]
                if cells:
                    parts.append(" | ".join(cells))
        return "\n".join(parts)
    return data.decode("utf-8", errors="replace")


def chunk_text(text: str) -> list[str]:
    text = " ".join(text.split())
    if not text:
        return []
    chunks, start = [], 0
    while start < len(text):
        end = start + CHUNK_CHARS
        chunks.append(text[start:end])
        if end >= len(text):
            break
        start = end - CHUNK_OVERLAP
    return chunks


def _set_status(file_id: str, status: str, *, error: str = "", chunks: int | None = None) -> None:
    with get_connection() as conn:
        conn.execute(
            """UPDATE files SET status = %s, error_detail = %s,
                      chunk_count = COALESCE(%s, chunk_count), updated_at = now()
                WHERE id = %s""",
            (status, error or None, chunks, file_id),
        )


def _store_chunks(file: dict, chunks: list[str], vectors: list[list[float]]) -> None:
    with get_connection() as conn:
        conn.execute("DELETE FROM file_chunks WHERE file_id = %s", (file["id"],))
        for index, (content, vector) in enumerate(zip(chunks, vectors, strict=True)):
            conn.execute(
                """INSERT INTO file_chunks (file_id, tenant_id, chunk_index, content, embedding)
                   VALUES (%s, %s, %s, %s, %s)""",
                (file["id"], file["tenant_id"], index, content, str(vector)),
            )


async def ingest_file(file_id: str, embedding_spec: dict) -> None:
    """Nunca levanta: o resultado (ready/error) fica em `files`."""
    with get_connection() as conn:
        file = conn.execute("SELECT * FROM files WHERE id = %s", (file_id,)).fetchone()
    if file is None:
        logger.warning("ingest: arquivo %s não encontrado", file_id)
        return
    _set_status(file_id, "processing")
    try:
        data = await asyncio.to_thread(load_bytes, file["storage_path"])
        text = await asyncio.to_thread(extract_text, data, file["content_type"], file["name"])
        chunks = chunk_text(text)
        if not chunks:
            raise ValueError("arquivo sem texto extraível")
        vectors = await embed_texts(embedding_spec or {"provider": "stub"}, chunks)
        await asyncio.to_thread(_store_chunks, file, chunks, vectors)
        _set_status(file_id, "ready", chunks=len(chunks))
        logger.info("arquivo %s ingerido: %d trechos", file_id, len(chunks))
    except Exception as exc:  # noqa: BLE001 - o status precisa refletir a falha
        logger.exception("ingestão falhou para %s", file_id)
        _set_status(file_id, "error", error=str(exc)[:500])
