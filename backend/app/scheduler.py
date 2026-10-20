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
        "template_name": r.get("template_name"),
        "last_chat_id": str(r["last_chat_id"]) if r.get("last_chat_id") else None,
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
            """SELECT j.*, t.name AS template_name FROM scheduled_jobs j
                 LEFT JOIN templates t ON t.id = j.template_id
                WHERE j.tenant_id = %s AND j.user_id = %s ORDER BY j.created_at""",
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


def cron_from_frequency(freq: dict) -> str:
    """Frequência amigável (a tela) -> cron.

    `kind`: hourly (minute) | daily (time) | weekly (time, weekdays 0=domingo..6) |
    monthly (time, day 1..28).
    """
    if not isinstance(freq, dict):
        raise HTTPException(status_code=400, detail="Frequência inválida")
    kind = freq.get("kind")

    def _time() -> tuple[int, int]:
        raw = str(freq.get("time") or "")
        try:
            hh, mm = raw.split(":")
            hour, minute = int(hh), int(mm)
        except ValueError:
            raise HTTPException(status_code=400, detail="Horário inválido") from None
        if not (0 <= hour <= 23 and 0 <= minute <= 59):
            raise HTTPException(status_code=400, detail="Horário inválido")
        return hour, minute

    if kind == "hourly":
        minute = freq.get("minute", 0)
        if not isinstance(minute, int) or not 0 <= minute <= 59:
            raise HTTPException(status_code=400, detail="Minuto inválido")
        return f"{minute} * * * *"
    if kind not in ("daily", "weekly", "monthly"):
        raise HTTPException(status_code=400, detail="Frequência inválida")
    hour, minute = _time()
    if kind == "daily":
        return f"{minute} {hour} * * *"
    if kind == "weekly":
        days = freq.get("weekdays")
        if (
            not isinstance(days, list)
            or not days
            or not all(isinstance(d, int) and 0 <= d <= 6 for d in days)
        ):
            raise HTTPException(status_code=400, detail="Escolha pelo menos um dia da semana")
        return f"{minute} {hour} * * {','.join(str(d) for d in sorted(set(days)))}"
    day = freq.get("day")
    if not isinstance(day, int) or not 1 <= day <= 28:
        raise HTTPException(status_code=400, detail="Dia do mês deve ficar entre 1 e 28")
    return f"{minute} {hour} {day} * *"


def _with_template_name(row: dict) -> dict:
    """`row` pode ser a linha do banco ou a saída de `_row_out`."""
    out = row if isinstance(row.get("id"), str) else _row_out(row)
    with get_connection() as conn:
        t = conn.execute(
            "SELECT name FROM templates WHERE id::text = %s", (str(out["template_id"]),)
        ).fetchone()
    out["template_name"] = t["name"] if t else None
    return out


def _template_ok(conn, tenant_id, template_id) -> None:
    row = conn.execute(
        """SELECT 1 FROM templates
            WHERE id::text = %s AND tenant_id = %s AND agent_kind = 'skills' AND NOT is_deleted
              AND active_version_id IS NOT NULL""",
        (str(template_id), tenant_id),
    ).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="Agente não encontrado ou sem versão publicada")


def op_create_from_ui(tenant_id, user_id, body: dict) -> dict:
    """Rotina criada pela tela (o agente cria pela ferramenta rt_schedule)."""
    name = str(body.get("name") or "").strip()
    instruction = str(body.get("instruction") or "").strip()
    if not name or not instruction:
        raise HTTPException(status_code=400, detail="Informe o nome e a instrução da rotina")
    cron = str(body.get("cron") or "").strip()
    if not cron:
        cron = cron_from_frequency(body.get("frequency") or {})
    tz = str(body.get("timezone") or "America/Sao_Paulo")
    template_id = body.get("template_id")
    if not template_id:
        raise HTTPException(status_code=400, detail="Escolha o agente da rotina")
    with get_connection() as conn:
        _template_ok(conn, tenant_id, template_id)
    row = op_create(
        {"tenant_id": tenant_id, "user_id": user_id, "template_id": template_id},
        name,
        cron,
        tz,
        instruction,
    )
    if body.get("active") is False:
        row = op_set_status(tenant_id, user_id, row["id"], "paused")
    return _with_template_name(row)


def op_update(tenant_id, user_id, sid: str, body: dict) -> dict:
    with get_connection() as conn:
        row = conn.execute(
            "SELECT * FROM scheduled_jobs WHERE id::text = %s AND tenant_id = %s AND user_id = %s",
            (sid, tenant_id, user_id),
        ).fetchone()
        if row is None:
            raise HTTPException(status_code=404, detail="Rotina não encontrada")
        name = str(body.get("name", row["name"]) or "").strip()
        instruction = str(body.get("instruction", row["instruction"]) or "").strip()
        if not name or not instruction:
            raise HTTPException(status_code=400, detail="Informe o nome e a instrução da rotina")
        cron = str(body.get("cron") or "").strip()
        if not cron and body.get("frequency"):
            cron = cron_from_frequency(body["frequency"])
        cron = cron or row["cron"]
        tz = str(body.get("timezone") or row["timezone"])
        template_id = body.get("template_id") or row["template_id"]
        if str(template_id) != str(row["template_id"]):
            _template_ok(conn, tenant_id, template_id)
        status = row["status"]
        if body.get("active") is True:
            status = "active"
        elif body.get("active") is False:
            status = "paused"
        nxt = next_run(cron, tz) if status == "active" else None
        row = conn.execute(
            """UPDATE scheduled_jobs
                  SET name = %s, instruction = %s, cron = %s, timezone = %s, template_id = %s,
                      status = %s, next_run_at = %s, updated_at = now()
                WHERE id = %s RETURNING *""",
            (name[:80], instruction[:4000], cron, tz, template_id, status, nxt, row["id"]),
        ).fetchone()
    return _with_template_name(row)


def op_runs(tenant_id, user_id, sid: str, limit: int = 20) -> dict:
    with get_connection() as conn:
        job = conn.execute(
            "SELECT id FROM scheduled_jobs WHERE id::text = %s AND tenant_id = %s AND user_id = %s",
            (sid, tenant_id, user_id),
        ).fetchone()
        if job is None:
            raise HTTPException(status_code=404, detail="Rotina não encontrada")
        rows = conn.execute(
            """SELECT * FROM scheduled_job_runs WHERE job_id = %s
                ORDER BY started_at DESC LIMIT %s""",
            (job["id"], limit),
        ).fetchall()
    return {
        "runs": [
            {
                "id": str(r["id"]),
                "started_at": r["started_at"].isoformat(),
                "finished_at": r["finished_at"].isoformat() if r["finished_at"] else None,
                "status": r["status"],
                "chat_id": str(r["chat_id"]) if r["chat_id"] else None,
            }
            for r in rows
        ]
    }


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
            started = conn.execute(
                "INSERT INTO scheduled_job_runs (job_id, tenant_id) VALUES (%s, %s) RETURNING id",
                (r["id"], r["tenant_id"]),
            ).fetchone()
            claimed.append({**r, "history_id": started["id"]})
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
            conn.execute("UPDATE scheduled_job_runs SET chat_id = %s WHERE id = %s",
                         (chat_id, job.get("history_id")))
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
            conn.execute(
                "UPDATE scheduled_job_runs SET status = %s, finished_at = now() WHERE id = %s",
                ("ok" if outcome == "done" else "error", job.get("history_id")),
            )


async def tick() -> int:
    due = await asyncio.to_thread(_claim_due)
    for job in due:
        task = asyncio.create_task(run_job(job))
        _BACKGROUND.add(task)
        task.add_done_callback(_BACKGROUND.discard)
    return len(due)


async def loop() -> None:
    n = 0
    while True:
        try:
            await tick()
            n += 1
            if n % 20 == 1:  # a cada ~10 min
                swept = await asyncio.to_thread(chat_runs.sweep_stale)
                if swept:
                    logger.info("runs presas encerradas: %s", swept)
        except Exception:  # noqa: BLE001
            logger.exception("tick do agendador falhou")
        await asyncio.sleep(TICK_SECONDS)
