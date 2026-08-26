import pytest
from app.db import get_connection


def auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def tool_payload(**overrides) -> dict:
    payload = {
        "name": "consultar_estoque",
        "description": "Consulta estoque do ERP.",
        "input_schema": {"type": "object", "properties": {"sku": {"type": "string"}}},
        "python_code": "def main(inputs, context):\n    return {'sku': inputs.get('sku')}",
        "timeout_seconds": 60,
        "enabled": True,
    }
    payload.update(overrides)
    return payload


@pytest.mark.integration
def test_custom_tool_secrets_are_write_only_and_can_be_cleared(client, tenant_admin):
    created = client.post(
        "/api/custom-tools",
        headers=auth(tenant_admin["token"]),
        json=tool_payload(secrets={"ERP_TOKEN": "super-secret"}),
    )
    assert created.status_code == 201, created.text
    tool = created.json()
    assert "secrets" not in tool

    # Omitted secrets retain the encrypted map during ordinary edits.
    retained = client.put(
        f"/api/custom-tools/{tool['id']}",
        headers=auth(tenant_admin["token"]),
        json=tool_payload(description="Consulta estoque atualizado."),
    )
    assert retained.status_code == 200, retained.text
    with get_connection() as conn:
        encrypted = conn.execute(
            "SELECT secrets_encrypted FROM custom_tools WHERE id=%s", (tool["id"],)
        ).fetchone()["secrets_encrypted"]
    assert encrypted

    # An explicit empty map is the deliberate credential-revocation path.
    cleared = client.put(
        f"/api/custom-tools/{tool['id']}",
        headers=auth(tenant_admin["token"]),
        json=tool_payload(secrets={}),
    )
    assert cleared.status_code == 200, cleared.text
    with get_connection() as conn:
        encrypted = conn.execute(
            "SELECT secrets_encrypted FROM custom_tools WHERE id=%s", (tool["id"],)
        ).fetchone()["secrets_encrypted"]
    assert encrypted is None


@pytest.mark.integration
def test_custom_tools_cannot_cross_tenant_scope(client, tenant_admin, master_token):
    created = client.post(
        "/api/custom-tools", headers=auth(tenant_admin["token"]), json=tool_payload()
    )
    assert created.status_code == 201, created.text
    other = client.post(
        "/api/tenants",
        headers=auth(master_token),
        json={"name": "Outra", "tenant_key": "outra-custom-tools"},
    ).json()
    # Master cannot use the endpoint as an implicit cross-tenant proxy either.
    response = client.get("/api/custom-tools", headers=auth(master_token))
    assert response.status_code == 400
    assert other["id"] != tenant_admin["user"]["tenant_id"]
