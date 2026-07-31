"""MCP Store: curadoria pelo master, ativação por tenant, isolamento."""

import uuid

import psycopg
import pytest
from app.config import settings
from tests.conftest import auth

pytestmark = pytest.mark.integration


def _publish(client, master_token, slug=None, **overrides):
    payload = {
        "slug": slug or f"item_{uuid.uuid4().hex[:8]}",
        "name": "CRM Externo",
        "description": "Consulta de clientes no CRM.",
        "category": "vendas",
        "icon": "◆",
        "server_url": "https://crm.example/mcp?token={{credential:api_token}}",
        "auth_token_template": "{{credential:api_token}}",
        "required_credentials": [{"key": "api_token", "label": "Token do CRM", "secret": True}],
    }
    payload.update(overrides)
    return client.post("/api/mcp-store/catalog", json=payload, headers=auth(master_token))


def test_only_the_platform_admin_curates_the_catalog(client, master_token, tenant_admin):
    ok = _publish(client, master_token)
    assert ok.status_code == 201, ok.text

    denied = _publish(client, tenant_admin["token"])
    assert denied.status_code == 403


def test_tenant_sees_only_active_items(client, master_token, tenant_admin):
    item = _publish(client, master_token).json()
    visible = client.get("/api/mcp-store/catalog", headers=auth(tenant_admin["token"])).json()
    assert any(i["id"] == item["id"] for i in visible)

    client.delete(f"/api/mcp-store/catalog/{item['id']}", headers=auth(master_token))
    visible = client.get("/api/mcp-store/catalog", headers=auth(tenant_admin["token"])).json()
    assert all(i["id"] != item["id"] for i in visible)


def test_activation_requires_the_declared_credentials(client, master_token, tenant_admin):
    item = _publish(client, master_token).json()
    r = client.put(
        f"/api/mcp-store/activations/{item['id']}",
        json={"credentials": {}},
        headers=auth(tenant_admin["token"]),
    )
    assert r.status_code == 400
    assert "api_token" in r.json()["detail"]


def test_credentials_are_encrypted_and_never_returned(client, master_token, tenant_admin):
    item = _publish(client, master_token).json()
    secret = f"tok-{uuid.uuid4().hex}"
    r = client.put(
        f"/api/mcp-store/activations/{item['id']}",
        json={"credentials": {"api_token": secret}},
        headers=auth(tenant_admin["token"]),
    )
    assert r.status_code == 200, r.text
    assert secret not in r.text
    assert r.json()["configured_fields"] == ["api_token"]

    listing = client.get("/api/mcp-store/activations", headers=auth(tenant_admin["token"]))
    assert secret not in listing.text

    with psycopg.connect(settings.database_url) as conn:
        stored = conn.execute("SELECT credentials_encrypted FROM tenant_mcp_activations").fetchall()
    assert stored and all(secret not in (s[0] or "") for s in stored)


def test_activation_reaches_the_run_payload_with_credentials_resolved(
    client, master_token, tenant_admin
):
    """A credencial só é descriptografada no template_runtime, e o servidor
    entra no payload sem que a versão do template (imutável) seja reescrita."""
    from app.template_runtime import build_run_payload

    slug = f"crm_{uuid.uuid4().hex[:8]}"
    item = _publish(client, master_token, slug=slug).json()
    client.put(
        f"/api/mcp-store/activations/{item['id']}",
        json={"credentials": {"api_token": "tok-123"}},
        headers=auth(tenant_admin["token"]),
    )
    run = build_run_payload(tenant_admin["user"]["tenant_id"], None)
    server = next(s for s in run["mcp_servers"] if s["name"] == slug)
    assert server["url"] == "https://crm.example/mcp?token=tok-123"
    assert server["auth_token"] == "tok-123"


def test_deactivation_removes_the_server_from_the_payload(client, master_token, tenant_admin):
    from app.template_runtime import build_run_payload

    slug = f"crm_{uuid.uuid4().hex[:8]}"
    item = _publish(client, master_token, slug=slug).json()
    h = auth(tenant_admin["token"])
    client.put(
        f"/api/mcp-store/activations/{item['id']}",
        json={"credentials": {"api_token": "tok-123"}},
        headers=h,
    )
    client.delete(f"/api/mcp-store/activations/{item['id']}", headers=h)
    run = build_run_payload(tenant_admin["user"]["tenant_id"], None)
    assert all(s["name"] != slug for s in run["mcp_servers"])


def test_activations_are_tenant_isolated(client, master_token, tenant_admin):
    from app.template_runtime import build_run_payload

    slug = f"crm_{uuid.uuid4().hex[:8]}"
    item = _publish(client, master_token, slug=slug).json()
    other = client.post(
        "/api/tenants",
        json={"name": "Outro", "tenant_key": f"outro-{uuid.uuid4().hex[:6]}"},
        headers=auth(master_token),
    ).json()
    client.put(
        f"/api/mcp-store/activations/{item['id']}",
        json={"credentials": {"api_token": "tok-do-outro"}, "tenant_id": other["id"]},
        headers=auth(master_token),
    )

    visible = client.get("/api/mcp-store/activations", headers=auth(tenant_admin["token"])).json()
    assert all(a["tenant_id"] != other["id"] for a in visible)

    run = build_run_payload(tenant_admin["user"]["tenant_id"], None)
    assert all(s["name"] != slug for s in run["mcp_servers"])


def test_native_item_is_listed_but_not_activated_here(client, tenant_admin):
    catalog = client.get("/api/mcp-store/catalog", headers=auth(tenant_admin["token"])).json()
    native = next(i for i in catalog if i["slug"] == "pagamentos_pix")
    assert native["is_native"] is True

    r = client.put(
        f"/api/mcp-store/activations/{native['id']}",
        json={"credentials": {}},
        headers=auth(tenant_admin["token"]),
    )
    assert r.status_code == 400
