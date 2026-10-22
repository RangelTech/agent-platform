import time
import uuid

import pytest
from app.config import settings
from app.db import get_connection
from app.ui_context import sign_context


def _ctx(aud="install", scopes=("lifecycle",), **extra):
    now = int(time.time())
    claims = {
        "aud": aud,
        "scopes": list(scopes),
        "nonce": uuid.uuid4().hex,
        "iat": now,
        "exp": now + 30,
    }
    claims.update(extra)
    return {"X-UI-Context": sign_context(claims)}


@pytest.fixture
def ui(client):
    settings.router_auto_provision_enabled = False
    with get_connection() as conn:
        conn.execute("TRUNCATE ui_nonces")
    return client


def test_provision_is_idempotent_and_creates_factory(ui):
    body = {"account_id": 4242, "name": "Padaria Água Viva"}
    first = ui.post("/internal/ui/provision", json=body, headers=_ctx())
    assert first.status_code == 200 and first.json()["created"] is True
    second = ui.post("/internal/ui/provision", json=body, headers=_ctx())
    assert second.json()["created"] is False
    assert second.json()["engine_tenant_id"] == first.json()["engine_tenant_id"]
    with get_connection() as conn:
        n = conn.execute(
            "SELECT count(*) AS n FROM templates WHERE tenant_id=%s AND system_key IS NOT NULL",
            (first.json()["engine_tenant_id"],),
        ).fetchone()["n"]
    assert n == 1


def test_context_rejects_replay_wrong_audience_and_tamper(ui):
    headers = _ctx()
    body = {"account_id": 7, "name": "Loja"}
    assert ui.post("/internal/ui/provision", json=body, headers=headers).status_code == 200
    assert (
        ui.post("/internal/ui/provision", json=body, headers=headers).status_code == 401
    )  # replay
    assert ui.post("/internal/ui/provision", json=body, headers=_ctx(aud="ui")).status_code == 401
    bad = _ctx()
    bad["X-UI-Context"] = bad["X-UI-Context"][:-3] + "AAA"
    assert ui.post("/internal/ui/provision", json=body, headers=bad).status_code == 401
    assert ui.post("/internal/ui/provision", json=body).status_code == 401


def test_context_requires_lifecycle_scope_and_short_ttl(ui):
    body = {"account_id": 8, "name": "X"}
    assert ui.post("/internal/ui/provision", json=body, headers=_ctx(scopes=())).status_code == 403
    now = int(time.time())
    long_lived = sign_context(
        {"aud": "install", "scopes": ["lifecycle"], "nonce": "n1", "iat": now, "exp": now + 3600}
    )
    assert (
        ui.post(
            "/internal/ui/provision", json=body, headers={"X-UI-Context": long_lived}
        ).status_code
        == 401
    )


def test_suspend_resume_deprovision_never_delete(ui):
    tid = ui.post(
        "/internal/ui/provision", json={"account_id": 9, "name": "Y"}, headers=_ctx()
    ).json()["engine_tenant_id"]
    assert (
        ui.post("/internal/ui/suspend", json={"account_id": 9}, headers=_ctx()).status_code == 200
    )
    with get_connection() as conn:
        assert (
            conn.execute("SELECT is_active FROM tenants WHERE id=%s", (tid,)).fetchone()[
                "is_active"
            ]
            is False
        )
    assert ui.post("/internal/ui/resume", json={"account_id": 9}, headers=_ctx()).status_code == 200
    out = ui.post("/internal/ui/deprovision", json={"account_id": 9}, headers=_ctx()).json()
    assert out["data_retained"] is True
    with get_connection() as conn:
        assert (
            conn.execute("SELECT count(*) AS n FROM tenants WHERE id=%s", (tid,)).fetchone()["n"]
            == 1
        )


def test_suspend_and_resume_follow_gateway_when_provisioned(ui, monkeypatch):
    tid = ui.post(
        "/internal/ui/provision", json={"account_id": 10, "name": "Gateway"}, headers=_ctx()
    ).json()["engine_tenant_id"]
    with get_connection() as conn:
        conn.execute(
            """INSERT INTO tenant_routers
                   (tenant_id, gateway_tenant_id, gateway_bridge_key_encrypted,
                    gateway_ai_assist_key_encrypted)
               VALUES (%s, %s, %s, %s)""",
            (tid, tid, "bridge", "assist"),
        )
    calls = []

    async def fake_status(base_url, admin_token, *, tenant_id, active):
        calls.append((tenant_id, active))

    monkeypatch.setattr("app.routes.ui_internal.resolver_segredo", lambda *_: "fake")
    monkeypatch.setattr("app.routes.ui_internal.gateway_client.set_tenant_status", fake_status)
    suspended = ui.post("/internal/ui/suspend", json={"account_id": 10}, headers=_ctx())
    resumed = ui.post("/internal/ui/resume", json={"account_id": 10}, headers=_ctx())
    assert suspended.status_code == 200
    assert resumed.status_code == 200
    assert calls == [(tid, False), (tid, True)]


def test_me_projects_chatwoot_user_per_tenant_and_blocks_suspended(ui):
    tid = ui.post(
        "/internal/ui/provision", json={"account_id": 11, "name": "Z"}, headers=_ctx()
    ).json()["engine_tenant_id"]
    ctx = dict(
        aud="ui",
        scopes=("ai.chat.run",),
        engine_tenant_id=tid,
        chatwoot_user_id=55,
        account_id=11,
        email="ana@exemplo.com",
        name="Ana",
        flags={"chat_enabled": True},
    )
    me = ui.get("/internal/ui/me", headers=_ctx(**ctx))
    assert me.status_code == 200 and me.json()["scopes"] == ["ai.chat.run"]
    again = ui.get("/internal/ui/me", headers=_ctx(**ctx)).json()
    assert again["user_id"] == me.json()["user_id"]
    ui.post("/internal/ui/suspend", json={"account_id": 11}, headers=_ctx())
    assert ui.get("/internal/ui/me", headers=_ctx(**ctx)).status_code == 403


def test_same_person_in_two_companies_gets_two_isolated_users(ui):
    a = ui.post(
        "/internal/ui/provision", json={"account_id": 21, "name": "A"}, headers=_ctx()
    ).json()["engine_tenant_id"]
    b = ui.post(
        "/internal/ui/provision", json={"account_id": 22, "name": "B"}, headers=_ctx()
    ).json()["engine_tenant_id"]
    common = dict(aud="ui", scopes=("ai.chat.run",), chatwoot_user_id=7, email="x@y.com", name="X")
    ua = ui.get("/internal/ui/me", headers=_ctx(engine_tenant_id=a, account_id=21, **common)).json()
    ub = ui.get("/internal/ui/me", headers=_ctx(engine_tenant_id=b, account_id=22, **common)).json()
    assert ua["user_id"] != ub["user_id"] and ua["tenant_id"] != ub["tenant_id"]


def test_usuario_readicionado_com_novo_id_reaproveita_a_projecao(ui):
    """Gate FINAL: remover e readicionar a mesma pessoa (novo id) dava 500 por e-mail duplicado."""
    tid = ui.post(
        "/internal/ui/provision", json={"account_id": 31, "name": "R"}, headers=_ctx()
    ).json()["engine_tenant_id"]
    base = dict(
        aud="ui", scopes=("ai.chat.run",), engine_tenant_id=tid, account_id=31, email="r@y.com"
    )
    first = ui.get("/internal/ui/me", headers=_ctx(chatwoot_user_id=70, **base))
    second = ui.get("/internal/ui/me", headers=_ctx(chatwoot_user_id=71, **base))
    assert first.status_code == 200 and second.status_code == 200
    assert first.json()["user_id"] == second.json()["user_id"]


def test_ui_context_flags_drive_tenant_harness_and_graph(ui):
    tid = ui.post(
        "/internal/ui/provision", json={"account_id": 12, "name": "Y"}, headers=_ctx()
    ).json()["engine_tenant_id"]
    ctx = dict(
        aud="ui",
        scopes=("ai.chat.run",),
        engine_tenant_id=tid,
        chatwoot_user_id=7,
        account_id=12,
        email="b@exemplo.com",
        name="B",
        flags={"harness_enabled": True, "graph_enabled": False},
    )
    assert ui.get("/internal/ui/me", headers=_ctx(**ctx)).status_code == 200
    with get_connection() as conn:
        row = conn.execute(
            "SELECT harness_enabled, graph_enabled FROM tenants WHERE id=%s", (tid,)
        ).fetchone()
    assert row["harness_enabled"] is True and row["graph_enabled"] is False


def test_suspend_cancels_running_runs_of_tenant(ui):
    tid = ui.post(
        "/internal/ui/provision", json={"account_id": 13, "name": "W"}, headers=_ctx()
    ).json()["engine_tenant_id"]
    rid = str(uuid.uuid4())
    with get_connection() as conn:
        conn.execute(
            "INSERT INTO chat_runs (run_id, tenant_id, chat_id, kernel, status) "
            "VALUES (%s, %s, %s, 'harness', 'running')",
            (rid, tid, "c-1"),
        )
    ui.post("/internal/ui/suspend", json={"account_id": 13}, headers=_ctx())
    with get_connection() as conn:
        status = conn.execute(
            "SELECT status FROM chat_runs WHERE run_id::text=%s", (rid,)
        ).fetchone()["status"]
    assert status == "cancelling"
