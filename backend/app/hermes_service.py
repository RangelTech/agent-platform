"""Helpers shared by the Hermes routes: auditoria de produto e o writer de
eventos de sessão. Centralizados aqui para que toda rota grave do mesmo jeito
— a seção 9 da spec pede índices e formato consistentes, e a seção 2/critério
15 pede que a auditoria sempre mostre ator, dispositivo, sessão e resultado."""

from psycopg.types.json import Json


def audit(
    conn,
    *,
    tenant_id: str,
    actor_type: str,
    actor_id: str | None,
    action: str,
    resource_type: str,
    resource_id: str | None,
    result: str = "ok",
    metadata: dict | None = None,
) -> None:
    """Appends one audit row. Never raises on a bad `metadata` shape by
    itself — mutations should not fail because logging almost failed — but a
    caller inside the same transaction still rolls back together on a real
    database error, which is the correct behaviour here (an audit write that
    silently vanished would defeat the point)."""
    conn.execute(
        """INSERT INTO hermes_audit_log
               (tenant_id, actor_type, actor_id, action, resource_type,
                resource_id, result, metadata)
           VALUES (%s, %s, %s, %s, %s, %s, %s, %s)""",
        (
            tenant_id,
            actor_type,
            actor_id,
            action,
            resource_type,
            resource_id,
            result,
            Json(metadata or {}),
        ),
    )


def append_session_event(
    conn,
    *,
    tenant_id: str,
    session_id: str,
    sequence: int,
    type_: str,
    payload: dict,
) -> dict | None:
    """Idempotent by (session_id, sequence): a retried/duplicated publish
    from the extension after a reconnect is a no-op, per seção 8.3 ("eventos
    duplicados são descartados por event_id/sequência"). Returns the row that
    ended up stored — the new one, or the existing one on a duplicate."""
    row = conn.execute(
        """INSERT INTO hermes_session_events
               (tenant_id, session_id, sequence, type, payload)
           VALUES (%s, %s, %s, %s, %s)
           ON CONFLICT (session_id, sequence) DO NOTHING
           RETURNING *""",
        (tenant_id, session_id, sequence, type_, Json(payload)),
    ).fetchone()
    if row is not None:
        conn.execute(
            "UPDATE hermes_sessions SET last_activity_at = now(), updated_at = now() WHERE id = %s",
            (session_id,),
        )
        return row
    return conn.execute(
        "SELECT * FROM hermes_session_events WHERE session_id = %s AND sequence = %s",
        (session_id, sequence),
    ).fetchone()


# Estados válidos que um comando pode assumir a partir de cada estado atual
# (seção 8.3). Uma transição fora deste mapa é 409, não um estado inventado.
COMMAND_TRANSITIONS: dict[str, set[str]] = {
    "queued": {"delivered", "cancelled", "expired"},
    "delivered": {"accepted", "rejected", "failed", "cancelled", "expired"},
    "accepted": {"running", "failed", "cancelled"},
    "running": {"completed", "failed", "cancelled"},
}

COMMAND_TERMINAL = {"completed", "rejected", "failed", "cancelled", "expired"}
