"""Skills da empresa x pessoais, criar rotina pela tela e vínculo caixa x template."""

import threading
import uuid

import pytest
import uvicorn
from app import scheduler
from app.config import settings
from fastapi import FastAPI, HTTPException, Request
from tests.conftest import auth
from tests.test_skills_agents import (
    SKILL_MD,
    _enable,
    _new_user,
    _publish,
    _skills_template,
    _sql,
)

pytestmark = pytest.mark.integration


@pytest.fixture
def enabled(client, master_token, tenant):
    _enable(client, master_token, tenant)
    return tenant


def _viewer(client, master_token, tenant):
    """Usuário comum que enxerga as skills da empresa (sem poder editar)."""
    profile = client.post(
        "/api/user-profiles",
        json={
            "name": "Leitor",
            "tenant_id": tenant["id"],
            "permissions": {"skills": ["view"], "chats": ["view", "create"]},
        },
        headers=auth(master_token),
    ).json()
    email = f"leitor-{uuid.uuid4().hex[:6]}@acme.com"
    client.post(
        "/api/users",
        json={
            "email": email,
            "name": "Leitor",
            "password": "senha-forte-123",
            "profile_id": profile["id"],
            "tenant_id": tenant["id"],
        },
        headers=auth(master_token),
    )
    token = client.post(
        "/api/auth/login", json={"email": email, "password": "senha-forte-123"}
    ).json()["token"]
    return {"token": token}


def _md(name, desc="Faz algo útil", body="Passo a passo.\n"):
    return f"---\nname: {name}\ndescription: {desc}\n---\n{body}"


# ------------------------------------------------------------- empresa ---


def test_company_skill_crud_is_shared_and_users_cannot_edit(
    client, master_token, enabled, tenant_admin
):
    admin = auth(tenant_admin["token"])
    r = client.post("/api/skills/company", json={"content": _md("tom-de-voz")}, headers=admin)
    assert r.status_code == 201, r.text
    sid = r.json()["id"]
    assert r.json()["scope"] == "company" and r.json()["status"] == "active"

    # Outro usuário da mesma empresa vê, mas não mexe.
    member = _viewer(client, master_token, enabled)
    mh = auth(member["token"])
    listed = client.get("/api/skills/company", headers=mh).json()
    assert [x["name"] for x in listed] == ["tom-de-voz"]
    assert client.get(f"/api/skills/company/{sid}", headers=mh).json()["content"]
    for verb, url in (
        ("put", f"/api/skills/company/{sid}"),
        ("delete", f"/api/skills/company/{sid}"),
        ("post", f"/api/skills/company/{sid}/disable"),
    ):
        kw = {"json": {"content": _md("tom-de-voz")}} if verb == "put" else {}
        assert getattr(client, verb)(url, headers=mh, **kw).status_code == 403
    assert (
        client.post("/api/skills/company", json={"content": _md("x")}, headers=mh).status_code
        == 403
    )

    # Quem gere edita para todos; desligada, some para quem não gere.
    r = client.put(
        f"/api/skills/company/{sid}",
        json={"content": _md("tom-de-voz", "Tom novo")},
        headers=admin,
    )
    assert r.status_code == 200 and r.json()["revision"] == 2
    assert client.get("/api/skills/company", headers=mh).json()[0]["description"] == "Tom novo"
    assert client.post(f"/api/skills/company/{sid}/disable", headers=admin).json()["status"] == (
        "disabled"
    )
    assert client.get("/api/skills/company", headers=mh).json() == []
    assert client.get(f"/api/skills/company/{sid}", headers=mh).status_code == 404
    assert len(client.get("/api/skills/company", headers=admin).json()) == 1
    assert client.post(f"/api/skills/company/{sid}/enable", headers=admin).json()["status"] == (
        "active"
    )

    # Nome não muda; prefixo u- é das pessoais; duplicado é recusado; segredo também.
    assert (
        client.put(
            f"/api/skills/company/{sid}", json={"content": _md("outro")}, headers=admin
        ).status_code
        == 422
    )
    assert (
        client.post("/api/skills/company", json={"content": _md("u-x")}, headers=admin).status_code
        == 422
    )
    dup = client.post("/api/skills/company", json={"content": _md("tom-de-voz")}, headers=admin)
    assert dup.status_code == 422 and dup.json()["problems"][0]["code"] == "duplicate_name"
    leak = client.post(
        "/api/skills/company",
        json={"content": _md("vaza", body="senha: Zx9Qw3Er7Ty1U\n")},
        headers=admin,
    )
    assert leak.status_code == 422

    assert client.delete(f"/api/skills/company/{sid}", headers=admin).status_code == 200
    assert client.get("/api/skills/company", headers=admin).json() == []


def test_company_skill_isolated_between_tenants(
    client, master_token, enabled, tenant_admin, other_tenant_admin
):
    admin = auth(tenant_admin["token"])
    sid = client.post(
        "/api/skills/company", json={"content": _md("interna")}, headers=admin
    ).json()["id"]
    _enable(client, master_token, other_tenant_admin["tenant"])
    oh = auth(other_tenant_admin["token"])
    assert client.get("/api/skills/company", headers=oh).json() == []
    assert client.get(f"/api/skills/company/{sid}", headers=oh).status_code == 404
    assert client.delete(f"/api/skills/company/{sid}", headers=oh).status_code == 404
    # Mesmo nome em outra empresa é livre.
    assert (
        client.post("/api/skills/company", json={"content": _md("interna")}, headers=oh).status_code
        == 201
    )


def test_company_skill_listed_next_to_template_ones_and_blocks_template_name(
    client, enabled, tenant_admin
):
    h = auth(tenant_admin["token"])
    t = _skills_template(client, tenant_admin["token"], "Financeiro")
    _publish(
        client,
        tenant_admin["token"],
        t,
        [
            {"path": "AGENTE.md", "content": "# Agente\n"},
            {"path": "skills/cobranca/SKILL.md", "content": SKILL_MD},
        ],
    )
    client.post("/api/skills/company", json={"content": _md("saudacao")}, headers=h)
    rows = client.get("/api/skills/company", headers=h).json()
    by = {r["name"]: r for r in rows}
    assert by["cobranca"]["scope"] == "template" and by["cobranca"]["editable"] is False
    assert by["saudacao"]["scope"] == "company" and by["saudacao"]["editable"] is True
    # Nome que já existe no pacote de um agente também é recusado.
    dup = client.post("/api/skills/company", json={"content": _md("cobranca")}, headers=h)
    assert dup.status_code == 422


def test_company_skills_reach_the_run_package(client, enabled, tenant_admin, fake_harness):
    from tests.test_skills_agents import _setup_runtime

    h = auth(tenant_admin["token"])
    t = _setup_runtime(client, tenant_admin)
    on = client.post("/api/skills/company", json={"content": _md("saudacao")}, headers=h).json()
    off = client.post("/api/skills/company", json={"content": _md("desligada")}, headers=h).json()
    client.post(f"/api/skills/company/{off['id']}/disable", headers=h)
    # spec 11 §4: só entram as skills vinculadas ao agente
    client.put(f"/api/skills/agent/{t['id']}", json={"skill_ids": [on["id"], off["id"]]}, headers=h)
    client.post("/api/chat/send", json={"message": "oi", "template_id": t["id"]}, headers=h)
    pkg = fake_harness.calls["runs"][-1]["skills"]["tenant_package"]
    paths = [f["path"] for f in pkg["files"]]
    assert "skills/saudacao/SKILL.md" in paths and "skills/cobranca/SKILL.md" in paths
    assert "skills/desligada/SKILL.md" not in paths
    assert on["id"]


def test_my_skills_create_and_isolation(client, master_token, enabled, tenant_admin):
    a = auth(tenant_admin["token"])
    b_user = _new_user(client, master_token, enabled, "B")
    b = auth(b_user["token"])
    r = client.post("/api/me/skills", json={"content": _md("u-resumo")}, headers=a)
    assert r.status_code == 201 and r.json()["status"] == "active" and r.json()["source"] == "user"
    sid = r.json()["id"]
    assert (
        client.post("/api/me/skills", json={"content": _md("u-resumo")}, headers=a).status_code
        == 409
    )
    assert (
        client.post("/api/me/skills", json={"content": _md("resumo")}, headers=a).status_code == 422
    )
    # B nunca vê nem mexe na de A; pode ter uma com o mesmo nome.
    assert client.get("/api/me/skills", headers=b).json() == []
    assert client.get(f"/api/me/skills/{sid}", headers=b).status_code == 404
    assert client.post(f"/api/me/skills/{sid}/disable", headers=b).status_code == 404
    assert client.delete(f"/api/me/skills/{sid}", headers=b).status_code == 404
    assert (
        client.post("/api/me/skills", json={"content": _md("u-resumo")}, headers=b).status_code
        == 201
    )
    # E as pessoais não vazam para a lista da empresa.
    viewer = auth(_viewer(client, master_token, enabled)["token"])
    assert client.get("/api/skills/company", headers=viewer).json() == []
    assert client.post(f"/api/me/skills/{sid}/disable", headers=a).json()["status"] == "disabled"


# -------------------------------------------------------------- rotinas ---


def test_cron_from_frequency():
    f = scheduler.cron_from_frequency
    assert f({"kind": "daily", "time": "08:30"}) == "30 8 * * *"
    assert f({"kind": "weekly", "time": "09:00", "weekdays": [5, 1, 1]}) == "0 9 * * 1,5"
    assert f({"kind": "monthly", "time": "07:15", "day": 10}) == "15 7 10 * *"
    assert f({"kind": "hourly", "minute": 20}) == "20 * * * *"
    for bad in (
        {"kind": "daily", "time": "25:00"},
        {"kind": "daily", "time": "abc"},
        {"kind": "weekly", "time": "09:00", "weekdays": []},
        {"kind": "monthly", "time": "09:00", "day": 31},
        {"kind": "x"},
        None,
    ):
        with pytest.raises(HTTPException):
            f(bad)


def _routine_body(template, **kw):
    return {
        "name": "Resumo diário",
        "instruction": "Resuma as vendas de ontem",
        "template_id": template["id"],
        "timezone": "America/Sao_Paulo",
        "frequency": {"kind": "daily", "time": "08:00"},
        **kw,
    }


def test_create_routine_from_screen(client, master_token, enabled, tenant_admin):
    from tests.test_skills_agents import _setup_runtime

    h = auth(tenant_admin["token"])
    t = _setup_runtime(client, tenant_admin)
    r = client.post("/api/schedules", json=_routine_body(t), headers=h)
    assert r.status_code == 201, r.text
    row = r.json()
    assert row["cron"] == "0 8 * * *" and row["status"] == "active" and row["next_run_at"]
    assert row["template_name"] == t["name"]

    # Cron direto, pausada ao criar; intervalo curto recusado; agente inexistente recusado.
    body = _routine_body(t, name="Semanal", cron="0 9 * * 1", active=False)
    body.pop("frequency")
    paused = client.post("/api/schedules", json=body, headers=h).json()
    assert paused["status"] == "paused" and paused["next_run_at"] is None
    short = _routine_body(t, name="Rápida", cron="*/5 * * * *")
    assert client.post("/api/schedules", json=short, headers=h).status_code == 400
    ghost = _routine_body(t, template_id="00000000-0000-0000-0000-000000000000")
    assert client.post("/api/schedules", json=ghost, headers=h).status_code == 404
    assert (
        client.post("/api/schedules", json=_routine_body(t, name=" "), headers=h).status_code == 400
    )

    # Editar muda horário e próxima execução; retomar/pausar seguem valendo.
    sid = row["id"]
    up = client.put(
        f"/api/schedules/{sid}",
        json={"frequency": {"kind": "weekly", "time": "10:30", "weekdays": [1, 3]}, "name": "Novo"},
        headers=h,
    ).json()
    assert up["cron"] == "30 10 * * 1,3" and up["name"] == "Novo" and up["status"] == "active"
    assert (
        client.put(f"/api/schedules/{sid}", json={"active": False}, headers=h).json()["status"]
        == "paused"
    )
    assert client.post(f"/api/schedules/{sid}/resume", headers=h).json()["next_run_at"]

    # Isolamento: outro usuário não vê, não edita, não vê histórico.
    other = _new_user(client, master_token, enabled)
    oh = auth(other["token"])
    assert client.get("/api/schedules", headers=oh).json()["schedules"] == []
    assert client.put(f"/api/schedules/{sid}", json={"name": "x"}, headers=oh).status_code == 404
    assert client.get(f"/api/schedules/{sid}/runs", headers=oh).status_code == 404

    assert client.delete(f"/api/schedules/{sid}", headers=h).status_code == 200


def test_routine_history_records_each_run(client, enabled, tenant_admin):
    from tests.test_skills_agents import _setup_runtime

    h = auth(tenant_admin["token"])
    t = _setup_runtime(client, tenant_admin)
    sid = client.post("/api/schedules", json=_routine_body(t), headers=h).json()["id"]
    assert client.get(f"/api/schedules/{sid}/runs", headers=h).json() == {"runs": []}
    _sql(
        "UPDATE scheduled_jobs SET next_run_at = now() - interval '1 minute' WHERE id::text = %s",
        (sid,),
    )
    claimed = scheduler._claim_due()
    assert len(claimed) == 1 and claimed[0]["history_id"]
    runs = client.get(f"/api/schedules/{sid}/runs", headers=h).json()["runs"]
    assert len(runs) == 1 and runs[0]["status"] == "running" and runs[0]["finished_at"] is None
    row = client.get("/api/schedules", headers=h).json()["schedules"][0]
    assert row["run_count"] == 1 and row["next_run_at"]


# ---------------------------------------------------------------- caixas ---


def _fake_bridge(fail=False):
    app = FastAPI()
    seen: dict = {"calls": []}

    @app.post("/admin/ai-config")
    async def ai_config(request: Request):
        seen["calls"].append(await request.json())
        if fail:
            raise HTTPException(status_code=500, detail="boom")
        return {"status": "ok", "autopilot": True, "agent_bot": "ligado"}

    @app.get("/admin/ai-config/{tenant_id}")
    async def listing(tenant_id: str):
        return {
            "default": None,
            "inboxes": [
                {
                    "chatwoot_inbox_id": 11,
                    "name": "Site",
                    "channel_type": "Channel::Api",
                    "ai": None,
                    "inherits_default": False,
                },
            ],
        }

    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=0, log_level="error"))
    threading.Thread(target=server.run, daemon=True).start()
    while not server.started:
        pass
    return server, server.servers[0].sockets[0].getsockname()[1], seen


@pytest.fixture
def bridge(monkeypatch):
    made = []

    def make(fail=False):
        server, port, seen = _fake_bridge(fail)
        made.append(server)
        monkeypatch.setattr(settings, "bridge_url", f"http://127.0.0.1:{port}")
        monkeypatch.setattr(settings, "bridge_admin_token", "token-ponte")
        return seen

    yield make
    for server in made:
        server.should_exit = True


def test_inbox_binding_saves_in_engine_and_syncs_bridge(client, enabled, tenant_admin, bridge):
    from tests.test_skills_agents import _setup_runtime

    seen = bridge()
    h = auth(tenant_admin["token"])
    t = _setup_runtime(client, tenant_admin)
    r = client.put(
        "/api/omnichannel/inboxes/11/ia",
        json={"template_id": t["id"], "autopilot": True},
        headers=h,
    )
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "ok" and seen["calls"][0]["template_id"] == t["id"]
    ia = client.get("/api/omnichannel/inboxes", headers=h).json()["inboxes"][0]["ai"]
    assert ia["template_id"] == t["id"] and ia["template_name"] == t["name"]
    assert ia["degraded"] is False

    # Desvincular (template nulo) também grava.
    r = client.put("/api/omnichannel/inboxes/11/ia", json={"template_id": None}, headers=h)
    assert r.status_code == 200 and r.json()["status"] == "ok"
    assert (
        client.get("/api/omnichannel/inboxes", headers=h).json()["inboxes"][0]["ai"]["template_id"]
        is None
    )


def test_inbox_binding_survives_a_bridge_failure(client, enabled, tenant_admin, bridge):
    from tests.test_skills_agents import _setup_runtime

    bridge(fail=True)
    h = auth(tenant_admin["token"])
    t = _setup_runtime(client, tenant_admin)
    r = client.put("/api/omnichannel/inboxes/11/ia", json={"template_id": None}, headers=h)
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "degraded" and r.json()["saved"] is True
    r = client.put("/api/omnichannel/inboxes/11/ia", json={"template_id": t["id"]}, headers=h)
    assert r.status_code == 200 and r.json()["status"] == "degraded"
    ia = client.get("/api/omnichannel/inboxes", headers=h).json()["inboxes"][0]["ai"]
    assert ia["template_id"] == t["id"] and ia["degraded"] is True and ia["degraded_reason"]


def test_inbox_binding_survives_a_dead_bridge(client, enabled, tenant_admin, monkeypatch):
    monkeypatch.setattr(settings, "bridge_url", "http://127.0.0.1:1")
    monkeypatch.setattr(settings, "bridge_admin_token", "token-ponte")
    h = auth(tenant_admin["token"])
    r = client.put("/api/omnichannel/inboxes/11/ia", json={"template_id": None}, headers=h)
    assert r.status_code == 200 and r.json()["status"] == "degraded"


def test_inbox_binding_rejects_foreign_or_bad_template(
    client, master_token, enabled, tenant_admin, other_tenant_admin, bridge
):
    from tests.test_skills_agents import _skills_template

    bridge()
    _enable(client, master_token, other_tenant_admin["tenant"])
    foreign = _skills_template(client, other_tenant_admin["token"])
    h = auth(tenant_admin["token"])
    r = client.put("/api/omnichannel/inboxes/11/ia", json={"template_id": foreign["id"]}, headers=h)
    assert r.status_code == 404
    r = client.put("/api/omnichannel/inboxes/11/ia", json={"template_id": "nao-e-uuid"}, headers=h)
    assert r.status_code == 400
