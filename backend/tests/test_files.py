import io
import uuid

import pytest
from tests.conftest import auth

pytestmark = pytest.mark.integration


def _upload(client, token, name="doc.txt", content=b"Politica de teste da ACME."):
    return client.post(
        "/api/files",
        files={"file": (name, io.BytesIO(content), "text/plain")},
        headers=auth(token),
    )


def test_upload_creates_pending_file(client, tenant_admin):
    r = _upload(client, tenant_admin["token"])
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["status"] == "pending"
    assert body["size_bytes"] > 0

    listing = client.get("/api/files", headers=auth(tenant_admin["token"])).json()
    assert any(f["id"] == body["id"] for f in listing)


def test_unsupported_extension_rejected(client, tenant_admin):
    r = _upload(client, tenant_admin["token"], name="virus.exe")
    assert r.status_code == 400


def test_empty_file_rejected(client, tenant_admin):
    r = _upload(client, tenant_admin["token"], content=b"")
    assert r.status_code == 400


def test_files_are_tenant_isolated(client, master_token, tenant_admin):
    other = client.post(
        "/api/tenants",
        json={"name": "F", "tenant_key": f"f-{uuid.uuid4().hex[:6]}"},
        headers=auth(master_token),
    ).json()
    client.post(
        "/api/files",
        files={"file": ("alheio.txt", io.BytesIO(b"conteudo"), "text/plain")},
        data={"tenant_id": other["id"]},
        headers=auth(master_token),
    )
    visible = client.get("/api/files", headers=auth(tenant_admin["token"])).json()
    assert all(f["name"] != "alheio.txt" for f in visible)


def test_version_rejects_foreign_file(client, master_token, tenant_admin):
    other = client.post(
        "/api/tenants",
        json={"name": "FF", "tenant_key": f"ff-{uuid.uuid4().hex[:6]}"},
        headers=auth(master_token),
    ).json()
    foreign = client.post(
        "/api/files",
        files={"file": ("deles.txt", io.BytesIO(b"x"), "text/plain")},
        data={"tenant_id": other["id"]},
        headers=auth(master_token),
    ).json()

    tpl = client.post(
        "/api/templates",
        json={"name": f"t-{uuid.uuid4().hex[:6]}", "description": "d"},
        headers=auth(tenant_admin["token"]),
    ).json()
    r = client.post(
        f"/api/templates/{tpl['id']}/versions",
        json={
            "supervisor_prompt": "x",
            "agents": [
                {
                    "name": "rag_agent",
                    "description": "docs",
                    "prompt": "p",
                    "file_ids": [foreign["id"]],
                }
            ],
        },
        headers=auth(tenant_admin["token"]),
    )
    assert r.status_code == 400
