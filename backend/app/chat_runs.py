"""Registro de runs de chat: estado, cancelamento e gravação única da resposta."""

from __future__ import annotations

import asyncio
import time
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


def sweep_stale(minutes: int = 25) -> int:
    """Runs que ficaram `running` (reinício do kernel, queda) viram `error`: revoga o run_token."""
    with get_connection() as conn:
        rows = conn.execute(
            """UPDATE chat_runs SET status = 'error', finished_at = now()
                WHERE status IN ('running', 'cancelling')
                  AND created_at < now() - make_interval(mins => %s)
                RETURNING run_id""",
            (minutes,),
        ).fetchall()
    return len(rows)


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
        # Cartão sem resposta quando a run acaba (kernel expirou ou cancelou): não pode ficar
        # clicável, senão o usuário "aprova" algo que já terminou.
        conn.execute(
            "UPDATE approvals SET status = 'expired' WHERE run_id = %s AND status = 'pending'",
            (run_id,),
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


# --- corte por revogação (usuário removido / empresa suspensa) -------------------------------
REVOKED_MESSAGE = "Seu acesso foi removido"
SUSPENDED_MESSAGE = "O acesso desta empresa foi suspenso"
WATCH_INTERVAL = 5.0  # s; com o repasse do motor o corte fica bem abaixo de 30 s


class AccessRevoked(Exception):
    """O usuário perdeu o acesso (ou a empresa foi suspensa) com o stream aberto."""

    def __init__(self, message: str):
        super().__init__(message)
        self.message = message


def access_reason(tenant_id, user_id) -> str | None:
    """Mensagem pt-BR se o usuário não pode mais receber o stream; None se segue valendo."""
    with get_connection() as conn:
        row = conn.execute(
            """SELECT t.is_active AS tenant_active, u.is_active AS user_active
                 FROM tenants t LEFT JOIN users u ON u.id = %s AND u.tenant_id = t.id
                WHERE t.id = %s""",
            (user_id, tenant_id),
        ).fetchone()
    if row is None or not row["tenant_active"]:
        return SUSPENDED_MESSAGE
    if row["user_active"] is False:
        return REVOKED_MESSAGE
    return None


async def watch(source, tenant_id, user_id, interval: float = WATCH_INTERVAL):
    """Repassa `source` conferindo o acesso a cada `interval` s, mesmo sem chegar nada.

    Levanta AccessRevoked no corte. O item pendente de `source` não é cancelado a cada
    espera (cancelar um stream httpx no meio o corromperia).
    """
    iterator = source.__aiter__()
    pending = asyncio.ensure_future(iterator.__anext__())
    last = time.monotonic()
    try:
        while True:
            done, _ = await asyncio.wait({pending}, timeout=interval)
            if time.monotonic() - last >= interval:
                last = time.monotonic()
                reason = await asyncio.to_thread(access_reason, tenant_id, user_id)
                if reason:
                    raise AccessRevoked(reason)
            if not done:
                continue
            try:
                item = pending.result()
            except StopAsyncIteration:
                return
            yield item
            pending = asyncio.ensure_future(iterator.__anext__())
    finally:
        pending.cancel()
