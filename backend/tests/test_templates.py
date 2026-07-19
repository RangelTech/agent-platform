import json
import uuid

import pytest
from tests.conftest import auth

pytestmark = pytest.mark.integration


def _version_payload(**overrides):
    body = {
        "supervisor_prompt": "Você coordena especialistas.",
        "max_steps": 5,
        "agents": [
            {
                "name": "financeiro_agent",
                "description": "questões financeiras",
                "prompt": "Você é o financeiro.",
            },
            {
                "name": "rh_agent",
                "description": "questões de RH",
                "prompt": "Você é o RH.",
            },
        ],
    }
    body.update(overrides)
    return body


@pytest.fixture
def template(client, tenant_admin):
    r = client.post(
        "/api/templates",
        json={"name": f"tpl-{uuid.uuid4().hex[:6]}", "description": "Atendimento"},
        headers=auth(tenant_admin["token"]),
    )
    assert r.status_code == 201, r.text
    return r.json()


def test_version_lifecycle_and_deploy(client, tenant_admin, template):
    h = auth(tenant_admin["token"])
    v1 = client.post(
        f"/api/templates/{template['id']}/versions", json=_version_payload(), headers=h
    ).json()
    assert v1["version_number"] == 1

    v2 = client.post(
        f"/api/templates/{template['id']}/versions",
        json=_version_payload(supervisor_prompt="Prompt novo."),
        headers=h,
    ).json()
    assert v2["version_number"] == 2

    # Deploy v2, then roll back to v1.
    assert (
        client.post(
            f"/api/templates/{template['id']}/deploy",
            json={"version_id": v2["id"]},
            headers=h,
        ).status_code
        == 200
    )
    listed = client.get("/api/templates", headers=h).json()
    me = next(t for t in listed if t["id"] == template["id"])
    assert me["active_version_number"] == 2

    client.post(
        f"/api/templates/{template['id']}/deploy", json={"version_id": v1["id"]}, headers=h
    )
    listed = client.get("/api/templates", headers=h).json()
    me = next(t for t in listed if t["id"] == template["id"])
    assert me["active_version_number"] == 1


def test_version_is_immutable_snapshot(client, tenant_admin, template):
    h = auth(tenant_admin["token"])
    v1 = client.post(
        f"/api/templates/{template['id']}/versions", json=_version_payload(), headers=h
    ).json()
    detail = client.get(
        f"/api/templates/{template['id']}/versions/{v1['id']}", headers=h
    ).json()
    assert [a["name"] for a in detail["agents"]] == ["financeiro_agent", "rh_agent"]
    assert detail["max_steps"] == 5


def test_agent_names_are_validated(client, tenant_admin, template):
    h = auth(tenant_admin["token"])
    bad = _version_payload(
        agents=[{"name": "Nome Inválido", "description": "x", "prompt": "x"}]
    )
    r = client.post(f"/api/templates/{template['id']}/versions", json=bad, headers=h)
    assert r.status_code == 400

    dup = _version_payload()
    dup["agents"].append(dup["agents"][0])
    r = client.post(f"/api/templates/{template['id']}/versions", json=dup, headers=h)
    assert r.status_code == 400


def test_ai_service_must_belong_to_the_tenant(client, master_token, tenant_admin, template):
    other = client.post(
        "/api/tenants",
        json={"name": "Outra", "tenant_key": f"o-{uuid.uuid4().hex[:6]}"},
        headers=auth(master_token),
    ).json()
    foreign_service = client.post(
        "/api/ai-services",
        json={
            "name": "alheio",
            "provider": "gemini",
            "model": "gemini-flash-latest",
            "api_key": "k",
            "tenant_id": other["id"],
        },
        headers=auth(master_token),
    ).json()

    payload = _version_payload(supervisor_ai_service_id=foreign_service["id"])
    r = client.post(
        f"/api/templates/{template['id']}/versions",
        json=payload,
        headers=auth(tenant_admin["token"]),
    )
    assert r.status_code == 400


def test_chat_sends_the_template_run_payload(client, master_token, tenant_admin, template):
    """The kernel must receive supervisor+agents from the deployed version."""
    import threading

    import uvicorn
    from fastapi import FastAPI
    from fastapi.responses import StreamingResponse

    captured = {}
    app = FastAPI()

    @app.post("/v1/runs")
    async def runs(payload: dict):
        captured.update(payload)

        async def stream():
            yield 'event: done\ndata: {"text": "ok"}\n\n'

        return StreamingResponse(stream(), media_type="text/event-stream")

    config = uvicorn.Config(app, host="127.0.0.1", port=0, log_level="error")
    server = uvicorn.Server(config)
    threading.Thread(target=server.run, daemon=True).start()
    while not server.started:
        pass
    port = server.servers[0].sockets[0].getsockname()[1]

    h = auth(tenant_admin["token"])
    v = client.post(
        f"/api/templates/{template['id']}/versions", json=_version_payload(), headers=h
    ).json()
    client.post(
        f"/api/templates/{template['id']}/deploy", json={"version_id": v["id"]}, headers=h
    )

    email = f"u-{uuid.uuid4().hex[:6]}@acme.com"
    client.post(
        "/api/users",
        json={"email": email, "name": "U", "password": "senha-forte-123"},
        headers=h,
    )
    utoken = client.post(
        "/api/auth/login", json={"email": email, "password": "senha-forte-123"}
    ).json()["token"]

    from app.config import settings as backend_settings

    backend_settings.kernel_url = f"http://127.0.0.1:{port}"
    with client.stream(
        "POST",
        "/api/chat/send",
        json={"message": "oi", "template_id": template["id"]},
        headers=auth(utoken),
    ) as response:
        "".join(response.iter_text())
    server.should_exit = True

    assert captured["supervisor"]["prompt"] == "Você coordena especialistas."
    assert [a["name"] for a in captured["agents"]] == ["financeiro_agent", "rh_agent"]
    assert captured["max_steps"] == 5
    assert json.dumps(captured)


def test_chat_rejects_template_of_another_tenant(
    client, master_token, tenant_admin, template, fake_kernel
):
    other = client.post(
        "/api/tenants",
        json={"name": "X", "tenant_key": f"x-{uuid.uuid4().hex[:6]}"},
        headers=auth(master_token),
    ).json()
    email = f"x-{uuid.uuid4().hex[:6]}@x.com"
    client.post(
        "/api/users",
        json={
            "email": email,
            "name": "X",
            "password": "senha-forte-123",
            "tenant_id": other["id"],
        },
        headers=auth(master_token),
    )
    xtoken = client.post(
        "/api/auth/login", json={"email": email, "password": "senha-forte-123"}
    ).json()["token"]

    with client.stream(
        "POST",
        "/api/chat/send",
        json={"message": "oi", "template_id": template["id"]},
        headers=auth(xtoken),
    ) as response:
        status = response.status_code
    assert status == 404
