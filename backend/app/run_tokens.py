"""run_token: credencial curta, por run, para as rotas /internal/runs/{id}/*.

Formato `base64url(claims).base64url(hmac_sha256)`; sem dependências. O token
prova apenas "esta chamada pertence à run X"; tenant, usuário e chat vêm da
linha de `chat_runs`, nunca do corpo da chamada. É revogado quando a run
termina (status deixa de ser running/cancelling).
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time
import uuid

from app.config import settings


def _secret() -> bytes:
    material = settings.run_token_secret or settings.encryption_key or "dev-run-token-secret"
    return hashlib.sha256(("run-token:" + material).encode()).digest()


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def _unb64(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def issue_run_token(
    *,
    run_id: str,
    tenant_id: str,
    user_id: str | None,
    chat_id: str,
    template_id: str | None,
    version_id: str | None,
) -> str:
    claims = {
        "run_id": run_id,
        "tenant_id": tenant_id,
        "user_id": user_id,
        "chat_id": chat_id,
        "template_id": template_id,
        "version_id": version_id,
        "scopes": ["usage", "artifacts", "messages", "skills", "approvals", "package"],
        "jti": uuid.uuid4().hex,
        "exp": int(time.time()) + settings.run_token_ttl_seconds,
    }
    body = _b64(json.dumps(claims, separators=(",", ":")).encode())
    sig = _b64(hmac.new(_secret(), body.encode(), hashlib.sha256).digest())
    return f"{body}.{sig}"


def verify_run_token(token: str) -> dict | None:
    try:
        body, sig = token.split(".", 1)
        expected = _b64(hmac.new(_secret(), body.encode(), hashlib.sha256).digest())
        if not hmac.compare_digest(sig, expected):
            return None
        claims = json.loads(_unb64(body))
    except (ValueError, TypeError):
        return None
    if not isinstance(claims, dict) or claims.get("exp", 0) < time.time():
        return None
    return claims
