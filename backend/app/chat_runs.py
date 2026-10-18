"""Registro de runs de chat: estado, cancelamento e gravação única da resposta."""

from __future__ import annotations

import asyncio
import uuid

from app.db import get_connection

# Cancelamento cooperativo dentro do processo (runs do fluxo estruturado, que
# não têm rota de cancelamento no kernel). Chave: run_id.
CANCEL_EVENTS: dict[str, asyncio.Event] = {}


def register_run(
    *, run_id: str, tenant_id, user_id, chat_id: str, template_id, kernel: str
) -> None:
    with get_connection() as conn:
        conn.execute(
            """INSERT INTO chat_runs (run_id, tenant_id, user_id, chat_id, template_id, kernel)
               VALUES (%s, %s, %s, %s, %s, %s) ON CONFLICT (run_id) DO NOTHING""",
            (run_id, tenant_id, user_id, chat_id, template_id, kernel),
        )


def new_run_id() -> str:
    return str(uuid.uuid4())


def finish_run(run_id: str, status: str) -> None:
    """Fecha a run (revoga o run_token). Não rebaixa uma run já encerrada."""
    with get_connection() as conn:
        conn.execute(
            """UPDATE chat_runs
                  SET status = CASE WHEN status = 'cancelling' AND %s = 'done'
                                    THEN 'cancelled' ELSE %s END,
                      finished_at = now()
                WHERE run_id = %s AND status IN ('running', 'cancelling')""",
            (status, status, run_id),
        )
    CANCEL_EVENTS.pop(run_id, None)


def save_assistant_message(run_id: str, chat_id: str, text: str, attachments=None) -> str | None:
    """Grava a resposta uma única vez por run. Devolve o id ou None se já gravada."""
    from psycopg.types.json import Json

    if not text:
        return None
    try:
        uuid.UUID(chat_id)
    except ValueError:
        return None  # conversas de canal externo (ext-...) não vivem em chats
    with get_connection() as conn:
        claimed = conn.execute(
            """UPDATE chat_runs SET assistant_saved = true
                WHERE run_id = %s AND NOT assistant_saved RETURNING run_id""",
            (run_id,),
        ).fetchone()
        if claimed is None:
            return None
        row = conn.execute(
            """INSERT INTO chat_messages (chat_id, role, content, attachments)
               VALUES (%s, 'assistant', %s, %s) RETURNING id""",
            (chat_id, text, Json(attachments or [])),
        ).fetchone()
        conn.execute("UPDATE chats SET updated_at = now() WHERE id::text = %s", (chat_id,))
    return str(row["id"])
