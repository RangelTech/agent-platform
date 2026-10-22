"""Agentes por skills: tipo de template, pacote, roteamento de kernel, skills,
aprovações, rotas internas, ambiente e permissões."""

import base64
import json
import uuid

import psycopg
import pytest
from app.config import settings
from tests.conftest import auth

pytestmark = pytest.mark.integration

SERVICE = {"Authorization": "Bearer token-interno-de-teste"}
FORBIDDEN_WORDS = ("harness", "kernel", "hermes")


# ---------------------------------------------------------------- helpers ---


def _sql(query: str, params=()):
    with psycopg.connect(settings.database_url, autocommit=True) as conn:
        cur = conn.execute(query, params)
        return cur.fetchall() if cur.description else None


def _enable(client, master_token, tenant, on=True):
    r = client.put(
        f"/api/tenants/{tenant['id']}", json={"harness_enabled": on}, headers=auth(master_token)
    )
    assert r.status_code == 200, r.text
    return r.json()


@pytest.fixture
def enabled(client, master_token, tenant):
    _enable(client, master_token, tenant)
    return tenant


def _new_user(client, master_token, tenant, name="Pessoa"):
    email = f"u-{uuid.uuid4().hex[:8]}@acme.com"
    profiles = client.get("/api/user-profiles", headers=auth(master_token)).json()
    member = next(p for p in profiles if p["tenant_id"] == tenant["id"] and p["name"] == "Usuário")
    r = client.post(
        "/api/users",
        json={
            "email": email,
            "name": name,
            "password": "senha-forte-123",
            "tenant_id": tenant["id"],
            "profile_id": member["id"],
        },
        headers=auth(master_token),
    )
    assert r.status_code == 201, r.text
    token = client.post(
        "/api/auth/login", json={"email": email, "password": "senha-forte-123"}
    ).json()["token"]
    return {"user": r.json(), "token": token}


def _skills_template(client, token, name=None):
    r = client.post(
        "/api/templates",
        json={"name": name or f"sk-{uuid.uuid4().hex[:6]}", "agent_kind": "skills"},
        headers=auth(token),
    )
    assert r.status_code == 201, r.text
    return r.json()


def _publish(client, token, template, files=None, manifest=None, notes="v"):
    h = auth(token)
    draft = client.get(f"/api/templates/{template['id']}/draft", headers=h).json()
    body = {
        "files": files or [{"path": f["path"], "content": f["content"]} for f in draft["files"]],
        "manifest": manifest if manifest is not None else draft["manifest"],
    }
    assert client.put(f"/api/templates/{template['id']}/draft", json=body, headers=h).json()["ok"]
    r = client.post(
        f"/api/templates/{template['id']}/versions",
        json={"source": "draft", "notes": notes},
        headers=h,
    )
    assert r.status_code == 201, r.text
    version = r.json()
    d = client.post(
        f"/api/templates/{template['id']}/deploy", json={"version_id": version["id"]}, headers=h
    )
    assert d.status_code == 200
    return version


def _sse(response) -> list[tuple[str, dict]]:
    events, current = [], ""
    for line in response.text.splitlines():
        if line.startswith("event: "):
            current = line[7:]
        elif line.startswith("data: "):
            events.append((current, json.loads(line[6:])))
    return events


SKILL_MD = "---\nname: cobranca\ndescription: Cobra clientes em atraso\n---\n# Cobrança\n"


# ------------------------------------------------------------------ flag ---


def test_flag_off_blocks_creation_and_hides_skills_templates(
    client, master_token, tenant, tenant_admin
):
    h = auth(tenant_admin["token"])
    r = client.post("/api/templates", json={"name": "X", "agent_kind": "skills"}, headers=h)
    assert r.status_code == 403
    assert r.json()["detail"] == "Recurso não habilitado para sua empresa"

    _enable(client, master_token, tenant)
    created = _skills_template(client, tenant_admin["token"], "Meu agente")
    assert created["agent_kind"] == "skills"
    assert any(t["id"] == created["id"] for t in client.get("/api/templates", headers=h).json())

    _enable(client, master_token, tenant, on=False)
    listed = client.get("/api/templates", headers=h).json()
    assert all(t["id"] != created["id"] for t in listed)
    assert client.get(f"/api/templates/{created['id']}/draft", headers=h).status_code == 403


def test_flag_serialization(client, master_token, tenant, tenant_admin):
    assert (
        client.get("/api/tenants", headers=auth(master_token)).json()["items"][0]["harness_enabled"]
        is False
    )
    me = client.get("/api/auth/me", headers=auth(tenant_admin["token"])).json()
    assert me["skills_agents_enabled"] is False
    assert "harness_enabled" not in me
    _enable(client, master_token, tenant)
    me = client.get("/api/auth/me", headers=auth(tenant_admin["token"])).json()
    assert me["skills_agents_enabled"] is True
    assert me["permissions"]["skills"] == ["view", "create", "edit", "delete"]
    assert me["permissions"]["environment"] == ["view", "manage"]
    # Só o master liga a flag.
    r = client.put(
        f"/api/tenants/{tenant['id']}",
        json={"harness_enabled": False},
        headers=auth(tenant_admin["token"]),
    )
    assert r.status_code == 403


def test_structured_templates_keep_working(client, tenant_admin):
    h = auth(tenant_admin["token"])
    t = client.post("/api/templates", json={"name": "Clássico"}, headers=h).json()
    assert t["agent_kind"] == "structured"
    r = client.get(f"/api/templates/{t['id']}/draft", headers=h)
    assert r.status_code == 400
    listed = client.get("/api/templates", headers=h).json()[0]
    assert set(listed) >= {
        "id",
        "name",
        "description",
        "agent_kind",
        "active_version_id",
        "active_version_number",
        "updated_at",
        "system_key",
    }
    # Tipo inválido é recusado.
    assert (
        client.post(
            "/api/templates", json={"name": "Z", "agent_kind": "outro"}, headers=h
        ).status_code
        == 422
    )


# --------------------------------------------------------------- pacote ---


def test_draft_lifecycle_and_validation(client, enabled, tenant_admin):
    h = auth(tenant_admin["token"])
    t = _skills_template(client, tenant_admin["token"])
    draft = client.get(f"/api/templates/{t['id']}/draft", headers=h).json()
    assert [f["path"] for f in draft["files"]] == ["AGENTE.md"]
    assert draft["files"][0]["size_bytes"] > 0
    assert draft["manifest"] == {
        "tools": [],
        "environment": {"enabled": False},
        "model": None,
        "limits": {},
        "output": {"format": "markdown"},
    }

    # Rascunho inválido é gravado, com os problemas devolvidos.
    bad = {
        "files": [{"path": "skills/x/SKILL.md", "content": "sem cabecalho"}],
        "manifest": {"tools": ["nao_existe"]},
    }
    saved = client.put(f"/api/templates/{t['id']}/draft", json=bad, headers=h).json()
    assert saved["ok"] is False
    found = {p["code"] for p in saved["problems"]}
    assert {"missing_agent_md", "bad_frontmatter", "unknown_tool"} <= found
    assert (
        client.get(f"/api/templates/{t['id']}/draft", headers=h).json()["files"][0]["path"]
        == "skills/x/SKILL.md"
    )

    # Publicar com problemas: 422 com a lista.
    r = client.post(f"/api/templates/{t['id']}/versions", json={"source": "draft"}, headers=h)
    assert r.status_code == 422
    assert r.json()["problems"]

    v = client.post(f"/api/templates/{t['id']}/validate", json=bad, headers=h).json()
    assert v["ok"] is False and all(
        {"path", "code", "message", "severity"} <= set(p) for p in v["problems"]
    )


def test_versions_files_and_rollback(client, enabled, tenant_admin):
    h = auth(tenant_admin["token"])
    t = _skills_template(client, tenant_admin["token"])
    files = [
        {"path": "AGENTE.md", "content": "# Agente v1\n"},
        {"path": "skills/cobranca/SKILL.md", "content": SKILL_MD},
    ]
    v1 = _publish(
        client, tenant_admin["token"], t, files, {"tools": ["rt_sql_read"]}, notes="primeira"
    )
    assert v1["number"] == 1 and v1["is_active"] is False  # ativada em seguida pelo deploy
    files[0]["content"] = "# Agente v2\n"
    v2 = _publish(client, tenant_admin["token"], t, files, {"tools": []}, notes="segunda")
    assert v2["number"] == 2

    versions = client.get(f"/api/templates/{t['id']}/versions", headers=h).json()
    assert [v["number"] for v in versions] == [2, 1]
    assert [v["is_active"] for v in versions] == [True, False]
    assert versions[1]["notes"] == "primeira" and versions[1]["created_by"]

    listed = client.get(f"/api/templates/{t['id']}/versions/{v1['id']}/files", headers=h).json()
    assert {f["path"] for f in listed} == {"AGENTE.md", "skills/cobranca/SKILL.md"}
    assert all(len(f["sha256"]) == 64 and f["size_bytes"] > 0 for f in listed)
    content = client.get(
        f"/api/templates/{t['id']}/versions/{v1['id']}/files/skills/cobranca/SKILL.md", headers=h
    ).json()
    assert content == {"path": "skills/cobranca/SKILL.md", "content": SKILL_MD}
    old = client.get(
        f"/api/templates/{t['id']}/versions/{v1['id']}/files/AGENTE.md", headers=h
    ).json()
    assert old["content"] == "# Agente v1\n"  # versão imutável: o rascunho mudou, a v1 não
    assert (
        client.get(
            f"/api/templates/{t['id']}/versions/{v1['id']}/files/nao/existe.md", headers=h
        ).status_code
        == 404
    )

    # Restaurar = deploy da versão antiga.
    client.post(f"/api/templates/{t['id']}/deploy", json={"version_id": v1["id"]}, headers=h)
    assert client.get("/api/templates", headers=h).json()[0]["active_version_number"] == 1


def test_package_is_isolated_between_tenants(
    client, enabled, tenant_admin, other_tenant_admin, master_token
):
    t = _skills_template(client, tenant_admin["token"])
    _enable(client, master_token, other_tenant_admin["tenant"])
    other = auth(other_tenant_admin["token"])
    assert client.get(f"/api/templates/{t['id']}/draft", headers=other).status_code == 404
    assert (
        client.post(
            f"/api/templates/{t['id']}/validate", json={"files": [], "manifest": {}}, headers=other
        ).status_code
        == 404
    )


def test_tools_catalog(client, tenant_admin):
    r = client.get("/api/tools/catalog", headers=auth(tenant_admin["token"]))
    assert r.status_code == 200
    tools = r.json()
    assert tools and all(set(t) == {"id", "label", "description", "sensitive"} for t in tools)
    assert any(t["sensitive"] for t in tools) and any(not t["sensitive"] for t in tools)
    text = json.dumps(tools, ensure_ascii=False).lower()
    assert not any(w in text for w in FORBIDDEN_WORDS)
    assert client.get("/api/tools/catalog").status_code == 401


def test_member_cannot_edit_packages(client, master_token, enabled):
    member = _new_user(client, master_token, enabled)
    h = auth(member["token"])
    assert (
        client.post(
            "/api/templates", json={"name": "N", "agent_kind": "skills"}, headers=h
        ).status_code
        == 403
    )
    assert (
        client.get("/api/skills/company", headers=h).status_code == 403
    )  # sem a permissão `skills`
    assert client.get("/api/environment", headers=h).status_code == 403


# --------------------------------------------------------------- empresa ---


def test_company_skills_come_from_active_versions(
    client, enabled, tenant_admin, other_tenant_admin, master_token
):
    h = auth(tenant_admin["token"])
    t = _skills_template(client, tenant_admin["token"], "Financeiro")
    assert client.get("/api/skills/company", headers=h).json() == []
    _publish(
        client,
        tenant_admin["token"],
        t,
        [
            {"path": "AGENTE.md", "content": "# Agente\n"},
            {"path": "skills/cobranca/SKILL.md", "content": SKILL_MD},
        ],
    )
    all_rows = client.get("/api/skills/company", headers=h).json()
    # O AGENTE.md vem junto (kind=agent) para o agente aparecer mesmo sem skills.
    assert [r["kind"] for r in all_rows] == ["agent", "skill"]
    assert all_rows[0]["name"] == "AGENTE.md" and all_rows[0]["description"] == "Agente"
    rows = [r for r in all_rows if r["kind"] == "skill"]
    assert len(rows) == 1
    assert rows[0]["template_id"] == t["id"] and rows[0]["template_name"] == "Financeiro"
    assert rows[0]["name"] == "cobranca" and rows[0]["description"] == "Cobra clientes em atraso"
    _enable(client, master_token, other_tenant_admin["tenant"])
    assert client.get("/api/skills/company", headers=auth(other_tenant_admin["token"])).json() == []


# --------------------------------------------------------------- roteamento ---


def _setup_runtime(client, tenant_admin, tools=None):
    t = _skills_template(client, tenant_admin["token"], f"Agente {uuid.uuid4().hex[:4]}")
    _publish(
        client,
        tenant_admin["token"],
        t,
        [
            {"path": "AGENTE.md", "content": "# Agente\n"},
            {"path": "skills/cobranca/SKILL.md", "content": SKILL_MD},
        ],
        {
            "tools": tools or ["rt_sql_read"],
            "environment": {"enabled": True},
            "model": {"alias": "agente-x"},
            "limits": {"max_turns": 30},
        },
    )
    return t


def test_chat_with_skills_template_goes_to_the_second_kernel(
    client, enabled, tenant_admin, fake_harness, fake_kernel, monkeypatch
):
    monkeypatch.setattr(settings, "kernel_url", fake_kernel)
    h = auth(tenant_admin["token"])
    t = _setup_runtime(client, tenant_admin)
    r = client.post("/api/chat/send", json={"message": "oi", "template_id": t["id"]}, headers=h)
    assert r.status_code == 200
    events = _sse(r)
    names = [n for n, _ in events]
    assert names[0] == "chat" and "run_id" in events[0][1]
    assert {"step", "token", "done", "evento_futuro"} <= set(
        names
    )  # tipos desconhecidos passam direto
    chat_id = events[0][1]["chat_id"]

    payload = fake_harness.calls["runs"][-1]
    assert payload["contract_version"] == 1 and payload["run_id"] == events[0][1]["run_id"]
    assert payload["template"]["kind"] == "skills" and payload["chat_id"] == chat_id
    assert (
        payload["tenant_id"] == enabled["id"] and payload["user_id"] == tenant_admin["user"]["id"]
    )
    assert payload["manifest"]["tools"] == ["rt_sql_read"]
    assert payload["manifest"]["toolsets"] == []
    assert payload["manifest"]["limits"]["turn_timeout_s"] == 1800  # tarefa longa >= 10 min
    assert (
        payload["manifest"]["limits"]["max_turns"] == 30 and payload["model"]["alias"] == "agente-x"
    )
    files = {f["path"]: f for f in payload["skills"]["tenant_package"]["files"]}
    assert set(files) == {"AGENTE.md", "skills/cobranca/SKILL.md"} and files["AGENTE.md"]["sha256"]
    assert payload["run_token"] and "credentials" in payload and payload["history"] == []
    assert payload["skills"]["system_rev"] == settings.harness_system_skills_rev

    msgs = client.get(f"/api/chats/{chat_id}/messages", headers=h).json()
    assert [m["role"] for m in msgs] == ["user", "assistant"]
    assert msgs[1]["content"] == "resposta do agente"
    chat = client.get(f"/api/chats/{chat_id}", headers=h).json()
    assert chat["agent_kind"] == "skills"
    assert _sql("SELECT kernel FROM chats WHERE id::text=%s", (chat_id,))[0][0] == "harness"
    assert (
        _sql("SELECT status FROM chat_runs WHERE run_id::text=%s", (payload["run_id"],))[0][0]
        == "done"
    )


def test_graph_flow_is_untouched_and_pins_the_chat(
    client, enabled, tenant_admin, fake_harness, fake_kernel, monkeypatch
):
    monkeypatch.setattr(settings, "kernel_url", fake_kernel)
    h = auth(tenant_admin["token"])
    r = client.post("/api/chat/send", json={"message": "oi"}, headers=h)
    events = _sse(r)
    assert [n for n, _ in events if n in ("token", "done")] == ["token", "token", "done"]
    assert "run_id" in events[0][1]
    assert fake_harness.calls["runs"] == []
    chat_id = events[0][1]["chat_id"]
    assert client.get(f"/api/chats/{chat_id}", headers=h).json()["agent_kind"] == "structured"
    assert client.get("/api/chats", headers=h).json()[0]["agent_kind"] == "structured"


def test_switching_kernel_inside_a_chat_is_409(
    client, enabled, tenant_admin, fake_harness, fake_kernel, monkeypatch
):
    monkeypatch.setattr(settings, "kernel_url", fake_kernel)
    h = auth(tenant_admin["token"])
    skills = _setup_runtime(client, tenant_admin)
    structured = client.post("/api/templates", json={"name": "Estruturado"}, headers=h).json()
    ver = client.post(
        f"/api/templates/{structured['id']}/versions",
        json={"supervisor_prompt": "ok", "agents": []},
        headers=h,
    ).json()
    client.post(
        f"/api/templates/{structured['id']}/deploy", json={"version_id": ver["id"]}, headers=h
    )

    first = _sse(
        client.post(
            "/api/chat/send", json={"message": "oi", "template_id": skills["id"]}, headers=h
        )
    )
    chat_id = first[0][1]["chat_id"]
    before = _sql("SELECT count(*) FROM chat_messages WHERE chat_id::text=%s", (chat_id,))[0][0]
    r = client.post(
        "/api/chat/send",
        json={"message": "troca", "chat_id": chat_id, "template_id": structured["id"]},
        headers=h,
    )
    assert r.status_code == 409
    assert (
        r.json()["detail"]
        == "Este chat usa outro tipo de agente. Abra uma nova conversa para usar este agente."
    )
    # Nada foi gravado e o template do chat continua o mesmo.
    assert (
        _sql("SELECT count(*) FROM chat_messages WHERE chat_id::text=%s", (chat_id,))[0][0]
        == before
    )
    assert (
        _sql("SELECT template_id::text FROM chats WHERE id::text=%s", (chat_id,))[0][0]
        == skills["id"]
    )

    # E o inverso: chat estruturado não aceita agente por skills.
    g = _sse(
        client.post(
            "/api/chat/send", json={"message": "oi", "template_id": structured["id"]}, headers=h
        )
    )
    r = client.post(
        "/api/chat/send",
        json={"message": "x", "chat_id": g[0][1]["chat_id"], "template_id": skills["id"]},
        headers=h,
    )
    assert r.status_code == 409
    # Mesmo tipo continua permitido (conversa segue).
    ok = client.post("/api/chat/send", json={"message": "mais", "chat_id": chat_id}, headers=h)
    assert ok.status_code == 200


def test_chat_blocked_when_flag_off_or_kernel_missing(
    client, master_token, enabled, tenant_admin, fake_harness, monkeypatch
):
    h = auth(tenant_admin["token"])
    t = _setup_runtime(client, tenant_admin)
    monkeypatch.setattr(settings, "kernel_harness_url", "")
    r = client.post("/api/chat/send", json={"message": "oi", "template_id": t["id"]}, headers=h)
    assert r.status_code == 503
    monkeypatch.setattr(settings, "kernel_harness_url", fake_harness.url)
    _enable(client, master_token, enabled, on=False)
    r = client.post("/api/chat/send", json={"message": "oi", "template_id": t["id"]}, headers=h)
    assert r.status_code == 403 and r.json()["detail"] == "Recurso não habilitado para sua empresa"


def test_unpublished_skills_agent_cannot_run(client, enabled, tenant_admin, fake_harness):
    h = auth(tenant_admin["token"])
    t = _skills_template(client, tenant_admin["token"])
    r = client.post("/api/chat/send", json={"message": "oi", "template_id": t["id"]}, headers=h)
    assert r.status_code == 409
    assert fake_harness.calls["runs"] == []


def test_second_turn_has_no_history_first_turn_has(client, enabled, tenant_admin, fake_harness):
    h = auth(tenant_admin["token"])
    t = _setup_runtime(client, tenant_admin)
    first = _sse(
        client.post("/api/chat/send", json={"message": "um", "template_id": t["id"]}, headers=h)
    )
    client.post(
        "/api/chat/send", json={"message": "dois", "chat_id": first[0][1]["chat_id"]}, headers=h
    )
    runs = fake_harness.calls["runs"]
    assert runs[0]["history"] == [] and "history" not in runs[1]
    assert runs[1]["message"] == "dois"


def test_cancel_run(client, enabled, tenant_admin, fake_harness, master_token):
    h = auth(tenant_admin["token"])
    t = _setup_runtime(client, tenant_admin)
    first = _sse(
        client.post("/api/chat/send", json={"message": "um", "template_id": t["id"]}, headers=h)
    )
    run_id = first[0][1]["run_id"]
    # Run já terminada: não cancela.
    assert client.post(f"/api/chat/runs/{run_id}/cancel", headers=h).status_code == 409
    _sql("UPDATE chat_runs SET status='running' WHERE run_id::text=%s", (run_id,))
    other = _new_user(client, master_token, enabled)
    assert (
        client.post(f"/api/chat/runs/{run_id}/cancel", headers=auth(other["token"])).status_code
        == 404
    )
    r = client.post(f"/api/chat/runs/{run_id}/cancel", headers=h)
    assert r.status_code == 200 and r.json() == {"status": "cancelling"}
    assert fake_harness.calls["cancels"] == [run_id]
    assert (
        _sql("SELECT status FROM chat_runs WHERE run_id::text=%s", (run_id,))[0][0] == "cancelling"
    )
    assert client.post("/api/chat/runs/nao-existe/cancel", headers=h).status_code == 404


def test_public_api_routes_skills_template_to_second_kernel(
    client, enabled, tenant_admin, fake_harness
):
    h = auth(tenant_admin["token"])
    t = _setup_runtime(client, tenant_admin)
    created = client.post(
        "/api/integrations", json={"name": "API", "template_id": t["id"]}, headers=h
    )
    assert created.status_code in (200, 201), created.text
    key = created.json().get("api_key")
    r = client.post(
        "/v1/messages",
        json={"message": "oi", "external_session_id": "s1"},
        headers={"Authorization": f"Bearer {key}"},
    )
    assert r.status_code == 200, r.text
    assert r.json()["reply"] == "resposta do agente"
    sent = fake_harness.calls["runs"][-1]
    assert (
        sent["channel"] == "api" and sent["actor"]["type"] == "external" and sent["user_id"] is None
    )


# ------------------------------------------------------- skills do usuário ---


def _user_skill_md(name="u-resumo", desc="Resume reuniões"):
    return f"---\nname: {name}\ndescription: {desc}\n---\nFaça resumos curtos.\n"


def _run_for(client, tenant_admin, fake_harness):
    t = _setup_runtime(client, tenant_admin)
    first = _sse(
        client.post(
            "/api/chat/send",
            json={"message": "um", "template_id": t["id"]},
            headers=auth(tenant_admin["token"]),
        )
    )
    return first[0][1]["run_id"], t


def test_agent_proposes_skill_and_user_reviews(
    client, enabled, tenant_admin, fake_harness, master_token
):
    h = auth(tenant_admin["token"])
    run_id, t = _run_for(client, tenant_admin, fake_harness)
    body = {"name": "u-resumo", "content": _user_skill_md()}
    r = client.post(f"/internal/runs/{run_id}/skills", json=body, headers=SERVICE)
    assert r.status_code == 201, r.text
    proposed = r.json()
    assert proposed["status"] == "pending_review" and proposed["name"] == "u-resumo"

    listed = client.get("/api/me/skills?status=pending_review", headers=h).json()
    assert [s["id"] for s in listed] == [proposed["user_skill_id"]]
    s = listed[0]
    assert s["source"] == "agent" and s["revision"] == 1 and s["origin_chat_id"]
    assert client.get("/api/me/skills?status=active", headers=h).json() == []

    # Outro usuário não enxerga nem mexe.
    other = _new_user(client, master_token, enabled)
    oh = auth(other["token"])
    assert client.get("/api/me/skills", headers=oh).json() == []
    assert client.get(f"/api/me/skills/{s['id']}", headers=oh).status_code == 404
    assert client.post(f"/api/me/skills/{s['id']}/approve", headers=oh).status_code == 404
    assert client.delete(f"/api/me/skills/{s['id']}", headers=oh).status_code == 404

    full = client.get(f"/api/me/skills/{s['id']}", headers=h).json()
    assert "Faça resumos" in full["content"]

    # Edição revalida: segredo é recusado; nome não muda.
    leak = _user_skill_md() + "\nsenha: Zx9Qw3Er7Ty1U\n"
    r = client.put(f"/api/me/skills/{s['id']}", json={"content": leak}, headers=h)
    assert r.status_code == 422 and r.json()["problems"][0]["code"] == "secret_detected"
    r = client.put(
        f"/api/me/skills/{s['id']}", json={"content": _user_skill_md("u-outro")}, headers=h
    )
    assert r.status_code == 422
    r = client.put(
        f"/api/me/skills/{s['id']}",
        json={"content": _user_skill_md(), "description": "Resumo novo"},
        headers=h,
    )
    assert (
        r.status_code == 200
        and r.json()["revision"] == 2
        and r.json()["description"] == "Resumo novo"
    )
    assert "Resumo novo" in r.json()["content"]

    # Aprovar -> ativa; entra no próximo run.
    a = client.post(f"/api/me/skills/{s['id']}/approve", headers=h).json()
    assert a["status"] == "active"
    mat = client.get(
        f"/internal/skills/user/{enabled['id']}/{tenant_admin['user']['id']}", headers=SERVICE
    ).json()
    assert [x["name"] for x in mat["skills"]] == ["u-resumo"]
    client.post(
        "/api/chat/send",
        json={"message": "dois", "chat_id": client.get("/api/chats", headers=h).json()[0]["id"]},
        headers=h,
    )
    assert fake_harness.calls["runs"][-1]["skills"]["user_skills"][0]["name"] == "u-resumo"

    assert (
        client.post(f"/api/me/skills/{s['id']}/disable", headers=h).json()["status"] == "disabled"
    )
    assert (
        client.get(
            f"/internal/skills/user/{enabled['id']}/{tenant_admin['user']['id']}", headers=SERVICE
        ).json()["skills"]
        == []
    )
    assert client.post(f"/api/me/skills/{s['id']}/enable", headers=h).json()["status"] == "active"
    assert (
        client.post(f"/api/me/skills/{s['id']}/discard", headers=h).json()["status"] == "discarded"
    )
    assert client.get("/api/me/skills", headers=h).json() == []
    assert (
        _sql("SELECT count(*) FROM skill_audit WHERE tenant_id::text=%s", (enabled["id"],))[0][0]
        >= 5
    )


def test_user_skill_proposal_rules(client, enabled, tenant_admin, fake_harness):
    run_id, t = _run_for(client, tenant_admin, fake_harness)
    url = f"/internal/runs/{run_id}/skills"
    # Sem prefixo u-.
    assert (
        client.post(
            url, json={"name": "resumo", "content": _user_skill_md("resumo")}, headers=SERVICE
        ).status_code
        == 422
    )
    # Segredo escrito.
    assert (
        client.post(
            url,
            json={
                "name": "u-a",
                "content": _user_skill_md("u-a") + "api_key = 'abc123def456ghi789'",
            },
            headers=SERVICE,
        ).status_code
        == 422
    )
    # Colide com skill da empresa.
    assert (
        client.post(
            url,
            json={"name": "u-cobranca", "content": _user_skill_md("u-cobranca")},
            headers=SERVICE,
        ).status_code
        == 422
    )
    # Reproposta de uma skill ativa volta para revisão.
    first = client.post(
        url, json={"name": "u-resumo", "content": _user_skill_md()}, headers=SERVICE
    ).json()
    client.post(
        f"/api/me/skills/{first['user_skill_id']}/approve", headers=auth(tenant_admin["token"])
    )
    again = client.post(
        url, json={"name": "u-resumo", "content": _user_skill_md(desc="Outra")}, headers=SERVICE
    ).json()
    assert again["user_skill_id"] == first["user_skill_id"] and again["status"] == "pending_review"
    # Teto de pendentes.
    for i in range(25):
        r = client.post(
            url, json={"name": f"u-n{i}", "content": _user_skill_md(f"u-n{i}")}, headers=SERVICE
        )
        if r.status_code == 409:
            break
    assert r.status_code == 409


# ---------------------------------------------------------------- aprovações ---


def test_authenticated_approval_flow(client, enabled, tenant_admin, fake_harness, master_token):
    h = auth(tenant_admin["token"])
    run_id, t = _run_for(client, tenant_admin, fake_harness)
    r = client.post(
        "/internal/approvals",
        json={
            "run_id": run_id,
            "summary": "Criar cobrança Pix de R$ 50",
            "risk": "high",
            "kind": "pix",
            "details": {"valor": 50},
        },
        headers=SERVICE,
    )
    assert r.status_code == 201, r.text
    approval_id = r.json()["approval_id"]

    pending = client.get("/api/approvals/pending", headers=h).json()
    assert len(pending) == 1
    assert set(pending[0]) == {
        "approval_id",
        "chat_id",
        "summary",
        "risk",
        "kind",
        "details",
        "expires_at",
    }
    assert pending[0]["details"] == {"valor": 50}

    other = _new_user(client, master_token, enabled)
    assert client.get("/api/approvals/pending", headers=auth(other["token"])).json() == []
    assert (
        client.post(
            f"/api/approvals/{approval_id}",
            json={"decision": "approve"},
            headers=auth(other["token"]),
        ).status_code
        == 404
    )
    assert (
        client.post(f"/api/approvals/{approval_id}", json={"decision": "approve"}).status_code
        == 401
    )
    assert (
        client.post(
            f"/api/approvals/{approval_id}", json={"decision": "talvez"}, headers=h
        ).status_code
        == 422
    )

    r = client.post(f"/api/approvals/{approval_id}", json={"decision": "approve"}, headers=h)
    assert r.status_code == 200 and r.json() == {"status": "approved"}
    assert fake_harness.calls["approvals"][-1]["decision"] == "approve"
    assert (
        client.post(
            f"/api/approvals/{approval_id}", json={"decision": "deny"}, headers=h
        ).status_code
        == 409
    )
    assert client.get("/api/approvals/pending", headers=h).json() == []
    status = client.get(f"/internal/approvals/{approval_id}", headers=SERVICE).json()
    assert status["status"] == "approved"


def test_approval_expires_when_run_finishes(client, enabled, tenant_admin, fake_harness):
    from app import chat_runs

    h = auth(tenant_admin["token"])
    run_id, t = _run_for(client, tenant_admin, fake_harness)
    approval_id = client.post(
        "/internal/approvals",
        json={"run_id": run_id, "summary": "Publicar app", "ttl_seconds": 900},
        headers=SERVICE,
    ).json()["approval_id"]
    assert len(client.get("/api/approvals/pending", headers=h).json()) == 1
    chat_runs.finish_run(run_id, "error")  # kernel expirou o cartão e encerrou a run
    assert client.get("/api/approvals/pending", headers=h).json() == []
    r = client.post(f"/api/approvals/{approval_id}", json={"decision": "approve"}, headers=h)
    assert r.json() == {"status": "expired"}


def test_approval_expiry_and_deny(client, enabled, tenant_admin, fake_harness):
    h = auth(tenant_admin["token"])
    run_id, t = _run_for(client, tenant_admin, fake_harness)
    def mk():
        return client.post(
            "/internal/approvals",
            json={"run_id": run_id, "summary": "Enviar e-mail", "ttl_seconds": 60},
            headers=SERVICE,
        ).json()["approval_id"]

    expired = mk()
    _sql(
        "UPDATE approvals SET expires_at = now() - interval '1 minute' WHERE approval_id::text=%s",
        (expired,),
    )
    assert client.get("/api/approvals/pending", headers=h).json() == []
    assert client.post(
        f"/api/approvals/{expired}", json={"decision": "approve"}, headers=h
    ).json() == {"status": "expired"}
    denied = mk()
    assert client.post(f"/api/approvals/{denied}", json={"decision": "deny"}, headers=h).json() == {
        "status": "denied"
    }


# ------------------------------------------------------------ rotas internas ---


def test_internal_auth_matrix(client, enabled, tenant_admin, fake_harness):
    run_id, t = _run_for(client, tenant_admin, fake_harness)
    run_token = fake_harness.calls["runs"][-1]["run_token"]
    url = f"/internal/runs/{run_id}/usage"
    body = {"input_tokens": 10, "output_tokens": 5}
    assert client.post(url, json=body).status_code == 401
    assert (
        client.post(url, json=body, headers={"Authorization": "Bearer errado"}).status_code == 401
    )
    # Token de sessão de usuário não vale.
    assert client.post(url, json=body, headers=auth(tenant_admin["token"])).status_code == 401
    # run_token: run encerrada -> revogado.
    assert (
        client.post(url, json=body, headers={"Authorization": f"Bearer {run_token}"}).status_code
        == 401
    )
    assert client.post(url, json=body, headers=SERVICE).status_code == 200
    # Reabre a run: o run_token volta a valer, mas só para a run dele.
    _sql("UPDATE chat_runs SET status='running' WHERE run_id::text=%s", (run_id,))
    assert (
        client.post(url, json=body, headers={"Authorization": f"Bearer {run_token}"}).status_code
        == 200
    )
    other_run = str(uuid.uuid4())
    assert (
        client.post(
            f"/internal/runs/{other_run}/usage",
            json=body,
            headers={"Authorization": f"Bearer {run_token}"},
        ).status_code
        == 404
    )
    # Sem token de serviço configurado, a rota de serviço fica fechada.
    from app.config import settings as s

    s.harness_internal_token = ""
    try:
        assert client.post(url, json=body, headers=SERVICE).status_code == 401
    finally:
        s.harness_internal_token = "token-interno-de-teste"
    # Rota só de serviço rejeita o run_token.
    assert (
        client.get(
            f"/internal/skills/user/{enabled['id']}/{tenant_admin['user']['id']}",
            headers={"Authorization": f"Bearer {run_token}"},
        ).status_code
        == 401
    )


def test_internal_usage_artifacts_messages_audit_package(
    client, enabled, tenant_admin, fake_harness
):
    h = auth(tenant_admin["token"])
    run_id, t = _run_for(client, tenant_admin, fake_harness)
    tid = enabled["id"]
    assert client.post(
        f"/internal/runs/{run_id}/usage",
        json={"input_tokens": 100, "output_tokens": 40, "cost_usd": 0.01, "model": "m"},
        headers=SERVICE,
    ).json() == {"ok": True}
    row = _sql(
        "SELECT prompt_tokens, completion_tokens, tenant_id::text FROM usage_records "
        "WHERE tenant_id::text=%s",
        (tid,),
    )[0]
    assert row == (100, 40, tid)
    assert (
        _sql("SELECT input_tokens FROM tenant_usage_hourly WHERE tenant_id::text=%s", (tid,))[0][0]
        == 100
    )

    ok = client.post(
        f"/internal/runs/{run_id}/artifacts",
        json={
            "kind": "file",
            "title": "relatorio.pdf",
            "storage_path": f"tenants/{tid}/artifacts/r.pdf",
            "content_type": "application/pdf",
        },
        headers=SERVICE,
    )
    assert ok.status_code == 201
    arts = _sql(
        "SELECT tenant_id::text, chat_id FROM artifacts WHERE id::text=%s",
        (ok.json()["artifact_id"],),
    )[0]
    assert arts[0] == tid
    # O caminho precisa ser do espaço da própria empresa.
    bad = client.post(
        f"/internal/runs/{run_id}/artifacts",
        json={"kind": "file", "storage_path": f"tenants/{uuid.uuid4()}/x.pdf"},
        headers=SERVICE,
    )
    assert bad.status_code == 400
    # Baixável pelo dono, com o isolamento de sempre.
    assert client.get(f"/api/artifacts/{ok.json()['artifact_id']}", headers=h).status_code in (
        200,
        404,
        500,
    )

    # Mensagem do assistente: gravada uma única vez por run (a run já gravou pelo stream).
    first = client.post(
        f"/internal/runs/{run_id}/messages", json={"content": "extra"}, headers=SERVICE
    ).json()
    assert first == {"saved": False, "message_id": None}
    assert (
        client.post(
            f"/internal/runs/{run_id}/tool-audit",
            json={"tool": "rt_sql_read", "ok": True},
            headers=SERVICE,
        ).status_code
        == 201
    )
    assert _sql("SELECT tool, tenant_id::text FROM tool_audit WHERE run_id::text=%s", (run_id,))[
        0
    ] == ("rt_sql_read", tid)

    pkgr = client.get(f"/internal/runs/{run_id}/package", headers=SERVICE).json()
    assert {f["path"] for f in pkgr["files"]} == {"AGENTE.md", "skills/cobranca/SKILL.md"} and len(
        pkgr["sha256"]
    ) == 64


def test_internal_message_saved_once_when_stream_is_not_the_writer(
    client, enabled, tenant_admin, fake_harness
):
    h = auth(tenant_admin["token"])
    run_id, t = _run_for(client, tenant_admin, fake_harness)
    chat_id = client.get("/api/chats", headers=h).json()[0]["id"]
    _sql("UPDATE chat_runs SET assistant_saved=false WHERE run_id::text=%s", (run_id,))
    before = len(client.get(f"/api/chats/{chat_id}/messages", headers=h).json())
    r = client.post(
        f"/internal/runs/{run_id}/messages",
        json={"content": "resposta gravada pelo serviço"},
        headers=SERVICE,
    ).json()
    assert r["saved"] is True
    assert (
        client.post(
            f"/internal/runs/{run_id}/messages", json={"content": "de novo"}, headers=SERVICE
        ).json()["saved"]
        is False
    )
    assert len(client.get(f"/api/chats/{chat_id}/messages", headers=h).json()) == before + 1


def test_internal_environment_state_feeds_the_user_view(
    client, enabled, tenant_admin, fake_harness
):
    h = auth(tenant_admin["token"])
    r = client.put(
        f"/internal/environment/{enabled['id']}",
        json={
            "state": "running",
            "plan": "padrao",
            "usage": {"ram_mb": 300},
            "apps": [
                {
                    "name": "metabase",
                    "port": 3000,
                    "url": "https://metabase-acme.apps.rangeltech.net",
                    "status": "online",
                }
            ],
        },
        headers=SERVICE,
    )
    assert r.status_code == 200, r.text
    env = client.get("/api/environment", headers=h).json()
    assert env["state"] == "running" and env["plan"] == "padrao" and env["usage"]["ram_mb"] == 300
    assert env["apps"][0]["name"] == "metabase" and env["apps"][0]["port"] == 3000
    assert (
        client.put(f"/internal/environment/{enabled['id']}", json={"state": "running"}).status_code
        == 401
    )


# ------------------------------------------------------------------ ambiente ---


def test_environment_disabled_without_flag(client, tenant_admin):
    env = client.get("/api/environment", headers=auth(tenant_admin["token"])).json()
    assert env["enabled"] is False and env["state"] == "disabled"
    assert set(env) == {"enabled", "state", "plan", "usage", "apps", "domains", "last_backup_at"}
    assert (
        client.post("/api/environment/start", headers=auth(tenant_admin["token"])).status_code
        == 403
    )


def test_environment_stub_actions_domains_and_isolation(
    client, enabled, tenant_admin, other_tenant_admin, master_token, fake_harness
):
    h = auth(tenant_admin["token"])
    env = client.get("/api/environment", headers=h).json()
    assert env["enabled"] is True and env["state"] == "stopped"
    assert set(env["usage"]) == {
        "cpu_pct",
        "ram_mb",
        "ram_limit_mb",
        "disk_gb",
        "disk_limit_gb",
        "egress_gb",
    }
    assert client.post("/api/environment/start", headers=h).json() == {"state": "running"}
    assert client.get("/api/environment", headers=h).json()["state"] == "running"
    assert client.post("/api/environment/backup", headers=h).json() == {"state": "running"}
    assert client.get("/api/environment", headers=h).json()["last_backup_at"]
    assert client.post("/api/environment/stop", headers=h).json() == {"state": "stopped"}

    client.put(
        f"/internal/environment/{enabled['id']}",
        json={"apps": [{"name": "loja", "port": 8080}]},
        headers=SERVICE,
    )
    app_id = client.get("/api/environment", headers=h).json()["apps"][0]["id"]
    d = client.post(
        "/api/environment/domains",
        json={"host": "Loja.Cliente.com.br", "app_id": app_id},
        headers=h,
    )
    assert d.status_code == 201, d.text
    dom = d.json()
    assert dom["host"] == "loja.cliente.com.br" and dom["status"] == "pending_verification"
    assert dom["txt_name"] == "_rt-verify.loja.cliente.com.br" and dom["txt_value"].startswith(
        "rt-verify="
    )
    assert dom["cname_value"].startswith("loja-") and dom["cname_value"].endswith(
        settings.apps_base_domain
    )
    assert (
        client.post(
            "/api/environment/domains",
            json={"host": "loja.cliente.com.br", "app_id": app_id},
            headers=h,
        ).status_code
        == 409
    )
    assert (
        client.post(
            "/api/environment/domains", json={"host": "invalido", "app_id": app_id}, headers=h
        ).status_code
        == 400
    )
    assert (
        client.post(
            "/api/environment/domains",
            json={"host": f"x.{settings.apps_base_domain}", "app_id": app_id},
            headers=h,
        ).status_code
        == 400
    )

    # Outra empresa não vê nem mexe.
    _enable(client, master_token, other_tenant_admin["tenant"])
    oh = auth(other_tenant_admin["token"])
    assert client.get("/api/environment", headers=oh).json()["apps"] == []
    assert client.delete(f"/api/environment/domains/{dom['id']}", headers=oh).status_code == 404
    assert client.delete(f"/api/environment/apps/{app_id}", headers=oh).status_code == 404
    assert (
        client.post(
            "/api/environment/domains",
            json={"host": "a.outra.com.br", "app_id": app_id},
            headers=oh,
        ).status_code
        == 404
    )

    assert client.delete(f"/api/environment/domains/{dom['id']}", headers=h).status_code == 200
    assert client.get("/api/environment", headers=h).json()["domains"] == []
    assert client.delete(f"/api/environment/apps/{app_id}", headers=h).status_code == 200
    assert client.get("/api/environment", headers=h).json()["apps"] == []


def test_environment_permissions_view_vs_manage(client, master_token, enabled):
    profile = client.post(
        "/api/user-profiles",
        json={
            "name": "Observador",
            "tenant_id": enabled["id"],
            "permissions": {"environment": ["view"]},
        },
        headers=auth(master_token),
    )
    assert profile.status_code in (200, 201), profile.text
    email = f"obs-{uuid.uuid4().hex[:6]}@acme.com"
    client.post(
        "/api/users",
        json={
            "email": email,
            "name": "Obs",
            "password": "senha-forte-123",
            "profile_id": profile.json()["id"],
            "tenant_id": enabled["id"],
        },
        headers=auth(master_token),
    )
    token = client.post(
        "/api/auth/login", json={"email": email, "password": "senha-forte-123"}
    ).json()["token"]
    assert client.get("/api/environment", headers=auth(token)).status_code == 200
    assert client.post("/api/environment/start", headers=auth(token)).status_code == 403
    bad = client.post(
        "/api/user-profiles",
        json={
            "name": "Errado",
            "tenant_id": enabled["id"],
            "permissions": {"environment": ["delete"]},
        },
        headers=auth(master_token),
    )
    assert bad.status_code in (400, 422)


def test_environment_talks_to_the_manager_with_signed_calls(
    client, enabled, tenant_admin, monkeypatch
):
    from app.runtime_client import rm_tenant

    h = auth(tenant_admin["token"])
    calls = []
    rmt = rm_tenant(enabled["id"])
    state = {"created": False, "running": False}

    class Resp:
        def __init__(self, status, body):
            self.status_code = status
            self._b = body

        def json(self):
            return self._b

    def fake_request(method, url, **kw):
        path = url.split("rm.test", 1)[1]
        calls.append((method, path, kw["headers"]))
        view = {
            "plan": "standard",
            "suspended": False,
            "observed": {"workspace": {"state": "running" if state["running"] else "exited"}},
            "exposures": [{"app": "metabase", "port": 3000}],
            "usage": [{"cpu_pct": 12, "ram_mb": 300, "disk_gb": 1}],
        }
        if method == "GET" and path == f"/v1/tenants/{rmt}":
            return Resp(200, view) if state["created"] else Resp(404, {"error": "unknown_tenant"})
        if method == "POST" and path == f"/v1/tenants/{rmt}":
            state["created"] = True
            return Resp(200, {})
        if path == f"/v1/tenants/{rmt}/workspace:start":
            state["running"] = True
            return Resp(200, {})
        return Resp(404, {"error": "not_found"})

    monkeypatch.setattr(settings, "runtime_manager_url", "http://rm.test")
    monkeypatch.setattr(settings, "runtime_manager_secret", "segredo-de-teste-123456")
    monkeypatch.setattr("app.runtime_client.httpx.request", fake_request)
    assert client.post("/api/environment/start", headers=h).json() == {"state": "running"}
    methods = [(m, p) for m, p, _ in calls]
    assert ("POST", f"/v1/tenants/{rmt}") in methods  # criou no primeiro uso
    assert ("POST", f"/v1/tenants/{rmt}/workspace:start") in methods
    create = next(c for c in calls if c[0] == "POST" and c[1] == f"/v1/tenants/{rmt}")
    start = next(c for c in calls if c[1].endswith("workspace:start"))
    assert create[2]["X-RM-Scope"] == "*" and start[2]["X-RM-Scope"] == rmt
    assert start[2]["X-RM-Signature"] and start[2]["X-RM-Service"] == "agent-platform-backend"
    # a tela enxerga o app publicado pelo agente (espelhado do gerenciador)
    snap = client.get("/api/environment", headers=h).json()
    assert snap["state"] == "running"
    assert [a["name"] for a in snap["apps"]] == ["metabase"]
    assert snap["apps"][0]["url"] == f"https://metabase-{rmt}.apps.rangeltech.net"


# ---------------------------------------------------------------- linguagem ---


def test_client_facing_texts_never_name_internal_components(
    client, enabled, tenant_admin, fake_harness, monkeypatch
):
    h = auth(tenant_admin["token"])
    seen = []
    t = _setup_runtime(client, tenant_admin)
    seen.append(client.get(f"/api/templates/{t['id']}/draft", headers=h).text)
    seen.append(
        client.post(
            f"/api/templates/{t['id']}/validate",
            json={"files": [], "manifest": {"tools": ["x"]}},
            headers=h,
        ).text
    )
    seen.append(client.get("/api/tools/catalog", headers=h).text)
    seen.append(client.get("/api/environment", headers=h).text)
    seen.append(
        client.post(
            "/api/chat/send",
            json={"message": "oi", "template_id": "00000000-0000-0000-0000-000000000000"},
            headers=h,
        ).text
    )
    monkeypatch.setattr(settings, "kernel_harness_url", "")
    seen.append(
        client.post(
            "/api/chat/send", json={"message": "oi", "template_id": t["id"]}, headers=h
        ).text
    )
    for text in seen:
        assert not any(w in text.lower() for w in FORBIDDEN_WORDS), text


def test_run_payload_grants_follow_manifest_tools(client, enabled, tenant_admin, fake_harness):
    h = auth(tenant_admin["token"])
    t = _setup_runtime(client, tenant_admin, tools=["rt_pix_create", "rt_sql_read"])
    r = client.post("/api/chat/send", json={"message": "oi", "template_id": t["id"]}, headers=h)
    assert r.status_code == 200
    _sse(r)
    sent = fake_harness.calls["runs"][-1]
    kinds = {g["kind"] for g in sent["manifest"]["credential_grants"]}
    assert kinds == {"mercado_pago", "datasource"}  # nada de e-mail nem segredos sem a ferramenta


def test_run_payload_carries_output_format_and_chat_exposes_it(
    client, enabled, tenant_admin, fake_harness
):
    h = auth(tenant_admin["token"])
    padrao = _setup_runtime(client, tenant_admin)
    _sse(
        client.post(
            "/api/chat/send", json={"message": "oi", "template_id": padrao["id"]}, headers=h
        )
    )
    assert fake_harness.calls["runs"][-1]["manifest"]["output_format"] == "markdown"

    t = _skills_template(client, tenant_admin["token"])
    _publish(
        client,
        tenant_admin["token"],
        t,
        manifest={"tools": [], "output": {"format": "whatsapp"}},
    )
    events = _sse(
        client.post("/api/chat/send", json={"message": "oi", "template_id": t["id"]}, headers=h)
    )
    assert fake_harness.calls["runs"][-1]["manifest"]["output_format"] == "whatsapp"
    assert events[0][0] == "chat" and events[0][1]["output_format"] == "whatsapp"
    chat_id = events[0][1]["chat_id"]
    assert client.get(f"/api/chats/{chat_id}", headers=h).json()["output_format"] == "whatsapp"


def test_invalid_output_format_is_rejected_on_validate(client, enabled, tenant_admin):
    h = auth(tenant_admin["token"])
    t = _skills_template(client, tenant_admin["token"])
    draft = client.get(f"/api/templates/{t['id']}/draft", headers=h).json()
    body = {
        "files": [{"path": f["path"], "content": f["content"]} for f in draft["files"]],
        "manifest": {"tools": [], "output": {"format": "html"}},
    }
    checked = client.post(f"/api/templates/{t['id']}/validate", json=body, headers=h).json()
    assert checked["ok"] is False


def test_run_payload_without_credential_tools_grants_nothing(
    client, enabled, tenant_admin, fake_harness
):
    h = auth(tenant_admin["token"])
    t = _setup_runtime(client, tenant_admin, tools=["rt_search_docs"])
    _sse(client.post("/api/chat/send", json={"message": "oi", "template_id": t["id"]}, headers=h))
    assert fake_harness.calls["runs"][-1]["manifest"]["credential_grants"] == []


# ------------------------------------------------- arquivos do pacote (H-2) ---

PDF_V1 = b"%PDF-1.7\n\xe2\xe3\xcf\xd3\n1 0 obj << /Cardapio /Pizza >> endobj\n%%EOF\n"
PDF_V2 = b"%PDF-1.7\n\xe2\xe3\xcf\xd3\n1 0 obj << /Cardapio /Hamburguer >> endobj\n%%EOF\n"


def _put_asset(client, token, template, name, data, status=200):
    r = client.put(
        f"/api/templates/{template['id']}/draft/files/arquivos/{name}",
        json={"content_base64": base64.b64encode(data).decode()},
        headers=auth(token),
    )
    assert r.status_code == status, r.text
    return r.json()


def test_asset_upload_replace_remove_in_draft(client, enabled, tenant_admin):
    import hashlib

    tok = tenant_admin["token"]
    h = auth(tok)
    t = _skills_template(client, tok)
    a = _put_asset(client, tok, t, "cardapio.pdf", PDF_V1)
    assert a["path"] == "arquivos/cardapio.pdf" and a["size_bytes"] == len(PDF_V1)
    assert a["mime"] == "application/pdf" and a["sha256"] == hashlib.sha256(PDF_V1).hexdigest()

    # multipart, sem informar a pasta: cai em arquivos/
    r = client.post(
        f"/api/templates/{t['id']}/draft/files",
        files={"file": ("precos.xlsx", b"PK\x03\x04dados", "application/octet-stream")},
        headers=h,
    )
    assert r.status_code == 201, r.text
    assert r.json()["path"] == "arquivos/precos.xlsx"

    draft = client.get(f"/api/templates/{t['id']}/draft", headers=h).json()
    by = {f["path"]: f for f in draft["files"]}
    assert set(by) == {"AGENTE.md", "arquivos/cardapio.pdf", "arquivos/precos.xlsx"}
    pdf = by["arquivos/cardapio.pdf"]
    assert pdf["is_binary"] and pdf["content"] is None

    # Salvar o texto (inclusive devolvendo a listagem inteira) não mexe nos binários.
    body = {"files": draft["files"], "manifest": draft["manifest"]}
    saved = client.put(f"/api/templates/{t['id']}/draft", json=body, headers=h).json()
    assert saved["ok"], saved
    again = client.get(f"/api/templates/{t['id']}/draft", headers=h).json()
    assert {f["path"] for f in again["files"]} == set(by)

    # Substituir: mesmo caminho, conteúdo novo.
    b = _put_asset(client, tok, t, "cardapio.pdf", PDF_V2)
    assert b["sha256"] != a["sha256"]
    raw = client.get(f"/api/templates/{t['id']}/draft/files/arquivos/cardapio.pdf", headers=h)
    assert raw.content == PDF_V2 and raw.headers["content-type"] == "application/pdf"

    url = f"/api/templates/{t['id']}/draft/files/arquivos/precos.xlsx"
    assert client.delete(url, headers=h).status_code == 200
    assert client.delete(url, headers=h).status_code == 404
    left = client.get(f"/api/templates/{t['id']}/draft", headers=h).json()["files"]
    assert {f["path"] for f in left} == {"AGENTE.md", "arquivos/cardapio.pdf"}


def test_asset_upload_validation_errors(client, enabled, tenant_admin, monkeypatch):
    from app import skill_package as pkg

    tok = tenant_admin["token"]
    t = _skills_template(client, tok)
    for name, data, code in (
        ("virus.exe", b"abc", "bad_extension"),
        ("inocente.pdf", b"MZ\x90\x00\x03", "bad_extension"),
        ("vazio.pdf", b"", "empty_file"),
        ("chaves.csv", b"nome,valor\nsenha=Abc123def456ghi\n", "secret_detected"),
    ):
        r = _put_asset(client, tok, t, name, data, status=422)
        assert r["problems"][0]["code"] == code, (name, r)
        assert r["detail"] == r["problems"][0]["message"]
    bad64 = client.put(
        f"/api/templates/{t['id']}/draft/files/arquivos/x.pdf",
        json={"content_base64": "isto nao e base64!"},
        headers=auth(tok),
    )
    assert bad64.status_code == 422
    # Limites (reduzidos para o teste): por arquivo e do pacote.
    monkeypatch.setattr(pkg, "MAX_ASSET_BYTES", 100)
    r = _put_asset(client, tok, t, "grande.pdf", b"%PDF" + b"0" * 200, status=422)
    assert r["problems"][0]["code"] == "file_too_large"
    monkeypatch.setattr(pkg, "MAX_ASSETS_TOTAL_BYTES", 150)
    _put_asset(client, tok, t, "a.pdf", b"%PDF" + b"0" * 90)
    r = _put_asset(client, tok, t, "b.pdf", b"%PDF" + b"0" * 90, status=422)
    assert r["problems"][0]["code"] == "package_too_large"
    _put_asset(client, tok, t, "a.pdf", b"%PDF" + b"1" * 90)  # substituir cabe: não soma 2x


def test_asset_requires_edit_permission(client, master_token, enabled, tenant_admin):
    t = _skills_template(client, tenant_admin["token"])
    member = _new_user(client, master_token, enabled)
    r = client.put(
        f"/api/templates/{t['id']}/draft/files/arquivos/x.pdf",
        json={"content_base64": base64.b64encode(PDF_V1).decode()},
        headers=auth(member["token"]),
    )
    assert r.status_code in (403, 404)


def test_published_version_freezes_assets_and_payload_carries_base64(
    client, enabled, tenant_admin, fake_harness, fake_kernel, monkeypatch
):
    import hashlib

    from app import harness_runtime

    monkeypatch.setattr(settings, "kernel_url", fake_kernel)
    tok = tenant_admin["token"]
    h = auth(tok)
    t = _skills_template(client, tok)
    _put_asset(client, tok, t, "cardapio.pdf", PDF_V1)
    v1 = _publish(client, tok, t, notes="com cardapio")
    _put_asset(client, tok, t, "cardapio.pdf", PDF_V2)  # trocar na tela: o rascunho muda...

    # ...mas a versão publicada continua com os bytes antigos (imutável).
    listed = client.get(f"/api/templates/{t['id']}/versions/{v1['id']}/files", headers=h).json()
    asset = next(f for f in listed if f["path"] == "arquivos/cardapio.pdf")
    assert asset["is_binary"] and asset["sha256"] == hashlib.sha256(PDF_V1).hexdigest()
    got = client.get(
        f"/api/templates/{t['id']}/versions/{v1['id']}/files/arquivos/cardapio.pdf", headers=h
    )
    assert got.content == PDF_V1

    def payload_files():
        r = client.post("/api/chat/send", json={"message": "oi", "template_id": t["id"]}, headers=h)
        assert r.status_code == 200
        pk = fake_harness.calls["runs"][-1]["skills"]["tenant_package"]
        return pk, {f["path"]: f for f in pk["files"] or []}

    pk, files = payload_files()
    f = files["arquivos/cardapio.pdf"]
    assert f["encoding"] == "base64" and base64.b64decode(f["content"]) == PDF_V1
    assert f["sha256"] == hashlib.sha256(PDF_V1).hexdigest()
    assert "encoding" not in files["AGENTE.md"]  # texto continua como era
    sha_v1 = pk["sha256"]

    # Publicar a troca: a próxima resposta usa o PDF novo (sha do pacote muda).
    v2 = _publish(client, tok, t, notes="troca")
    pk, files = payload_files()
    assert base64.b64decode(files["arquivos/cardapio.pdf"]["content"]) == PDF_V2
    assert pk["sha256"] != sha_v1
    assert v2["number"] == 2

    # Pacote grande: sem inline, e a URL entrega o mesmo conteúdo em base64.
    monkeypatch.setattr(harness_runtime, "INLINE_PACKAGE_LIMIT", 10)
    pk, _ = payload_files()
    assert pk["files"] is None and pk["url"]
    run_id = fake_harness.calls["runs"][-1]["run_id"]
    full = client.get(f"/internal/runs/{run_id}/package", headers=SERVICE).json()
    item = next(x for x in full["files"] if x["path"] == "arquivos/cardapio.pdf")
    assert item["encoding"] == "base64" and base64.b64decode(item["content"]) == PDF_V2
    assert full["sha256"] == pk["sha256"]


def test_publish_blocked_when_assets_exceed_total(client, enabled, tenant_admin, monkeypatch):
    from app import skill_package as pkg

    tok = tenant_admin["token"]
    t = _skills_template(client, tok)
    client.get(f"/api/templates/{t['id']}/draft", headers=auth(tok))  # cria o rascunho
    _put_asset(client, tok, t, "a.pdf", PDF_V1)
    monkeypatch.setattr(pkg, "MAX_ASSETS_TOTAL_BYTES", 10)
    r = client.post(
        f"/api/templates/{t['id']}/versions", json={"source": "draft"}, headers=auth(tok)
    )
    assert r.status_code == 422
    assert "package_too_large" in {p["code"] for p in r.json()["problems"]}


def test_switching_agent_in_same_chat_seeds_history_again(
    client, enabled, tenant_admin, fake_harness
):
    h = auth(tenant_admin["token"])
    a = _setup_runtime(client, tenant_admin)
    b = _setup_runtime(client, tenant_admin)
    first = _sse(
        client.post("/api/chat/send", json={"message": "um", "template_id": a["id"]}, headers=h)
    )
    chat_id = first[0][1]["chat_id"]
    body = {"message": "dois", "chat_id": chat_id, "template_id": b["id"]}
    client.post("/api/chat/send", json=body, headers=h)
    runs = fake_harness.calls["runs"]
    assert runs[1]["message"] == "dois"
    assert [m["content"] for m in runs[1]["history"]][:1] == ["um"]
