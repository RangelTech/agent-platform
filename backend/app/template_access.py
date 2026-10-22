"""Quem pode usar cada agente (spec 10, A-7 / D-11).

O manifesto de um agente por skills pode declarar `limits.allowed_scopes`: permissões `ai.*`
(qualquer uma basta). Vazio = todos que podem conversar. O agente administrador de ambiente,
por exemplo, declara `ai.environment.manage`. Quem edita agentes (`ai.agents.edit`) sempre o testa.

Duas fontes de permissão chegam aqui: o contexto assinado do Chatwoot (já traz `ai.*`) e,
enquanto a interface antiga existir, o perfil legado do usuário (via `legacy_scopes`).
"""

from __future__ import annotations

import re

from fastapi import HTTPException

SCOPE_RE = re.compile(r"^ai\.[a-z_]+\.[a-z_]+$")

_LEGACY = {
    ("environment", "manage"): "ai.environment.manage",
    ("environment", "view"): "ai.environment.view",
    ("templates", "create"): "ai.agents.create",
    ("templates", "edit"): "ai.agents.edit",
    ("templates", "view"): "ai.agents.view",
    ("skills", "create"): "ai.skills.manage_company",
    ("skills", "edit"): "ai.skills.manage_company",
    ("chats", "create"): "ai.chat.run",
    ("chats", "view"): "ai.chat.view",
    ("payments", "edit"): "ai.payments.manage",
    ("secrets", "edit"): "ai.credentials.manage",
}


def legacy_scopes(user: dict) -> set[str]:
    """Escopos `ai.*` equivalentes ao perfil legado (o que o perfil já permite)."""
    out: set[str] = set()
    for resource, actions in (user.get("permissions") or {}).items():
        for action in actions or []:
            scope = _LEGACY.get((resource, action))
            if scope:
                out.add(scope)
    return out


def can_run(manifest: dict | None, scopes: set[str]) -> bool:
    limits = (manifest or {}).get("limits") or {}
    allowed = limits.get("allowed_scopes") or []
    if not allowed:
        return True
    if limits.get("strict_scope"):
        return any(s in scopes for s in allowed)
    return "ai.agents.edit" in scopes or any(s in scopes for s in allowed)


def ensure_can_run(manifest: dict | None, user: dict) -> None:
    if user.get("is_master"):
        return
    if not can_run(manifest, legacy_scopes(user)):
        raise HTTPException(status_code=403, detail="Você não tem permissão para usar este agente")
