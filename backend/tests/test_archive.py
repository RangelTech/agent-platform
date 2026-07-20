"""Soft-delete semantics: archived resources vanish from listings but stay
valid inside immutable template versions; cross-tenant archiving is a 404."""

import io
import uuid

import pytest
from tests.conftest import auth

pytestmark = pytest.mark.integration


def test_archive_hides_from_listings(client, tenant_admin):
    h = auth(tenant_admin["token"])
    svc = client.post(
        "/api/ai-services",
        json={
            "name": f"arq-{uuid.uuid4().hex[:6]}",
            "provider": "gemini",
            "model": "gemini-flash-latest",
            "api_key": "k",
        },
        headers=h,
    ).json()
    assert client.delete(f"/api/ai-services/{svc['id']}", headers=h).status_code == 200
    listing = client.get("/api/ai-services", headers=h).json()
    assert all(s["id"] != svc["id"] for s in listing)

    ds = client.post(
        "/api/datasources",
        json={"name": f"d-{uuid.uuid4().hex[:6]}", "kind": "sqlite", "config": {"path": "x.db"}},
        headers=h,
    ).json()
    assert client.delete(f"/api/datasources/{ds['id']}", headers=h).status_code == 200
    assert all(
        d["id"] != ds["id"] for d in client.get("/api/datasources", headers=h).json()
    )

    secret = client.post(
        "/api/secrets", json={"name": f"S{uuid.uuid4().hex[:6]}", "value": "v"}, headers=h
    ).json()
    assert client.delete(f"/api/secrets/{secret['id']}", headers=h).status_code == 200
    assert all(
        s["id"] != secret["id"] for s in client.get("/api/secrets", headers=h).json()
    )

    file = client.post(
        "/api/files",
        files={"file": ("a.txt", io.BytesIO(b"conteudo"), "text/plain")},
        headers=h,
    ).json()
    assert client.delete(f"/api/files/{file['id']}", headers=h).status_code == 200
    assert all(f["id"] != file["id"] for f in client.get("/api/files", headers=h).json())


def test_archived_template_leaves_deployed_versions_alone(client, tenant_admin, fake_kernel):
    h = auth(tenant_admin["token"])
    tpl = client.post(
        "/api/templates",
        json={"name": f"t-{uuid.uuid4().hex[:6]}", "description": "d"},
        headers=h,
    ).json()
    v = client.post(
        f"/api/templates/{tpl['id']}/versions",
        json={"supervisor_prompt": "x", "agents": []},
        headers=h,
    ).json()
    client.post(f"/api/templates/{tpl['id']}/deploy", json={"version_id": v["id"]}, headers=h)

    assert client.delete(f"/api/templates/{tpl['id']}", headers=h).status_code == 200
    listing = client.get("/api/templates", headers=h).json()
    assert all(t["id"] != tpl["id"] for t in listing)

    # A conversation pinned to the archived template degrades to the default
    # instead of erroring.
    from app.template_runtime import build_run_payload

    payload = build_run_payload(tenant_admin["user"]["tenant_id"], tpl["id"])
    assert payload["supervisor"]["model"]["provider"]  # resolved something


def test_cross_tenant_archive_is_404(client, master_token, tenant_admin):
    other = client.post(
        "/api/tenants",
        json={"name": "AR", "tenant_key": f"ar-{uuid.uuid4().hex[:6]}"},
        headers=auth(master_token),
    ).json()
    foreign = client.post(
        "/api/ai-services",
        json={
            "name": "deles",
            "provider": "gemini",
            "model": "m",
            "api_key": "k",
            "tenant_id": other["id"],
        },
        headers=auth(master_token),
    ).json()
    r = client.delete(f"/api/ai-services/{foreign['id']}", headers=auth(tenant_admin["token"]))
    assert r.status_code == 404
