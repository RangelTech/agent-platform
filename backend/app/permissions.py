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


def has_permission(user: dict, resource: str, action: str) -> bool:
    if user.get("is_master"):
        return True
    permissions = user.get("permissions") or {}
    return action in permissions.get(resource, [])


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
