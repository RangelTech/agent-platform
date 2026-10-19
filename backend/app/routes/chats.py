"""Chat endpoints: listing, history and the streaming gateway to the kernel.

POST /api/chat/send proxies the kernel's SSE stream straight to the browser
and persists both sides of the exchange. Until the AI-services ticket lands,
the model comes from backend settings (stub by default, Gemini when a key is
configured).
"""

import asyncio
import json
import logging
import uuid

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import StreamingResponse
from psycopg.types.json import Json
from pydantic import BaseModel, Field, ValidationError, field_validator

from app import chat_runs

# Shared with app/routes/public_api.py (Chatwoot bridge path) so both callers
# use the exact same storage/transcription logic instead of duplicating it.
from app.attachments import store_uploads as _store_uploads
from app.attachments import transcription_spec as _transcription_spec
from app.auth import current_user
from app.config import settings
from app.db import get_connection
from app.kernel_router import (
    SWITCH_CONFLICT,
    chat_kernel_of,
    ensure_skills_enabled,
    kernel_headers,
    kind_of,
    kind_of_chat_kernel,
    target_for_kind,
    template_row,
)
from app.permissions import require_scope_for_chatwoot

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api", tags=["chats"])


class SendRequest(BaseModel):
    message: str = Field(min_length=1, max_length=32_000)
    chat_id: str | None = None
    # Pins/switches the conversation's template. Persisted on the chat.
    template_id: str | None = None

    @field_validator("message")
    @classmethod
    def _message_clean(cls, v: str) -> str:
        if "\x00" in v:
            raise ValueError("A mensagem contém caracteres inválidos")
        if not v.strip():
            raise ValueError("A mensagem está vazia")
        return v

    @field_validator("chat_id", "template_id")
    @classmethod
    def _uuid_or_none(cls, v: str | None) -> str | None:
        if v is None or v == "":
            return None
        try:
            return str(uuid.UUID(v))
        except ValueError as exc:
            raise ValueError("Identificador inválido") from exc


def _agent_kind(row: dict) -> str:
    """'structured' | 'skills' (o kernel gravado no chat manda; senão, o template)."""
    kernel = row.get("kernel")
    if kernel == "harness":
        return "skills"
    if kernel == "llm":
        return "structured"
    return "skills" if row.get("agent_kind") == "skills" else "structured"


def _serialize_chat(row: dict) -> dict:
    return {
        "id": str(row["id"]),
        "title": row["title"],
        "agent_kind": _agent_kind(row),
        "template_id": str(row["template_id"]) if row.get("template_id") else None,
        "channel": row.get("channel") or "web",
        "external_contact": row.get("external_contact"),
        "created_at": row["created_at"].isoformat(),
        "updated_at": row["updated_at"].isoformat(),
    }


def _need_uuid(value: str) -> None:
    try:
        uuid.UUID(value)
    except ValueError:
        raise HTTPException(status_code=404, detail="Conversa não encontrada") from None


@router.get("/chats")
def list_chats(user: dict = Depends(current_user)):
    require_scope_for_chatwoot(user, "ai.chat.view")
    with get_connection() as conn:
        rows = conn.execute(
            """SELECT c.*, t.agent_kind FROM chats c
                 LEFT JOIN templates t ON t.id = c.template_id
                WHERE NOT c.is_hidden
                  AND (c.user_id = %s
                       OR (c.channel <> 'web' AND c.tenant_id = %s))
                ORDER BY c.updated_at DESC""",
            (user["id"], user["tenant_id"]),
        ).fetchall()
    return [_serialize_chat(r) for r in rows]


@router.get("/chats/{chat_id}")
def get_chat(chat_id: str, user: dict = Depends(current_user)):
    require_scope_for_chatwoot(user, "ai.chat.view")
    _need_uuid(chat_id)
    with get_connection() as conn:
        row = conn.execute(
            """SELECT c.*, t.agent_kind FROM chats c
                 LEFT JOIN templates t ON t.id = c.template_id
                WHERE c.id::text = %s
                  AND (c.user_id = %s OR (c.channel <> 'web' AND c.tenant_id = %s))""",
            (chat_id, user["id"], user["tenant_id"]),
        ).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="Conversa não encontrada")
    return _serialize_chat(row)


@router.post("/chats/ragentes-guide")
def open_ragentes_guide(user: dict = Depends(current_user)):
    """Open one private chat pinned to the tenant's system onboarding guide."""
    require_scope_for_chatwoot(user, "ai.chat.run")
    if user["tenant_id"] is None:
        raise HTTPException(status_code=400, detail="Usuário master não participa de conversas")
    from app.ragentes_guide import ensure_for_tenant

    with get_connection() as conn:
        template_id = ensure_for_tenant(conn, user["tenant_id"])
        chat = conn.execute(
            """SELECT * FROM chats WHERE tenant_id=%s AND user_id=%s AND template_id=%s
               AND NOT is_hidden ORDER BY updated_at DESC LIMIT 1""",
            (user["tenant_id"], user["id"], template_id),
        ).fetchone()
        if chat is None:
            chat = conn.execute(
                """INSERT INTO chats (tenant_id, user_id, title, template_id)
                   VALUES (%s, %s, %s, %s) RETURNING *""",
                (user["tenant_id"], user["id"], "Assistente RAgentes", template_id),
            ).fetchone()
        conn.execute(
            """INSERT INTO tenant_guide_audit (tenant_id, user_id, chat_id, action)
               VALUES (%s, %s, %s, 'open')""",
            (user["tenant_id"], user["id"], chat["id"]),
        )
        ai_ready = conn.execute(
            """SELECT EXISTS(
                   SELECT 1 FROM ai_services WHERE tenant_id=%s AND NOT is_deleted
               ) AS ready""",
            (user["tenant_id"],),
        ).fetchone()["ready"]
    return {**_serialize_chat(chat), "ai_ready": ai_ready}


@router.get("/chats/{chat_id}/messages")
def list_messages(chat_id: str, user: dict = Depends(current_user)):
    require_scope_for_chatwoot(user, "ai.chat.view")
    _need_uuid(chat_id)
    with get_connection() as conn:
        # Conversas de canal externo (WhatsApp) não têm dono na plataforma:
        # quem enxerga é a empresa inteira, em modo leitura.
        chat = conn.execute(
            """SELECT id FROM chats
                WHERE id = %s
                  AND (user_id = %s OR (channel <> 'web' AND tenant_id = %s))""",
            (chat_id, user["id"], user["tenant_id"]),
        ).fetchone()
        if chat is None:
            raise HTTPException(status_code=404, detail="Conversa não encontrada")
        rows = conn.execute(
            """SELECT id, role, content, attachments, created_at FROM chat_messages
                WHERE chat_id = %s ORDER BY created_at""",
            (chat_id,),
        ).fetchall()
    return [
        {
            "id": str(r["id"]),
            "role": r["role"],
            "content": r["content"],
            "attachments": [
                {"kind": a.get("kind"), "name": a.get("name")} for a in (r["attachments"] or [])
            ],
            "created_at": r["created_at"].isoformat(),
        }
        for r in rows
    ]


@router.put("/chats/{chat_id}/hide")
def hide_chat(chat_id: str, user: dict = Depends(current_user)):
    require_scope_for_chatwoot(user, "ai.chat.run")
    with get_connection() as conn:
        row = conn.execute(
            "UPDATE chats SET is_hidden = TRUE WHERE id = %s AND user_id = %s RETURNING id",
            (chat_id, user["id"]),
        ).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="Conversa não encontrada")
    return {"status": "ok"}


def _ensure_chat(user: dict, payload: SendRequest, attachments: list[dict] | None = None) -> dict:
    attachments = attachments or []
    if user["tenant_id"] is None:
        # The master administers the platform; conversations belong to tenant
        # users. This also guarantees chats always carry a tenant_id.
        raise HTTPException(status_code=400, detail="Usuário master não participa de conversas")
    with get_connection() as conn:
        requested = None
        if payload.template_id:
            requested = conn.execute(
                """SELECT id, agent_kind FROM templates
                    WHERE id = %s AND tenant_id = %s AND NOT is_deleted""",
                (payload.template_id, user["tenant_id"]),
            ).fetchone()
            if requested is None:
                raise HTTPException(status_code=404, detail="Template não encontrado")
        if payload.chat_id:
            chat = conn.execute(
                "SELECT * FROM chats WHERE id = %s AND user_id = %s",
                (payload.chat_id, user["id"]),
            ).fetchone()
            if chat is None:
                raise HTTPException(status_code=404, detail="Conversa não encontrada")
            effective = requested
            if effective is None and chat["template_id"]:
                effective = template_row(conn, user["tenant_id"], chat["template_id"])
            kind = kind_of(effective["agent_kind"] if effective else None)
            pinned = kind_of_chat_kernel(chat["kernel"])
            # Uma conversa não troca de tipo de agente no meio: o histórico de
            # um não é conversível no outro.
            if pinned is not None and pinned != kind:
                raise HTTPException(status_code=409, detail=SWITCH_CONFLICT)
            if kind == "skills":
                ensure_skills_enabled(conn, user["tenant_id"])
                target_for_kind("skills")
            if payload.template_id and str(chat["template_id"] or "") != payload.template_id:
                chat = conn.execute(
                    "UPDATE chats SET template_id = %s WHERE id = %s RETURNING *",
                    (payload.template_id, chat["id"]),
                ).fetchone()
            if chat["kernel"] is None:
                chat = conn.execute(
                    "UPDATE chats SET kernel = %s WHERE id = %s RETURNING *",
                    (chat_kernel_of(kind), chat["id"]),
                ).fetchone()
        else:
            kind = kind_of(requested["agent_kind"] if requested else None)
            if kind == "skills":
                ensure_skills_enabled(conn, user["tenant_id"])
                target_for_kind("skills")
            title = payload.message[:60] + ("…" if len(payload.message) > 60 else "")
            chat = conn.execute(
                """INSERT INTO chats (tenant_id, user_id, title, template_id, kernel)
                   VALUES (%s, %s, %s, %s, %s) RETURNING *""",
                (
                    user["tenant_id"],
                    user["id"],
                    title,
                    payload.template_id,
                    chat_kernel_of(kind),
                ),
            ).fetchone()
        conn.execute(
            """INSERT INTO chat_messages (chat_id, role, content, attachments)
               VALUES (%s, 'user', %s, %s)""",
            (chat["id"], payload.message, Json(attachments)),
        )
        conn.execute("UPDATE chats SET updated_at = now() WHERE id = %s", (chat["id"],))
    return chat


def _sse(event: str, data: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


# Tarefas de fundo das runs de skills: referência forte para não serem coletadas
# enquanto a resposta ainda está sendo recebida sem cliente conectado.
_BACKGROUND: set[asyncio.Task] = set()


@router.post("/chat/runs/{run_id}/cancel")
async def cancel_run(run_id: str, user: dict = Depends(current_user)):
    require_scope_for_chatwoot(user, "ai.chat.run")
    with get_connection() as conn:
        run = conn.execute(
            """SELECT * FROM chat_runs
                WHERE run_id::text = %s AND tenant_id = %s AND user_id = %s""",
            (run_id, user["tenant_id"], user["id"]),
        ).fetchone()
        if run is None:
            raise HTTPException(status_code=404, detail="Execução não encontrada")
        if run["status"] not in ("running", "cancelling"):
            raise HTTPException(status_code=409, detail="Esta execução já terminou")
        conn.execute(
            "UPDATE chat_runs SET status = 'cancelling' WHERE run_id = %s", (run["run_id"],)
        )
    event = chat_runs.CANCEL_EVENTS.get(str(run["run_id"]))
    if event is not None:
        event.set()
    if run["kernel"] == "harness":
        try:
            target = target_for_kind("skills")
            async with httpx.AsyncClient(timeout=httpx.Timeout(10.0)) as client:
                await client.post(
                    f"{target.url.rstrip('/')}/v1/runs/{run['run_id']}/cancel",
                    headers=await kernel_headers(target),
                )
        except (httpx.HTTPError, HTTPException):
            logger.warning("cancel could not be forwarded for run %s", run["run_id"])
    return {"status": "cancelling"}


@router.post("/chat/send")
async def send_message(request: Request, user: dict = Depends(current_user)):
    require_scope_for_chatwoot(user, "ai.chat.run")
    # JSON for text-only sends; multipart when attachments ride along.
    content_type = request.headers.get("content-type", "")
    if content_type.startswith("multipart/"):
        form = await request.form()
        try:
            payload = SendRequest(
                message=str(form.get("message") or "").strip() or "(anexo enviado)",
                chat_id=str(form.get("chat_id") or "") or None,
                template_id=str(form.get("template_id") or "") or None,
            )
        except ValidationError as exc:
            msg = "; ".join(str(e["msg"]).replace("Value error, ", "") for e in exc.errors())
            raise HTTPException(status_code=422, detail=msg or "Mensagem inválida") from exc
        attachments = await _store_uploads(user, form.getlist("files"))
    else:
        try:
            body = await request.json()
        except ValueError as exc:
            # Corpo inválido é erro do cliente, não falha do servidor.
            raise HTTPException(status_code=400, detail="Corpo da requisição inválido") from exc
        try:
            payload = SendRequest(**body) if isinstance(body, dict) else None
        except ValidationError as exc:
            msg = "; ".join(str(e["msg"]).replace("Value error, ", "") for e in exc.errors())
            raise HTTPException(status_code=422, detail=msg or "Mensagem inválida") from exc
        if payload is None:
            raise HTTPException(status_code=400, detail="Corpo da requisição inválido")
        attachments = []

    chat = _ensure_chat(user, payload, attachments)
    chat_id = str(chat["id"])
    template_id = str(chat["template_id"]) if chat["template_id"] else None

    if chat["kernel"] == "harness":
        return await _send_to_skills_agent(
            user, chat_id, template_id, payload.message, attachments
        )

    from app.template_runtime import build_run_payload

    run = build_run_payload(user["tenant_id"], template_id, chat_id)
    kernel_payload = {
        "thread_id": chat_id,
        "message": payload.message,
        "attachments": attachments,
        "transcription": _transcription_spec(user["tenant_id"]) if attachments else {},
        "user_id": str(user["id"]),
        **run,
    }
    target = target_for_kind("graph")
    headers = await kernel_headers(target)
    run_id = chat_runs.new_run_id()
    chat_runs.register_run(
        run_id=run_id,
        tenant_id=user["tenant_id"],
        user_id=user["id"],
        chat_id=chat_id,
        template_id=template_id,
        kernel="llm",
    )
    cancelled = asyncio.Event()
    chat_runs.CANCEL_EVENTS[run_id] = cancelled

    async def relay():
        # chat_id first, so a brand-new conversation appears in the sidebar
        # before the first token arrives.
        yield f"event: chat\ndata: {json.dumps({'chat_id': chat_id, 'run_id': run_id})}\n\n"
        assistant_text = ""
        outcome = "done"
        try:
            async with httpx.AsyncClient(timeout=httpx.Timeout(310.0)) as client:
                async with client.stream(
                    "POST",
                    target.runs_url,
                    json=kernel_payload,
                    headers=headers,
                ) as response:
                    if response.status_code != 200:
                        detail = (await response.aread()).decode()[:500]
                        outcome = "error"
                        yield (f"event: error\ndata: {json.dumps({'detail': detail})}\n\n")
                        return
                    current_event = ""
                    async for line in response.aiter_lines():
                        # Pass the kernel's SSE through untouched; capture the
                        # `done` payload (the full reply) for persistence.
                        if line.startswith("event: "):
                            current_event = line[7:].strip()
                        elif line.startswith("data: ") and current_event == "done":
                            try:
                                assistant_text = json.loads(line[6:]).get("text", "")
                            except json.JSONDecodeError:
                                pass
                        yield line + "\n"
                        if cancelled.is_set():
                            outcome = "cancelled"
                            break
        except httpx.HTTPError as exc:
            logger.exception("kernel unreachable")
            outcome = "error"
            yield f"event: error\ndata: {json.dumps({'detail': str(exc)})}\n\n"
            return
        finally:
            chat_runs.finish_run(run_id, outcome)
        if outcome == "cancelled":
            return
        if assistant_text:
            with get_connection() as conn:
                conn.execute(
                    """INSERT INTO chat_messages (chat_id, role, content)
                       VALUES (%s, 'assistant', %s)""",
                    (chat_id, assistant_text),
                )
            # Fire post-conversation fact extraction (cheap model, async).
            try:
                from app.tasks import enqueue_kernel_task

                enqueue_kernel_task(
                    "/v1/extract-memories",
                    {
                        "thread_id": chat_id,
                        "tenant_id": str(user["tenant_id"]),
                        "user_id": str(user["id"]),
                        "model": run["supervisor"]["model"],
                        "embedding": run.get("embedding", {}),
                    },
                )
            except Exception:  # noqa: BLE001 — memory must never break the chat
                logger.exception("failed to enqueue memory extraction")

    return StreamingResponse(
        relay(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


async def _send_to_skills_agent(
    user: dict, chat_id: str, template_id: str | None, message: str, attachments: list[dict]
):
    """Run de um agente por skills.

    A leitura do kernel e a gravação da resposta acontecem numa tarefa de
    fundo: se o navegador fechar no meio, a resposta é gravada do mesmo jeito.
    O cliente recebe as linhas por uma fila; eventos desconhecidos passam
    direto (o front ignora o que não conhece).
    """
    from app.harness_runtime import prepare_harness_run

    if template_id is None:
        raise HTTPException(status_code=409, detail="Escolha um agente para esta conversa")
    target = target_for_kind("skills")
    payload = prepare_harness_run(
        tenant_id=user["tenant_id"],
        template_id=template_id,
        chat_id=chat_id,
        message=message,
        user=user,
        attachments=attachments,
        transcription=_transcription_spec(user["tenant_id"]) if attachments else None,
    )
    run_id = payload["run_id"]
    headers = await kernel_headers(target)
    queue: asyncio.Queue[str | None] = asyncio.Queue()

    async def producer():
        outcome = "error"
        assistant_text = ""
        try:
            timeout = httpx.Timeout(
                connect=10.0,
                read=float(settings.harness_run_timeout_seconds),
                write=30.0,
                pool=10.0,
            )
            async with httpx.AsyncClient(timeout=timeout) as client:
                async with client.stream(
                    "POST", target.runs_url, json=payload, headers=headers
                ) as response:
                    if response.status_code != 200:
                        logger.warning("skills agent refused the run: %s", response.status_code)
                        await queue.put(
                            _sse("error", {"detail": "O agente não está disponível no momento"})
                        )
                        return
                    current_event = ""
                    async for line in response.aiter_lines():
                        if line.startswith("event: "):
                            current_event = line[7:].strip()
                        elif line.startswith("data: "):
                            if current_event == "done":
                                try:
                                    assistant_text = json.loads(line[6:]).get("text", "")
                                    outcome = "done"
                                except json.JSONDecodeError:
                                    pass
                            elif current_event == "error":
                                outcome = "error"
                        await queue.put(line + "\n")
        except httpx.HTTPError:
            logger.exception("skills agent unreachable")
            await queue.put(_sse("error", {"detail": "O agente não está disponível no momento"}))
        finally:
            try:
                if assistant_text and outcome == "done":
                    chat_runs.save_assistant_message(run_id, chat_id, assistant_text)
                chat_runs.finish_run(run_id, outcome)
            except Exception:  # noqa: BLE001 — persistence failure must not hang the stream
                logger.exception("failed to close run %s", run_id)
            await queue.put(None)

    task = asyncio.create_task(producer())
    _BACKGROUND.add(task)
    task.add_done_callback(_BACKGROUND.discard)

    async def relay():
        yield _sse("chat", {"chat_id": chat_id, "run_id": run_id})
        while True:
            item = await queue.get()
            if item is None:
                return
            yield item

    return StreamingResponse(
        relay(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
