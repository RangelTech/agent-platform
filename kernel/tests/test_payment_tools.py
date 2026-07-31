"""generate_pix_charge / check_payment_status, exercitados pelo contrato MCP
contra um Mercado Pago falso."""

import json
import threading

import pytest
import uvicorn
from app.config import settings
from app.tools import open_catalog_session, set_current_agent, set_run_context

pytestmark = [pytest.mark.integration, pytest.mark.anyio]


def _tool_text(result) -> str:
    return "\n".join(c.text for c in result.content if getattr(c, "text", None))


def _start_fake_gateway():
    from fastapi import FastAPI, Request

    fake = FastAPI()
    seen: dict = {}

    @fake.post("/v1/payments")
    async def create(request: Request):
        seen["body"] = await request.json()
        seen["auth"] = request.headers.get("authorization")
        return {
            "id": 1234567890,
            "status": "pending",
            "transaction_amount": seen["body"]["transaction_amount"],
            "point_of_interaction": {
                "transaction_data": {
                    "qr_code": "00020126PIX-COPIA-E-COLA",
                    "qr_code_base64": "aGVsbG8=",
                    "ticket_url": "https://mp.example/ticket/1234567890",
                }
            },
        }

    @fake.get("/v1/payments/{payment_id}")
    async def read(payment_id: str):
        return {"id": int(payment_id), "status": "approved", "transaction_amount": 48.9}

    config = uvicorn.Config(fake, host="127.0.0.1", port=0, log_level="error")
    server = uvicorn.Server(config)
    threading.Thread(target=server.run, daemon=True).start()
    while not server.started:
        pass
    port = server.servers[0].sockets[0].getsockname()[1]
    return server, port, seen


def _context(payment: dict) -> None:
    set_run_context(
        secrets={},
        datasources=[],
        tenant_id=None,
        chat_id=None,
        payment=payment,
    )
    set_current_agent("caixa")


async def test_charge_uses_the_requested_amount_not_a_test_value():
    """O valor cobrado é o valor pedido — sandbox não pode virar R$ 0,01."""
    server, port, seen = _start_fake_gateway()
    original = settings.mercado_pago_api
    settings.mercado_pago_api = f"http://127.0.0.1:{port}"
    try:
        _context({"provider": "mercado_pago", "access_token": "TEST-token", "sandbox": True})
        async with open_catalog_session() as session:
            result = await session.call_tool(
                "generate_pix_charge",
                {"amount": "48,90", "description": "Pedido 42", "reference_id": "42"},
            )
        payload = json.loads(_tool_text(result))
    finally:
        settings.mercado_pago_api = original
        server.should_exit = True

    assert seen["body"]["transaction_amount"] == 48.90
    assert seen["body"]["payment_method_id"] == "pix"
    assert seen["auth"] == "Bearer TEST-token"
    assert payload["status"] == "ok"
    assert payload["amount"] == "48.90"
    assert payload["pix_copia_e_cola"] == "00020126PIX-COPIA-E-COLA"
    assert payload["payment_id"] == "1234567890"


async def test_charge_without_credential_explains_instead_of_crashing():
    _context({})
    async with open_catalog_session() as session:
        result = await session.call_tool("generate_pix_charge", {"amount": "10.00"})
    assert "credencial de pagamento" in _tool_text(result)


async def test_charge_rejects_non_positive_amount():
    _context({"provider": "mercado_pago", "access_token": "TEST-token", "sandbox": True})
    async with open_catalog_session() as session:
        result = await session.call_tool("generate_pix_charge", {"amount": "0"})
    assert "maior que zero" in _tool_text(result)


async def test_check_status_reports_paid():
    server, port, _ = _start_fake_gateway()
    original = settings.mercado_pago_api
    settings.mercado_pago_api = f"http://127.0.0.1:{port}"
    try:
        _context({"provider": "mercado_pago", "access_token": "TEST-token", "sandbox": True})
        async with open_catalog_session() as session:
            result = await session.call_tool("check_payment_status", {"payment_id": "1234567890"})
        payload = json.loads(_tool_text(result))
    finally:
        settings.mercado_pago_api = original
        server.should_exit = True

    assert payload["paid"] is True
    assert payload["status"] == "paid"


async def test_check_status_requires_an_identifier():
    _context({"provider": "mercado_pago", "access_token": "TEST-token", "sandbox": True})
    async with open_catalog_session() as session:
        result = await session.call_tool("check_payment_status", {})
    assert "informe payment_id ou reference_id" in _tool_text(result).lower()
