"""Device authentication for the Hermes extension (não confundir com
`auth.py`, que autentica o usuário humano no RIA).

Espelha o padrão de `auth.py`/`sessions`: um segredo opaco é emitido uma vez
e só o hash fica no banco (`hash_token`, reaproveitado de `security.py`).
A diferença é o que a credencial autoriza — aqui, publicar presença e
sessões e consumir comandos do próprio dispositivo; nunca ações de usuário."""

from datetime import UTC, datetime

from fastapi import Depends, HTTPException, Request

from app.db import get_connection
from app.security import hash_token, new_session_token

_DEVICE_QUERY = """
SELECT d.id AS device_id, d.tenant_id, d.user_id, d.name, d.status,
       c.id AS credential_id, c.revoked_at AS credential_revoked_at
  FROM hermes_device_credentials c
  JOIN hermes_devices d ON d.id = c.device_id
"""


def issue_device_credential(conn, device_id: str) -> str:
    """Mints a new plaintext credential for a device, on the caller's own
    connection/transaction — pairing inserts the device and its credential
    together, and a device revoke can rotate one without a second round trip.
    The caller decides whether to revoke a previous credential (rotation);
    this only inserts."""
    token = new_session_token()
    conn.execute(
        "INSERT INTO hermes_device_credentials (device_id, token_hash) VALUES (%s, %s)",
        (device_id, hash_token(token)),
    )
    return token


def _bearer_token(request: Request) -> str:
    header = request.headers.get("authorization", "")
    scheme, _, token = header.partition(" ")
    if scheme.lower() != "bearer" or not token:
        raise HTTPException(status_code=401, detail="Dispositivo não autenticado")
    return token


def current_device(request: Request) -> dict:
    """FastAPI dependency: the authenticated Hermes device, or 401.

    Also touches `last_used_at`/`last_seen_at` so presence has a real
    timestamp without needing a heartbeat endpoint of its own yet (a real
    heartbeat/WSS presence arrives with the Relay, Fase B)."""
    token = _bearer_token(request)
    now = datetime.now(UTC)
    with get_connection() as conn:
        row = conn.execute(
            _DEVICE_QUERY + " WHERE c.token_hash = %s", (hash_token(token),)
        ).fetchone()
        if row is None or row["credential_revoked_at"] is not None:
            raise HTTPException(status_code=401, detail="Credencial de dispositivo inválida")
        if row["status"] == "revoked":
            raise HTTPException(status_code=401, detail="Dispositivo revogado")
        conn.execute(
            "UPDATE hermes_device_credentials SET last_used_at = %s WHERE id = %s",
            (now, row["credential_id"]),
        )
        conn.execute(
            """UPDATE hermes_devices
                  SET last_seen_at = %s,
                      status = CASE WHEN status = 'disconnected' THEN 'connected' ELSE status END
                WHERE id = %s""",
            (now, row["device_id"]),
        )
        return {
            "id": str(row["device_id"]),
            "tenant_id": str(row["tenant_id"]),
            "user_id": str(row["user_id"]),
            "name": row["name"],
        }


def require_device(device: dict = Depends(current_device)) -> dict:
    return device
