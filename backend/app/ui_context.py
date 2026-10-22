"""Contexto assinado pelo Rails (Chatwoot): única credencial das rotas /internal/ui/*.

Formato `base64url(claims).base64url(hmac_sha256)`, igual ao `run_token`.
`aud` separa o contexto de uma Account (`ui`) do de instalação (`install`, usado
pelo ciclo de vida e pelo Super Admin). O corpo/URL de uma requisição nunca é
fonte de tenant ou usuário: tudo vem das claims. Cada `nonce` vale uma vez.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time

from fastapi import Depends, Header, HTTPException

from app.config import settings
from app.db import get_connection

REVOKED_MESSAGE = "Seu acesso foi removido"
MAX_TTL_SECONDS = 60
CLOCK_SKEW_SECONDS = 5


def _secret() -> bytes:
    material = settings.ui_context_secret or settings.encryption_key or "dev-ui-context-secret"
    return hashlib.sha256(("ui-context:" + material).encode()).digest()


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def _unb64(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def sign_context(claims: dict) -> str:
    """Usado pelos testes e por qualquer cliente Python; o Rails reimplementa o mesmo formato."""
    body = _b64(json.dumps(claims, separators=(",", ":")).encode())
    return f"{body}.{_b64(hmac.new(_secret(), body.encode(), hashlib.sha256).digest())}"


def verify_context(token: str, audience: str) -> dict | None:
    try:
        body, sig = token.split(".", 1)
        expected = _b64(hmac.new(_secret(), body.encode(), hashlib.sha256).digest())
        if not hmac.compare_digest(sig, expected):
            return None
        claims = json.loads(_unb64(body))
    except (ValueError, TypeError):
        return None
    if not isinstance(claims, dict) or claims.get("aud") != audience:
        return None
    now = time.time()
    iat, exp = claims.get("iat"), claims.get("exp")
    if not isinstance(iat, int | float) or not isinstance(exp, int | float):
        return None
    if exp < now or iat > now + CLOCK_SKEW_SECONDS or exp - iat > MAX_TTL_SECONDS:
        return None
    if not claims.get("nonce"):
        return None
    return claims


def _consume_nonce(nonce: str, exp: float) -> bool:
    """True se o nonce era novo (e agora está gasto)."""
    with get_connection() as conn:
        conn.execute("DELETE FROM ui_nonces WHERE expires_at < now()")
        row = conn.execute(
            """INSERT INTO ui_nonces (nonce, expires_at) VALUES (%s, to_timestamp(%s))
               ON CONFLICT DO NOTHING RETURNING nonce""",
            (nonce, exp),
        ).fetchone()
    return row is not None


def _claims_from_header(header: str | None, audience: str) -> dict:
    if not header:
        raise HTTPException(status_code=401, detail="Contexto ausente")
    claims = verify_context(header, audience)
    if claims is None:
        raise HTTPException(status_code=401, detail="Contexto inválido ou expirado")
    if not _consume_nonce(str(claims["nonce"]), float(claims["exp"])):
        raise HTTPException(status_code=401, detail="Contexto já utilizado")
    return claims


def install_actor(x_ui_context: str | None = Header(default=None)) -> dict:
    """Ator de instalação (ciclo de vida, Super Admin): sem Account."""
    claims = _claims_from_header(x_ui_context, "install")
    return {"kind": "install", "claims": claims, "scopes": set(claims.get("scopes") or [])}


def _sync_engine_flags(conn, tenant_id, flags: dict) -> None:
    """O Chatwoot decide as flags; o motor só obedece (spec 10 §0.6).

    `harness_enabled` e `graph_enabled` também vivem na linha do tenant e
    guardam as rotas de templates; alinhá-las ao contexto assinado evita que
    uma Account nova receba 403 "Recurso não habilitado" com a flag ligada.
    """
    for key in ("harness_enabled", "graph_enabled"):
        if key in flags:
            conn.execute(
                f"UPDATE tenants SET {key} = %s WHERE id = %s AND {key} IS DISTINCT FROM %s",  # noqa: S608
                (bool(flags[key]), tenant_id, bool(flags[key])),
            )


def service_actor(x_ui_context: str | None = Header(default=None)) -> dict:
    """Ator de uma Account: tenant, usuário, escopos e flags vêm só das claims.

    Cria (uma vez) a projeção do usuário do Chatwoot no motor.
    """
    claims = _claims_from_header(x_ui_context, "ui")
    tenant_id = claims.get("engine_tenant_id")
    cw_user = claims.get("chatwoot_user_id")
    if not tenant_id or not cw_user:
        raise HTTPException(status_code=401, detail="Contexto incompleto")
    with get_connection() as conn:
        tenant = conn.execute(
            "SELECT id, is_active, suspended_by_chatwoot FROM tenants WHERE id = %s", (tenant_id,)
        ).fetchone()
        if tenant is None or not tenant["is_active"]:
            raise HTTPException(status_code=403, detail="Empresa indisponível")
        _sync_engine_flags(conn, tenant_id, claims.get("flags") or {})
        email = str(claims.get("email") or f"cw{cw_user}@invalid.local")
        # Mesma pessoa (e-mail) removida e readicionada no Chatwoot volta com outro id: religa a
        # projeção antiga em vez de colidir em users_tenant_email_key (500 no stream).
        conn.execute(
            """UPDATE users SET chatwoot_user_id = %s, updated_at = now()
               WHERE tenant_id = %s AND lower(email) = lower(%s)
                 AND chatwoot_user_id IS DISTINCT FROM %s""",
            (cw_user, tenant_id, email, cw_user),
        )
        revoked = conn.execute(
            """SELECT id, is_active, revoked_at,
                      extract(epoch FROM revoked_at) AS revoked_ts
                 FROM users WHERE tenant_id = %s AND chatwoot_user_id = %s""",
            (tenant_id, cw_user),
        ).fetchone()
        if revoked is not None and not revoked["is_active"]:
            # Removido no Chatwoot: só um contexto emitido depois da remoção (pessoa readicionada)
            # reativa. Contexto anterior, ou desativação sem remoção, segue recusado.
            issued = float(claims.get("iat") or 0)
            if revoked["revoked_at"] is None or issued + 1 <= float(revoked["revoked_ts"]):
                raise HTTPException(status_code=403, detail=REVOKED_MESSAGE)
            conn.execute(
                "UPDATE users SET is_active = true, revoked_at = NULL, updated_at = now() "
                "WHERE id = %s",
                (revoked["id"],),
            )
        row = conn.execute(
            """INSERT INTO users (tenant_id, chatwoot_user_id, email, name)
               VALUES (%s, %s, %s, %s)
               ON CONFLICT (tenant_id, chatwoot_user_id) WHERE chatwoot_user_id IS NOT NULL
               DO UPDATE SET email = EXCLUDED.email, name = EXCLUDED.name, updated_at = now()
               RETURNING id, is_active""",
            (tenant_id, cw_user, email, str(claims.get("name") or email)),
        ).fetchone()
    if not row["is_active"]:
        raise HTTPException(status_code=403, detail=REVOKED_MESSAGE)
    return {
        "kind": "ui",
        "tenant_id": str(tenant_id),
        "user_id": str(row["id"]),
        "chatwoot_account_id": claims.get("account_id"),
        "chatwoot_user_id": cw_user,
        "scopes": set(claims.get("scopes") or []),
        "flags": claims.get("flags") or {},
        "claims": claims,
    }


def require_scope(scope: str):
    def dependency(actor: dict = Depends(service_actor)) -> dict:
        if scope not in actor["scopes"]:
            raise HTTPException(status_code=403, detail="Permissão negada")
        return actor

    return dependency
