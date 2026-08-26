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
)

ACTIONS = ("view", "create", "edit", "delete")

# Seeded per tenant on creation.
ADMIN_PERMISSIONS = {r: list(ACTIONS) for r in RESOURCES}
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
            if action not in ACTIONS:
                raise ValueError(f"unknown action for {resource}: {action}")
