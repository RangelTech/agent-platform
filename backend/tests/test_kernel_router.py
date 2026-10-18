"""Roteador de kernel, run_token e vocabulário de permissões (sem banco)."""

import pytest
from app import kernel_router as kr
from app import run_tokens
from app.config import settings
from app.migrations import MIGRATIONS_DIR
from app.permissions import ADMIN_PERMISSIONS, MEMBER_PERMISSIONS, validate_permissions
from fastapi import HTTPException


def test_resolve_kernel_by_template_kind(monkeypatch):
    monkeypatch.setattr(settings, "kernel_url", "http://llm.test")
    monkeypatch.setattr(settings, "kernel_harness_url", "http://skills.test")
    assert kr.resolve_kernel(None).chat_kernel == "llm"
    assert kr.resolve_kernel({"agent_kind": "structured"}).url == "http://llm.test"
    skills = kr.resolve_kernel({"agent_kind": "skills"})
    assert (skills.kind, skills.chat_kernel, skills.url) == (
        "skills",
        "harness",
        "http://skills.test",
    )
    assert skills.runs_url == "http://skills.test/v1/runs"


def test_skills_kernel_unconfigured_is_503(monkeypatch):
    monkeypatch.setattr(settings, "kernel_harness_url", "")
    with pytest.raises(HTTPException) as exc:
        kr.resolve_kernel({"agent_kind": "skills"})
    assert exc.value.status_code == 503
    # O fluxo estruturado não depende da configuração do segundo kernel.
    assert kr.resolve_kernel({"agent_kind": "structured"}).kind == "graph"


def test_payload_discriminator(monkeypatch):
    monkeypatch.setattr(settings, "kernel_harness_url", "http://skills.test")
    assert kr.target_for_payload({"thread_id": "x"}).kind == "graph"
    assert kr.target_for_payload({"contract_version": 1}).kind == "skills"


def test_chat_kernel_mapping():
    assert kr.chat_kernel_of("skills") == "harness" and kr.chat_kernel_of("graph") == "llm"
    assert kr.kind_of_chat_kernel("harness") == "skills"
    assert kr.kind_of_chat_kernel("llm") == "graph"
    assert kr.kind_of_chat_kernel(None) is None


@pytest.mark.anyio
async def test_headers_use_static_token_when_no_audience(monkeypatch):
    monkeypatch.setattr(settings, "kernel_harness_url", "http://skills.test")
    monkeypatch.setattr(settings, "kernel_harness_audience", "")
    monkeypatch.setattr(settings, "kernel_harness_token", "segredo")
    headers = await kr.kernel_headers(kr.target_for_kind("skills"))
    assert headers == {"Authorization": "Bearer segredo"}


def _token(**over):
    base = dict(
        run_id="r1", tenant_id="t1", user_id="u1", chat_id="c1", template_id="x", version_id="v"
    )
    base.update(over)
    return run_tokens.issue_run_token(**base)


def test_run_token_roundtrip_and_tamper():
    token = _token()
    claims = run_tokens.verify_run_token(token)
    assert claims["run_id"] == "r1" and claims["tenant_id"] == "t1" and claims["jti"]
    body, sig = token.split(".")
    assert run_tokens.verify_run_token(f"{body}.{sig[:-2]}xx") is None
    forged = run_tokens._b64(b'{"run_id":"r2","exp":9999999999}')
    assert run_tokens.verify_run_token(f"{forged}.{sig}") is None
    assert run_tokens.verify_run_token("lixo") is None


def test_run_token_expiry_and_secret_rotation(monkeypatch):
    monkeypatch.setattr(settings, "run_token_ttl_seconds", -1)
    assert run_tokens.verify_run_token(_token()) is None
    monkeypatch.setattr(settings, "run_token_ttl_seconds", 600)
    token = _token()
    monkeypatch.setattr(settings, "run_token_secret", "outro-segredo")
    assert run_tokens.verify_run_token(token) is None


def test_permission_vocabulary():
    assert ADMIN_PERMISSIONS["skills"] == ["view", "create", "edit", "delete"]
    assert ADMIN_PERMISSIONS["environment"] == ["view", "manage"]
    assert "skills" not in MEMBER_PERMISSIONS
    validate_permissions({"environment": ["view", "manage"], "skills": ["view"]})
    with pytest.raises(ValueError):
        validate_permissions({"environment": ["delete"]})
    with pytest.raises(ValueError):
        validate_permissions({"skills": ["manage"]})


def test_new_migrations_exist_and_leave_hermes_ones_alone():
    names = {p.name for p in MIGRATIONS_DIR.glob("*.sql")}
    for prefix in ("0043", "0044", "0045", "0046", "0047"):
        assert any(n.startswith(prefix) for n in names), prefix
    assert "0041_hermes_dominio.sql" in names and "0042_hermes_ws_tickets.sql" in names
