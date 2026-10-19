import pytest
from app.skill_package import normalize_manifest
from app.template_access import can_run, ensure_can_run, legacy_scopes
from fastapi import HTTPException

ADMIN_ENV = {"limits": {"allowed_scopes": ["ai.environment.manage"]}}


def test_manifest_keeps_only_valid_scopes():
    m = normalize_manifest(
        {"limits": {"allowed_scopes": ["ai.environment.manage", "root", 3, "AI.Chat.Run"]}}
    )
    assert m["limits"]["allowed_scopes"] == ["ai.environment.manage", "ai.chat.run"]
    assert "allowed_scopes" not in normalize_manifest({"limits": {"allowed_scopes": []}})["limits"]


def test_open_agent_runs_for_everyone_and_restricted_needs_a_scope():
    assert can_run({"limits": {}}, set()) and can_run(None, set())
    assert not can_run(ADMIN_ENV, {"ai.chat.run"})
    assert can_run(ADMIN_ENV, {"ai.environment.manage"})
    assert can_run(ADMIN_ENV, {"ai.agents.edit"})  # quem edita sempre testa


def test_legacy_profile_maps_to_scopes_and_blocks_members():
    member = {
        "permissions": {"chats": ["view", "create"], "templates": ["view"]},
        "is_master": False,
    }
    admin = {
        "permissions": {"environment": ["view", "manage"], "chats": ["create"]},
        "is_master": False,
    }
    assert "ai.chat.run" in legacy_scopes(member) and "ai.environment.manage" not in legacy_scopes(
        member
    )
    with pytest.raises(HTTPException) as exc:
        ensure_can_run(ADMIN_ENV, member)
    assert exc.value.status_code == 403
    ensure_can_run(ADMIN_ENV, admin)
    ensure_can_run(ADMIN_ENV, {"permissions": {}, "is_master": True})
