"""Tenant onboarding wizard and branding self-service."""

import io
import uuid

import pytest
from tests.conftest import auth

pytestmark = pytest.mark.integration


def test_wizard_creates_tenant_and_admin_in_one_step(client, master_token):
    key = f"wiz-{uuid.uuid4().hex[:6]}"
    email = f"admin-{uuid.uuid4().hex[:6]}@wiz.com"
    r = client.post(
        "/api/tenants",
        json={
            "name": "Wizard SA",
            "tenant_key": key,
            "admin_name": "Admin Wizard",
            "admin_email": email,
            "admin_password": "senha-forte-123",
        },
        headers=auth(master_token),
    )
    assert r.status_code == 201, r.text

    # The admin can sign in immediately and already manages users.
    token = client.post(
        "/api/auth/login", json={"email": email, "password": "senha-forte-123"}
    ).json()["token"]
    me = client.get("/api/auth/me", headers=auth(token)).json()
    assert me["is_master"] is False
    assert "edit" in me["permissions"].get("users", [])
    assert me["branding"]["name"] == "Wizard SA"


def test_wizard_partial_admin_fields_rejected(client, master_token):
    r = client.post(
        "/api/tenants",
        json={
            "name": "Meio",
            "tenant_key": f"meio-{uuid.uuid4().hex[:6]}",
            "admin_email": "so-email@x.com",
        },
        headers=auth(master_token),
    )
    assert r.status_code == 400


def test_admin_customizes_branding_and_users_see_it(client, tenant_admin):
    h = auth(tenant_admin["token"])
    r = client.put(
        "/api/tenants/branding",
        json={"brand_name": "ACME Corp", "brand_color": "#ff6600", "brand_theme": "light"},
        headers=h,
    )
    assert r.status_code == 200, r.text

    me = client.get("/api/auth/me", headers=h).json()
    assert me["branding"] == {
        "name": "ACME Corp",
        "tenant_key": me["branding"]["tenant_key"],
        "has_logo": False,
        "color": "#ff6600",
        "theme": "light",
    }


def test_branding_is_forbidden_for_master_and_plain_users(
    client, master_token, tenant_admin
):
    assert (
        client.put(
            "/api/tenants/branding",
            json={"brand_name": "X"},
            headers=auth(master_token),
        ).status_code
        == 403
    )
    # A user without users.edit cannot brand the company.
    email = f"px-{uuid.uuid4().hex[:6]}@acme.com"
    client.post(
        "/api/users",
        json={"email": email, "name": "P", "password": "senha-forte-123"},
        headers=auth(tenant_admin["token"]),
    )
    token = client.post(
        "/api/auth/login", json={"email": email, "password": "senha-forte-123"}
    ).json()["token"]
    assert (
        client.put(
            "/api/tenants/branding", json={"brand_name": "Y"}, headers=auth(token)
        ).status_code
        == 403
    )


def test_logo_upload_and_public_fetch(client, tenant_admin):
    h = auth(tenant_admin["token"])
    png = bytes.fromhex(
        "89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c489"
        "0000000d49444154789c626001000000ffff03000006000557bfabd40000000049454e44ae426082"
    )
    r = client.post(
        "/api/tenants/branding/logo",
        files={"file": ("logo.png", io.BytesIO(png), "image/png")},
        headers=h,
    )
    assert r.status_code == 200, r.text

    me = client.get("/api/auth/me", headers=h).json()
    assert me["branding"]["has_logo"] is True

    # Public fetch by tenant_key (no auth), bytes intact.
    logo = client.get(f"/api/tenants/branding/logo/{me['branding']['tenant_key']}")
    assert logo.status_code == 200
    assert logo.content == png
    assert logo.headers["content-type"].startswith("image/png")


def test_invalid_color_rejected(client, tenant_admin):
    r = client.put(
        "/api/tenants/branding",
        json={"brand_color": "laranja"},
        headers=auth(tenant_admin["token"]),
    )
    assert r.status_code == 422
