"""Contrato F-4: Luar só descobre o estado do agente da própria empresa."""

import time
import uuid

from app.ui_context import sign_context
from tests.conftest import auth


def _routing_context(tenant_id: str) -> dict[str, str]:
    now = int(time.time())
    token = sign_context(
        {
            "aud": "install",
            "scopes": ["routing"],
            "engine_tenant_id": tenant_id,
            "nonce": uuid.uuid4().hex,
            "iat": now,
            "exp": now + 30,
        }
    )
    return {"X-UI-Context": token}


def _new_template(client, tenant_admin: dict) -> dict:
    headers = auth(tenant_admin["token"])
    created = client.post(
        "/api/templates", json={"name": f"fila-{uuid.uuid4().hex[:8]}"}, headers=headers
    )
    assert created.status_code == 201, created.text
    template = created.json()
    version = client.post(
        f"/api/templates/{template['id']}/versions",
        json={
            "supervisor_prompt": "Atenda o cliente.",
            "agents": [{"name": "atendente", "description": "Atende", "prompt": "Atenda."}],
        },
        headers=headers,
    )
    assert version.status_code == 201, version.text
    return template, version.json(), headers


def test_routing_template_status_requires_same_tenant_and_active_version(
    client, tenant, tenant_admin, other_tenant_admin
):
    template, version, headers = _new_template(client, tenant_admin)
    url = f"/api/internal/templates/{template['id']}/status"

    draft = client.get(url, headers=_routing_context(tenant["id"]))
    assert draft.status_code == 200
    assert draft.json()["published"] is False

    deployed = client.post(
        f"/api/templates/{template['id']}/deploy",
        json={"version_id": version["id"]},
        headers=headers,
    )
    assert deployed.status_code == 200, deployed.text
    published = client.get(url, headers=_routing_context(tenant["id"]))
    assert published.status_code == 200
    assert published.json() == {
        "tenant_id": tenant["id"],
        "template_id": template["id"],
        "published": True,
    }

    foreign = client.get(url, headers=_routing_context(other_tenant_admin["tenant"]["id"]))
    assert foreign.status_code == 404


def test_routing_template_status_requires_routing_scope(client, tenant, tenant_admin):
    template, _, _ = _new_template(client, tenant_admin)
    now = int(time.time())
    token = sign_context(
        {
            "aud": "install",
            "scopes": ["lifecycle"],
            "engine_tenant_id": tenant["id"],
            "nonce": uuid.uuid4().hex,
            "iat": now,
            "exp": now + 30,
        }
    )
    response = client.get(
        f"/api/internal/templates/{template['id']}/status", headers={"X-UI-Context": token}
    )
    assert response.status_code == 403
