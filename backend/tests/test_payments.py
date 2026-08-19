"""Credenciais de pagamento e webhook do Mercado Pago."""

import hashlib
import hmac
import threading
import uuid

import psycopg
import pytest
import uvicorn
from app.config import settings
from fastapi import FastAPI
from tests.conftest import auth

pytestmark = pytest.mark.integration


def test_access_token_is_encrypted_and_never_returned(client, tenant_admin):
    token = f"APP_USR-{uuid.uuid4().hex}"
    r = client.put(
        "/api/payments/credentials",
        json={"access_token": token, "sandbox": True},
        headers=auth(tenant_admin["token"]),
    )
    assert r.status_code == 200, r.text
    assert token not in r.text
    assert r.json()["has_token"] is True

    listing = client.get("/api/payments/credentials", headers=auth(tenant_admin["token"]))
    assert token not in listing.text

    with psycopg.connect(settings.database_url) as conn:
        stored = conn.execute("SELECT access_token_encrypted FROM payment_credentials").fetchall()
    assert stored and all(token not in (s[0] or "") for s in stored)


def test_update_without_token_keeps_the_stored_one(client, tenant_admin):
    h = auth(tenant_admin["token"])
    client.put("/api/payments/credentials", json={"access_token": "APP_USR-abc"}, headers=h)
    r = client.put("/api/payments/credentials", json={"sandbox": False}, headers=h)
    assert r.status_code == 200
    assert r.json()["has_token"] is True
    assert r.json()["sandbox"] is False


def test_first_credential_requires_a_token(client, tenant_admin):
    r = client.put(
        "/api/payments/credentials",
        json={"sandbox": True},
        headers=auth(tenant_admin["token"]),
    )
    assert r.status_code == 400


def test_credentials_are_tenant_isolated(client, master_token, tenant_admin):
    other = client.post(
        "/api/tenants",
        json={"name": "Pay", "tenant_key": f"pay-{uuid.uuid4().hex[:6]}"},
        headers=auth(master_token),
    ).json()
    client.put(
        "/api/payments/credentials",
        json={"access_token": "APP_USR-do-outro", "tenant_id": other["id"]},
        headers=auth(master_token),
    )
    visible = client.get("/api/payments/credentials", headers=auth(tenant_admin["token"])).json()
    assert all(c["tenant_id"] != other["id"] for c in visible)


def test_unknown_webhook_token_is_rejected(client):
    r = client.post(
        "/api/payments/webhooks/mercado-pago/nao-existe",
        json={"data": {"id": "1"}},
    )
    assert r.status_code == 404


def _start_fake_gateway(status: str):
    fake = FastAPI()

    @fake.get("/v1/payments/{payment_id}")
    async def read(payment_id: str):
        return {"id": int(payment_id), "status": status, "transaction_amount": 48.9}

    config = uvicorn.Config(fake, host="127.0.0.1", port=0, log_level="error")
    server = uvicorn.Server(config)
    threading.Thread(target=server.run, daemon=True).start()
    while not server.started:
        pass
    return server, server.servers[0].sockets[0].getsockname()[1]


def _charge(tenant_id: str, external_id: str) -> None:
    with psycopg.connect(settings.database_url) as conn:
        conn.execute(
            """INSERT INTO payment_charges
                   (tenant_id, provider, external_id, amount, description, reference_id, status)
               VALUES (%s, 'mercado_pago', %s, 48.90, 'Pedido', '42', 'pending')""",
            (tenant_id, external_id),
        )
        conn.commit()


def test_webhook_confirms_payment_by_rereading_the_gateway(client, tenant_admin):
    """O corpo do webhook não é fonte de verdade: o status vem de uma
    releitura autenticada, então um POST forjado não marca nada como pago."""
    h = auth(tenant_admin["token"])
    credential = client.put(
        "/api/payments/credentials", json={"access_token": "APP_USR-x"}, headers=h
    ).json()
    external_id = str(uuid.uuid4().int % 10**9)
    _charge(credential["tenant_id"], external_id)

    server, port = _start_fake_gateway("approved")
    original = settings.mercado_pago_api
    settings.mercado_pago_api = f"http://127.0.0.1:{port}"
    try:
        r = client.post(credential["webhook_path"], json={"data": {"id": external_id}})
    finally:
        settings.mercado_pago_api = original
        server.should_exit = True

    assert r.status_code == 200, r.text
    assert r.json()["charge_status"] == "paid"

    charges = client.get("/api/payments/charges", headers=h).json()
    assert any(c["external_id"] == external_id and c["status"] == "paid" for c in charges)


def test_webhook_with_wrong_signature_is_rejected(client, tenant_admin):
    h = auth(tenant_admin["token"])
    credential = client.put(
        "/api/payments/credentials",
        json={"access_token": "APP_USR-x", "webhook_secret": "segredo-mp"},
        headers=h,
    ).json()

    forged = client.post(
        credential["webhook_path"],
        json={"data": {"id": "999"}},
        headers={"x-signature": "ts=1,v1=deadbeef", "x-request-id": "req-1"},
    )
    assert forged.status_code == 401

    # E a assinatura correta passa da checagem (falhando adiante, no gateway).
    manifest = "id:999;request-id:req-1;ts:1;"
    signature = hmac.new(b"segredo-mp", manifest.encode(), hashlib.sha256).hexdigest()
    signed = client.post(
        credential["webhook_path"],
        json={"data": {"id": "999"}},
        headers={"x-signature": f"ts=1,v1={signature}", "x-request-id": "req-1"},
    )
    assert signed.status_code != 401


def test_webhook_is_idempotent_on_redelivery(client, tenant_admin):
    """O Mercado Pago reentrega o mesmo webhook de verdade (retry de rede,
    timeout no ACK, etc.) — processar 2x não pode duplicar/creditar em dobro.

    A garantia aqui é estrutural: o handler faz um UPDATE idempotente (seta o
    status a partir da releitura no gateway, não incrementa nada), então
    entregar o mesmo webhook 2x deve deixar exatamente 1 linha de cobrança,
    com o mesmo status final — não duas linhas, não um status "mais pago".
    """
    h = auth(tenant_admin["token"])
    credential = client.put(
        "/api/payments/credentials", json={"access_token": "APP_USR-x"}, headers=h
    ).json()
    external_id = str(uuid.uuid4().int % 10**9)
    _charge(credential["tenant_id"], external_id)

    server, port = _start_fake_gateway("approved")
    original = settings.mercado_pago_api
    settings.mercado_pago_api = f"http://127.0.0.1:{port}"
    try:
        first = client.post(credential["webhook_path"], json={"data": {"id": external_id}})
        second = client.post(credential["webhook_path"], json={"data": {"id": external_id}})
    finally:
        settings.mercado_pago_api = original
        server.should_exit = True

    assert first.status_code == 200 and first.json()["charge_status"] == "paid"
    assert second.status_code == 200 and second.json()["charge_status"] == "paid"

    with psycopg.connect(settings.database_url) as conn:
        rows = conn.execute(
            "SELECT status FROM payment_charges WHERE external_id = %s", (external_id,)
        ).fetchall()
    assert len(rows) == 1, "webhook redelivery duplicated the charge row"
    assert rows[0][0] == "paid"


def test_run_payload_carries_the_payment_credential(client, tenant_admin):
    """A credencial é descriptografada só no template_runtime, como os secrets."""
    from app.template_runtime import build_run_payload

    h = auth(tenant_admin["token"])
    client.put("/api/payments/credentials", json={"access_token": "APP_USR-run"}, headers=h)
    run = build_run_payload(tenant_admin["user"]["tenant_id"], None)
    assert run["payment"]["access_token"] == "APP_USR-run"
    assert run["payment"]["sandbox"] is True
