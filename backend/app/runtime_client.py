"""Cliente do gerenciador de execução (runtime-manager): assinatura HMAC por requisição.

Id do tenant no gerenciador: `t` + 12 hex do UUID da empresa.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import re
import time
import uuid

import httpx

from app.config import settings


def rm_tenant(tenant_id) -> str:
    return "t" + re.sub(r"[^0-9a-f]", "", str(tenant_id).lower())[:12]


def configured() -> bool:
    return bool(settings.runtime_manager_url and settings.runtime_manager_secret)


def _sign(method: str, target: str, body: bytes, scope: str) -> dict[str, str]:
    service = settings.runtime_manager_service
    iat = str(int(time.time()))
    msg = "\n".join([service, iat, scope, method.upper(), target,
                     hashlib.sha256(body).hexdigest()]).encode()
    sig = hmac.new(settings.runtime_manager_secret.encode(), msg, hashlib.sha256).hexdigest()
    return {"X-RM-Service": service, "X-RM-Iat": iat, "X-RM-Scope": scope, "X-RM-Signature": sig}


class RMError(Exception):
    def __init__(self, status: int, code: str):
        super().__init__(f"{status} {code}")
        self.status = status
        self.code = code


def call(method: str, path: str, *, scope: str, body: dict | None = None,
         timeout: float = 30.0) -> dict:
    raw = json.dumps(body, separators=(",", ":")).encode() if body is not None else b""
    headers = _sign(method, path, raw, scope)
    headers["Content-Type"] = "application/json"
    headers["Idempotency-Key"] = uuid.uuid4().hex
    r = httpx.request(method, settings.runtime_manager_url.rstrip("/") + path, content=raw,
                      headers=headers, timeout=timeout)
    if r.status_code >= 400:
        try:
            code = str(r.json().get("error") or r.json().get("code") or r.status_code)
        except ValueError:
            code = str(r.status_code)
        raise RMError(r.status_code, code)
    try:
        return r.json()
    except ValueError:
        return {}
