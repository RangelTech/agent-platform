"""Tenant usage reporting and per-message feedback."""

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from app.auth import current_user, require
from app.db import get_connection

router = APIRouter(prefix="/api", tags=["usage"])


@router.get("/usage")
def usage_summary(
    days: int = Query(default=30, ge=1, le=365),
    user: dict = Depends(require("usage", "view")),
):
    scope = "" if user["is_master"] else " AND tenant_id = %s"
    params: tuple = (days,) if user["is_master"] else (days, user["tenant_id"])
    with get_connection() as conn:
        totals = conn.execute(
            f"""SELECT count(*) AS calls,
                       COALESCE(sum(prompt_tokens), 0) AS prompt_tokens,
                       COALESCE(sum(completion_tokens), 0) AS completion_tokens,
                       COALESCE(sum(cost_usd), 0) AS cost_usd,
                       COALESCE(avg(latency_ms), 0) AS avg_latency_ms
                  FROM usage_records
                 WHERE created_at > now() - make_interval(days => %s){scope}""",
            params,
        ).fetchone()
        by_model = conn.execute(
            f"""SELECT provider, model, count(*) AS calls,
                       COALESCE(sum(prompt_tokens + completion_tokens), 0) AS tokens,
                       COALESCE(sum(cost_usd), 0) AS cost_usd
                  FROM usage_records
                 WHERE created_at > now() - make_interval(days => %s){scope}
                 GROUP BY provider, model ORDER BY cost_usd DESC""",
            params,
        ).fetchall()
        by_day = conn.execute(
            f"""SELECT date_trunc('day', created_at)::date AS day,
                       COALESCE(sum(prompt_tokens + completion_tokens), 0) AS tokens,
                       COALESCE(sum(cost_usd), 0) AS cost_usd
                  FROM usage_records
                 WHERE created_at > now() - make_interval(days => %s){scope}
                 GROUP BY 1 ORDER BY 1""",
            params,
        ).fetchall()
    return {
        "totals": {
            "calls": totals["calls"],
            "prompt_tokens": int(totals["prompt_tokens"]),
            "completion_tokens": int(totals["completion_tokens"]),
            "cost_usd": float(totals["cost_usd"]),
            "avg_latency_ms": int(totals["avg_latency_ms"]),
        },
        "by_model": [
            {
                "provider": r["provider"],
                "model": r["model"],
                "calls": r["calls"],
                "tokens": int(r["tokens"]),
                "cost_usd": float(r["cost_usd"]),
            }
            for r in by_model
        ],
        "by_day": [
            {
                "day": r["day"].isoformat(),
                "tokens": int(r["tokens"]),
                "cost_usd": float(r["cost_usd"]),
            }
            for r in by_day
        ],
    }


class FeedbackIn(BaseModel):
    message_id: str
    rating: int = Field(ge=-1, le=1)
    comment: str = Field(default="", max_length=2000)


@router.post("/chats/{chat_id}/feedback", status_code=201)
def give_feedback(chat_id: str, payload: FeedbackIn, user: dict = Depends(current_user)):
    if payload.rating not in (-1, 1):
        raise HTTPException(status_code=400, detail="rating deve ser -1 ou 1")
    with get_connection() as conn:
        message = conn.execute(
            """SELECT m.id FROM chat_messages m
                 JOIN chats c ON c.id = m.chat_id
                WHERE m.id = %s AND m.chat_id = %s AND c.user_id = %s""",
            (payload.message_id, chat_id, user["id"]),
        ).fetchone()
        if message is None:
            raise HTTPException(status_code=404, detail="Mensagem não encontrada")
        conn.execute(
            """INSERT INTO feedback (tenant_id, user_id, chat_id, message_id, rating, comment)
               VALUES (%s, %s, %s, %s, %s, %s)
               ON CONFLICT (message_id, user_id)
               DO UPDATE SET rating = EXCLUDED.rating, comment = EXCLUDED.comment""",
            (
                user["tenant_id"],
                user["id"],
                chat_id,
                payload.message_id,
                payload.rating,
                payload.comment,
            ),
        )
    return {"status": "ok"}


@router.get("/feedback")
def list_feedback(user: dict = Depends(require("usage", "view"))):
    scope = "" if user["is_master"] else " WHERE f.tenant_id = %s"
    params = () if user["is_master"] else (user["tenant_id"],)
    with get_connection() as conn:
        rows = conn.execute(
            f"""SELECT f.rating, f.comment, f.created_at, m.content, u.name AS user_name
                  FROM feedback f
                  JOIN chat_messages m ON m.id = f.message_id
                  JOIN users u ON u.id = f.user_id{scope}
                 ORDER BY f.created_at DESC LIMIT 200""",
            params,
        ).fetchall()
    return [
        {
            "rating": r["rating"],
            "comment": r["comment"],
            "message": r["content"][:300],
            "user": r["user_name"],
            "created_at": r["created_at"].isoformat(),
        }
        for r in rows
    ]
