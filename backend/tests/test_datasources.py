import uuid

import psycopg
import pytest
from app.config import settings
from tests.conftest import auth

pytestmark = pytest.mark.integration


def _create(client, token, **overrides):
    body = {
        "name": f"erp-{uuid.uuid4().hex[:6]}",
        "kind": "postgresql",
        "config": {"host": "db.acme.com", "port": 5432, "database": "erp", "user": "reader"},
        "secret": "senha-do-banco",
    }
    body.update(overrides)
    return client.post("/api/datasources", json=body, headers=auth(token))


def test_create_encrypts_secret_and_never_returns_it(client, tenant_admin):
    r = _create(client, tenant_admin["token"])
    assert r.status_code == 201, r.text
    assert "senha-do-banco" not in r.text
    assert r.json()["has_secret"] is True

    with psycopg.connect(settings.database_url) as conn:
        stored = conn.execute("SELECT secret_encrypted FROM datasources").fetchall()
    assert all("senha-do-banco" not in (s[0] or "") for s in stored)


def test_invalid_kind_rejected(client, tenant_admin):
    assert _create(client, tenant_admin["token"], kind="mongodb").status_code == 400


def test_datasources_are_tenant_isolated(client, master_token, tenant_admin):
    other = client.post(
        "/api/tenants",
        json={"name": "DS", "tenant_key": f"ds-{uuid.uuid4().hex[:6]}"},
        headers=auth(master_token),
    ).json()
    _create(client, master_token, tenant_id=other["id"], name="alheia")
    visible = client.get("/api/datasources", headers=auth(tenant_admin["token"])).json()
    assert all(d["name"] != "alheia" for d in visible)


def test_version_links_datasources_and_run_payload_decrypts_them(client, tenant_admin):
    h = auth(tenant_admin["token"])
    ds = _create(client, tenant_admin["token"], name="faturamento").json()

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
                    "name": "dados_agent",
                    "description": "dados",
                    "prompt": "Consulta dados.",
                    "tools": ["run_sql_query"],
                }
            ],
            "datasource_ids": [ds["id"]],
        },
        headers=h,
    ).json()
    client.post(f"/api/templates/{tpl['id']}/deploy", json={"version_id": v["id"]}, headers=h)

    detail = client.get(f"/api/templates/{tpl['id']}/versions/{v['id']}", headers=h).json()
    assert detail["datasource_ids"] == [ds["id"]]

    from app.template_runtime import build_run_payload

    payload = build_run_payload(tenant_admin["user"]["tenant_id"], tpl["id"])
    assert payload["datasources"] == [
        {
            "name": "faturamento",
            "kind": "postgresql",
            "config": {"host": "db.acme.com", "port": 5432, "database": "erp", "user": "reader"},
            "secret": "senha-do-banco",
        }
    ]


def test_version_rejects_foreign_datasource(client, master_token, tenant_admin):
    other = client.post(
        "/api/tenants",
        json={"name": "FD", "tenant_key": f"fd-{uuid.uuid4().hex[:6]}"},
        headers=auth(master_token),
    ).json()
    foreign = _create(client, master_token, tenant_id=other["id"], name="deles").json()

    tpl = client.post(
        "/api/templates",
        json={"name": f"t-{uuid.uuid4().hex[:6]}", "description": "d"},
        headers=auth(tenant_admin["token"]),
    ).json()
    r = client.post(
        f"/api/templates/{tpl['id']}/versions",
        json={"supervisor_prompt": "x", "agents": [], "datasource_ids": [foreign["id"]]},
        headers=auth(tenant_admin["token"]),
    )
    assert r.status_code == 400
