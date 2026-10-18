"""Rotinas agendadas: o agente por skills é acordado no horário com a instrução salva.

O kernel cria/lista/pausa/apaga rotinas pela rota interna `/internal/runs/{run_id}/schedules`
(tenant, usuário e agente vêm da run, nunca do corpo). Um laço de fundo, com trava no banco
(`FOR UPDATE SKIP LOCKED`), dispara o que venceu: abre uma conversa do dono da rotina, roda o
agente (canal `schedule`: sem ninguém na tela) e grava a resposta.
"""

from __future__ import annotations

import asyncio
import json
import logging
from datetime import UTC, datetime
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import httpx
from croniter import croniter
from fastapi import HTTPException
from psycopg.types.json import Json

from app import chat_runs
from app.config import settings
from app.db import get_connection

logger = logging.getLogger(__name__)

TICK_SECONDS = 30
MAX_PER_USER = 20
MIN_INTERVAL_SECONDS = 15 * 60  # rotina mais frequente que isso é recusada
_BACKGROUND: set[asyncio.Task] = set()


def _zone(tz: str) -> ZoneInfo:
    try:
        return ZoneInfo(tz)
    except (ZoneInfoNotFoundError, ValueError, KeyError):
        raise HTTPException(status_code=400, detail="Fuso horário inválido") from None


def next_run(cron: str, tz: str, after: datetime | None = None) -> datetime:
    """Próximo disparo (UTC) depois de `after`, segundo o cron no fuso informado."""
    if not croniter.is_valid(cron):
        raise HTTPException(status_code=400, detail="Horário inválido")
    zone = _zone(tz)
    base = (after or datetime.now(UTC)).astimezone(zone)
    it = croniter(cron, base)
    first = it.get_next(datetime)
    second = it.get_next(datetime)
    if (second - first).total_seconds() < MIN_INTERVAL_SECONDS:
        raise HTTPException(
            status_code=400, detail="A rotina não pode rodar com intervalo menor que 15 minutos"
        )
    return first.astimezone(UTC)


def _row_out(r: dict) -> dict:
    return {
        "id": str(r["id"]),
        "name": r["name"],
        "cron": r["cron"],
        "timezone": r["timezone"],
        "instruction": r["instruction"],
        "status": r["status"],
        "next_run_at": r["next_run_at"].isoformat() if r.get("next_run_at") else None,
        "last_run_at": r["last_run_at"].isoformat() if r.get("last_run_at") else None,
        "last_status": r.get("last_status"),
        "run_count": r.get("run_count", 0),
        "template_id": str(r["template_id"]),
    }


# ------------------------------------------------------------------ operações
def op_create(run: dict, name: str, cron: str, tz: str, instruction: str) -> dict:
    nxt = next_run(cron, tz)
    with get_connection() as conn:
        n = conn.execute(
            "SELECT count(*) AS n FROM scheduled_jobs WHERE tenant_id = %s AND user_id = %s",
            (run["tenant_id"], run["user_id"]),
        ).fetchone()["n"]
        if n >= MAX_PER_USER:
            raise HTTPException(status_code=409, detail="Limite de rotinas por usuário atingido")
        row = conn.execute(
            """INSERT INTO scheduled_jobs (tenant_id, user_id, template_id, name, cron, timezone,
                                           instruction, next_run_at)
               VALUES (%s, %s, %s, %s, %s, %s, %s, %s) RETURNING *""",
            (run["tenant_id"], run["user_id"], run["template_id"], name[:80], cron, tz,
             instruction[:4000], nxt),
        ).fetchone()
    return _row_out(row)


def op_list(tenant_id, user_id) -> dict:
    with get_connection() as conn:
        rows = conn.execute(
            """SELECT * FROM scheduled_jobs WHERE tenant_id = %s AND user_id = %s
                ORDER BY created_at""",
            (tenant_id, user_id),
        ).fetchall()
    return {"schedules": [_row_out(r) for r in rows]}


def op_set_status(tenant_id, user_id, sid: str, status: str) -> dict:
    with get_connection() as conn:
        row = conn.execute(
            "SELECT * FROM scheduled_jobs WHERE id::text = %s AND tenant_id = %s AND user_id = %s",
            (sid, tenant_id, user_id),
        ).fetchone()
        if row is None:
            raise HTTPException(status_code=404, detail="Rotina não encontrada")
        nxt = next_run(row["cron"], row["timezone"]) if status == "active" else None
        row = conn.execute(
            """UPDATE scheduled_jobs SET status = %s, next_run_at = %s, updated_at = now()
                WHERE id = %s RETURNING *""",
            (status, nxt, row["id"]),
        ).fetchone()
    return _row_out(row)


def op_delete(tenant_id, user_id, sid: str) -> dict:
    with get_connection() as conn:
        row = conn.execute(
            """DELETE FROM scheduled_jobs WHERE id::text = %s AND tenant_id = %s AND user_id = %s
               RETURNING id""",
            (sid, tenant_id, user_id),
        ).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="Rotina não encontrada")
    return {"deleted": str(row["id"])}


def handle_kernel_op(run: dict, payload: dict) -> dict:
    """Operações pedidas pelo agente (ferramentas rt_schedule_*). Escopo: dono da run."""
    if not run.get("user_id") or not run.get("template_id"):
        raise HTTPException(status_code=400, detail="Rotinas exigem usuário e agente")
    op = payload.get("op")
    if op == "create":
        return op_create(
            run,
            str(payload.get("name") or ""),
            str(payload.get("cron") or ""),
            str(payload.get("timezone") or "America/Sao_Paulo"),
            str(payload.get("instruction") or ""),
        )
    if op == "list":
        return op_list(run["tenant_id"], run["user_id"])
    if op in ("pause", "resume"):
        return op_set_status(
            run["tenant_id"], run["user_id"], str(payload.get("id") or ""),
            "paused" if op == "pause" else "active",
        )
    if op == "delete":
        return op_delete(run["tenant_id"], run["user_id"], str(payload.get("id") or ""))
    raise HTTPException(status_code=400, detail="Operação inválida")


# --------------------------------------------------------------------- disparo
def _claim_due(limit: int = 20) -> list[dict]:
    """Pega as rotinas vencidas e já agenda o próximo horário (não dispara duas vezes)."""
    claimed = []
    with get_connection() as conn:
        rows = conn.execute(
            """SELECT * FROM scheduled_jobs
                WHERE status = 'active' AND next_run_at IS NOT NULL AND next_run_at <= now()
                ORDER BY next_run_at LIMIT %s FOR UPDATE SKIP LOCKED""",
            (limit,),
        ).fetchall()
        for r in rows:
            try:
                nxt = next_run(r["cron"], r["timezone"])
            except HTTPException:
                nxt = None
            conn.execute(
                """UPDATE scheduled_jobs SET next_run_at = %s, last_run_at = now(),
                          last_status = 'running', run_count = run_count + 1, updated_at = now()
                    WHERE id = %s""",
                (nxt, r["id"]),
            )
            claimed.append(r)
    return claimed


async def run_job(job: dict) -> None:
    """Abre a conversa da rotina e roda o agente (canal `schedule`)."""
    from app.harness_runtime import prepare_harness_run
    from app.kernel_router import kernel_headers, target_for_kind

    outcome = "error"
    run_id = None
    try:
        with get_connection() as conn:
            user = conn.execute(
                "SELECT id, name, tenant_id FROM users WHERE id = %s AND is_active",
                (job["user_id"],),
            ).fetchone()
            if user is None:
                raise RuntimeError("usuário da rotina inativo")
            stamp = datetime.now(ZoneInfo(job["timezone"])).strftime("%d/%m %H:%M")
            chat = conn.execute(
                """INSERT INTO chats (tenant_id, user_id, title, template_id, kernel)
                   VALUES (%s, %s, %s, %s, 'harness') RETURNING id""",
                (job["tenant_id"], job["user_id"], f"Rotina: {job['name']} ({stamp})",
                 job["template_id"]),
            ).fetchone()
            conn.execute(
                "INSERT INTO chat_messages (chat_id, role, content, attachments) "
                "VALUES (%s, 'user', %s, %s)",
                (chat["id"], job["instruction"], Json([])),
            )
        chat_id = str(chat["id"])
        payload = prepare_harness_run(
            tenant_id=job["tenant_id"], template_id=job["template_id"], chat_id=chat_id,
            message=job["instruction"], user=user, channel="schedule",
            actor={"type": "member", "name": user["name"], "role": "member"},
        )
        run_id = payload["run_id"]
        target = target_for_kind("skills")
        headers = await kernel_headers(target)
        text = ""
        timeout = httpx.Timeout(connect=10.0, read=float(settings.harness_run_timeout_seconds),
                                write=30.0, pool=10.0)
        async with httpx.AsyncClient(timeout=timeout) as client:
            async with client.stream("POST", target.runs_url, json=payload, headers=headers) as r:
                if r.status_code != 200:
                    raise RuntimeError(f"kernel recusou a rotina: {r.status_code}")
                ev = ""
                async for line in r.aiter_lines():
                    if line.startswith("event: "):
                        ev = line[7:].strip()
                    elif line.startswith("data: ") and ev == "done":
                        text = json.loads(line[6:]).get("text", "")
                        outcome = "done"
        if text and outcome == "done":
            chat_runs.save_assistant_message(run_id, chat_id, text)
        with get_connection() as conn:
            conn.execute("UPDATE scheduled_jobs SET last_chat_id = %s WHERE id = %s",
                         (chat_id, job["id"]))
    except Exception:  # noqa: BLE001
        logger.exception("rotina %s falhou", job.get("id"))
    finally:
        if run_id:
            try:
                chat_runs.finish_run(run_id, outcome)
            except Exception:  # noqa: BLE001
                logger.exception("falha ao fechar a run da rotina %s", run_id)
        with get_connection() as conn:
            conn.execute(
                "UPDATE scheduled_jobs SET last_status = %s, updated_at = now() WHERE id = %s",
                ("ok" if outcome == "done" else "error", job["id"]),
            )


async def tick() -> int:
    due = await asyncio.to_thread(_claim_due)
    for job in due:
        task = asyncio.create_task(run_job(job))
        _BACKGROUND.add(task)
        task.add_done_callback(_BACKGROUND.discard)
    return len(due)


async def loop() -> None:
    while True:
        try:
            await tick()
        except Exception:  # noqa: BLE001
            logger.exception("tick do agendador falhou")
        await asyncio.sleep(TICK_SECONDS)
