import uuid

import psycopg
import pytest
from app.config import settings
from tests.conftest import auth

pytestmark = pytest.mark.integration


def test_secret_value_is_encrypted_and_never_returned(client, tenant_admin):
    name = f"TOKEN_{uuid.uuid4().hex[:6]}"
    r = client.post(
        "/api/secrets",
        json={"name": name, "value": "valor-confidencial-999"},
        headers=auth(tenant_admin["token"]),
    )
    assert r.status_code == 201
    assert "valor-confidencial-999" not in r.text

    listing = client.get("/api/secrets", headers=auth(tenant_admin["token"]))
    assert "valor-confidencial-999" not in listing.text

    with psycopg.connect(settings.database_url) as conn:
        stored = conn.execute("SELECT value_encrypted FROM secrets").fetchall()
    assert all("valor-confidencial-999" not in (s[0] or "") for s in stored)


def test_secret_name_rules_and_uniqueness(client, tenant_admin):
    h = auth(tenant_admin["token"])
    bad = client.post("/api/secrets", json={"name": "nome ruim!", "value": "x"}, headers=h)
    assert bad.status_code == 400
    name = f"DUP_{uuid.uuid4().hex[:6]}"
    first = client.post("/api/secrets", json={"name": name, "value": "a"}, headers=h)
    second = client.post("/api/secrets", json={"name": name, "value": "b"}, headers=h)
    assert first.status_code == 201
    assert second.status_code == 409


def test_secrets_are_tenant_isolated(client, master_token, tenant_admin):
    other = client.post(
        "/api/tenants",
        json={"name": "Sec", "tenant_key": f"sec-{uuid.uuid4().hex[:6]}"},
        headers=auth(master_token),
    ).json()
    client.post(
        "/api/secrets",
        json={"name": "DO_OUTRO", "value": "x", "tenant_id": other["id"]},
        headers=auth(master_token),
    )
    visible = client.get("/api/secrets", headers=auth(tenant_admin["token"])).json()
    assert all(s["name"] != "DO_OUTRO" for s in visible)


def test_run_payload_carries_tools_secrets_and_servers(client, tenant_admin):
    """template_runtime must decrypt secrets and include per-agent tools and
    external MCP servers in the kernel payload."""
    h = auth(tenant_admin["token"])
    client.post("/api/secrets", json={"name": "API_KEY_X", "value": "segredo-x"}, headers=h)

    tpl = client.post(
        "/api/templates",
        json={"name": f"t-{uuid.uuid4().hex[:6]}", "description": "d"},
        headers=h,
    ).json()
    v = client.post(
        f"/api/templates/{tpl['id']}/versions",
        json={
            "supervisor_prompt": "Coordene.",
            "agents": [
                {
                    "name": "api_agent",
                    "description": "chama APIs",
                    "prompt": "Você chama APIs.",
                    "tools": ["call_http_api", "calculate"],
                }
            ],
            "mcp_servers": [
                {"name": "ext1", "url": "http://mcp.example.com/mcp", "auth_token": "tok-ext"}
            ],
        },
        headers=h,
    ).json()
    client.post(f"/api/templates/{tpl['id']}/deploy", json={"version_id": v["id"]}, headers=h)

    from app.template_runtime import build_run_payload

    payload = build_run_payload(tenant_admin["user"]["tenant_id"], tpl["id"])
    assert payload["agents"][0]["tools"] == ["call_http_api", "calculate"]
    assert payload["secrets"]["API_KEY_X"] == "segredo-x"
    assert payload["mcp_servers"] == [
        {"name": "ext1", "url": "http://mcp.example.com/mcp", "auth_token": "tok-ext"}
    ]
    assert payload["tenant_id"] == str(tenant_admin["user"]["tenant_id"])
