"""Permission vocabulary and checks.

A profile holds {resource: [actions]}. The master user bypasses every check;
everyone else is confined to their tenant by the queries themselves.
"""

RESOURCES = (
    "templates",
    "ai_services",
    "datasources",
    "files",
    "secrets",
    "users",
    "user_profiles",
    "integrations",
    "chats",
    "usage",
    "payments",
    "mcp_store",
    "omnichannel",
    "ai_router",
    "email_accounts",
    "google_accounts",
    "microsoft_accounts",
    "unofficial_connections",
    # Hermes agente: dispositivos, sessões, comandos e aprovações remotas
    # (SPEC_HERMES_INTEGRADO_RIA_ATENDIMENTO.md, seção 7.6). "view" é o papel
    # visualizador; "create" cobre pareamento e envio de comando; "edit" cobre
    # aprovar/rejeitar e mudar estado de sessão; "delete" cobre revogar
    # dispositivo. O compartilhamento granular por sessão (seção 16.3) ainda
    # não tem ADR fechado — esta é a RBAC de tenant, não a de sessão individual.
    "hermes",
    # Agentes por skills (spec 04): skills da empresa e ambiente do cliente.
    "skills",
    "environment",
)

ACTIONS = ("view", "create", "edit", "delete")

# Recursos com vocabulário próprio de ações. `environment` não é CRUD: ver e
# gerir (iniciar, parar, publicar, domínios).
RESOURCE_ACTIONS = {"environment": ("view", "manage")}


def actions_for(resource: str) -> tuple[str, ...]:
    return RESOURCE_ACTIONS.get(resource, ACTIONS)


# Seeded per tenant on creation.
ADMIN_PERMISSIONS = {r: list(actions_for(r)) for r in RESOURCES}
MEMBER_PERMISSIONS = {"chats": ["view", "create"], "templates": ["view"]}


# Escopos `ai.*` do Chatwoot (spec 10) -> permissões que as rotas já checam. Só acrescenta;
# nunca amplia
# além do que o escopo diz.
_RW = ("view", "create", "edit", "delete")
SCOPE_PERMISSIONS: dict[str, tuple[tuple[str, tuple[str, ...]], ...]] = {
    "ai.chat.view": (("chats", ("view",)),),
    "ai.chat.run": (("chats", ("view", "create", "edit")),),
    "ai.agents.view": (("templates", ("view",)),),
    "ai.agents.create": (("templates", ("view", "create")),),
    "ai.agents.edit": (("templates", ("view", "edit")),),
    "ai.agents.publish": (("templates", ("view", "edit")),),
    "ai.agents.archive": (("templates", ("view", "delete")),),
    "ai.skills.view": (("skills", ("view",)),),
    "ai.skills.manage_company": (("skills", _RW),),
    "ai.knowledge.view": (("files", ("view",)),),
    "ai.knowledge.manage": (("files", _RW),),
    "ai.runs.view": (("hermes", ("view",)),),
    "ai.usage.view": (("usage", ("view",)),),
    "ai.providers.view": (("ai_services", ("view",)), ("ai_router", ("view",))),
    "ai.providers.manage": (("ai_services", _RW), ("ai_router", _RW)),
    # Credenciais é a tela única de tudo que o agente usa: segredos, SSH, GCP, bancos de dados,
    # e-mail, pagamento e contas Google/Outlook (spec 10 §15.1).
    "ai.credentials.view": (
        ("secrets", ("view",)), ("email_accounts", ("view",)),
        ("google_accounts", ("view",)), ("microsoft_accounts", ("view",)),
        ("datasources", ("view",)), ("payments", ("view",)),
    ),
    "ai.credentials.manage": (
        ("secrets", _RW), ("email_accounts", _RW),
        ("google_accounts", _RW), ("microsoft_accounts", _RW),
        ("datasources", _RW), ("payments", _RW),
    ),
    "ai.integrations.view": (("integrations", ("view",)),),
    "ai.integrations.manage": (("integrations", _RW),),
    "ai.payments.view": (("payments", ("view",)),),
    "ai.payments.manage": (("payments", _RW),),
    "ai.environment.view": (("environment", ("view",)),),
    "ai.environment.manage": (("environment", ("view", "manage")),),
    "ai.inbox_routing.view": (("omnichannel", ("view",)), ("unofficial_connections", ("view",))),
    "ai.inbox_routing.manage": (("omnichannel", _RW), ("unofficial_connections", _RW)),
}


def scopes_to_permissions(scopes) -> dict[str, list[str]]:
    out: dict[str, set[str]] = {}
    for scope in scopes or ():
        for resource, actions in SCOPE_PERMISSIONS.get(scope, ()):
            out.setdefault(resource, set()).update(a for a in actions if a in actions_for(resource))
    return {r: sorted(a) for r, a in out.items()}


def has_permission(user: dict, resource: str, action: str) -> bool:
    if user.get("is_master"):
        return True
    permissions = user.get("permissions") or {}
    return action in permissions.get(resource, [])


def require_scope_for_chatwoot(user: dict, scope: str) -> None:
    """Rotas só com login: quem vem do Chatwoot precisa do escopo `ai.*` correspondente."""
    if user.get("via") == "chatwoot" and scope not in user.get("scopes", ()):
        from fastapi import HTTPException

        raise HTTPException(status_code=403, detail="Permissão negada")


def validate_permissions(permissions: dict) -> None:
    """Raise ValueError if a profile payload names unknown resources/actions."""
    for resource, actions in permissions.items():
        if resource not in RESOURCES:
            raise ValueError(f"unknown resource: {resource}")
        if not isinstance(actions, list):
            raise ValueError(f"actions for {resource} must be a list")
        for action in actions:
            if action not in actions_for(resource):
                raise ValueError(f"unknown action for {resource}: {action}")
