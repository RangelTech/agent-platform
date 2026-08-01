"""Contas de IA e combos: isolamento e publicação como serviço de IA.

O 9Router é simulado por um servidor real (mesma filosofia dos outros testes):
o que precisa ser provado é o comportamento da nossa camada — que a instância
é sempre resolvida pelo tenant da sessão e que um combo só usa modelo das
contas da própria empresa.
"""

import threading
import uuid

import psycopg
import pytest
import uvicorn
from app.config import settings
from fastapi import FastAPI, Request, Response
from tests.conftest import auth

pytestmark = pytest.mark.integration


def _fake_router():
    """Instância de 9Router de mentira, com o contrato real observado."""
    app = FastAPI()
    estado: dict = {"conexoes": [], "combos": []}

    @app.post("/api/auth/login")
    async def login(request: Request):
        corpo = await request.json()
        if not corpo.get("password"):
            return Response(status_code=401)
        resposta = Response(content='{"ok":true}', media_type="application/json")
        resposta.set_cookie("auth_token", "token-de-teste")
        return resposta

    @app.get("/api/health")
    async def health():
        return {"status": "ok"}

    @app.get("/api/providers")
    async def listar():
        return {"connections": estado["conexoes"]}

    @app.post("/api/providers")
    async def criar(request: Request):
        corpo = await request.json()
        conexao = {
            "id": str(uuid.uuid4()),
            "provider": corpo["provider"],
            "authType": "apikey",
            "name": corpo.get("name"),
        }
        estado["conexoes"].append(conexao)
        return {"connection": conexao}

    @app.get("/v1/models")
    async def modelos():
        return {
            "data": [
                {"id": "gemini/gemini-3.1-flash", "owned_by": "gemini"},
                {"id": "cc/claude-sonnet-5", "owned_by": "claude"},
                {"id": "combo-existente", "owned_by": "combo"},
            ]
        }

    @app.get("/api/combos")
    async def listar_combos():
        return {"combos": estado["combos"]}

    @app.post("/api/combos")
    async def criar_combo(request: Request):
        corpo = await request.json()
        combo = {"id": str(uuid.uuid4()), "name": corpo["name"], "models": corpo["models"]}
        estado["combos"].append(combo)
        return combo

    @app.get("/api/usage")
    async def uso():
        return {"total": 42}

    config = uvicorn.Config(app, host="127.0.0.1", port=0, log_level="error")
    server = uvicorn.Server(config)
    threading.Thread(target=server.run, daemon=True).start()
    while not server.started:
        pass
    return server, server.servers[0].sockets[0].getsockname()[1], estado


def _registrar(client, master_token, tenant_id, porta):
    return client.put(
        "/api/ai-router/instancias",
        json={
            "tenant_id": tenant_id,
            "base_url": f"http://127.0.0.1:{porta}",
            "admin_password": "senha-da-instancia",
            "api_key": "sk-da-instancia",
        },
        headers=auth(master_token),
    )


def test_sem_instancia_a_empresa_sabe_que_falta_provisionar(client, tenant_admin):
    r = client.get("/api/ai-router/status", headers=auth(tenant_admin["token"]))
    assert r.status_code == 200
    assert r.json()["provisionado"] is False

    contas = client.get("/api/ai-router/contas", headers=auth(tenant_admin["token"]))
    assert contas.status_code == 409


def test_somente_o_master_registra_instancia(client, tenant_admin):
    r = _registrar(client, tenant_admin["token"], tenant_admin["user"]["tenant_id"], 1)
    assert r.status_code == 403


def test_conta_conectada_aparece_e_a_chave_nao_volta(client, master_token, tenant_admin):
    server, porta, estado = _fake_router()
    try:
        _registrar(client, master_token, tenant_admin["user"]["tenant_id"], porta)
        segredo = f"chave-{uuid.uuid4().hex}"
        criada = client.post(
            "/api/ai-router/contas",
            json={"provider": "gemini", "api_key": segredo, "label": "Gemini do cliente"},
            headers=auth(tenant_admin["token"]),
        )
        listagem = client.get("/api/ai-router/contas", headers=auth(tenant_admin["token"]))
    finally:
        server.should_exit = True

    assert criada.status_code == 201, criada.text
    assert segredo not in criada.text
    assert segredo not in listagem.text
    assert listagem.json()[0]["conectada"] is True
    # A chave chegou na instância, não ficou no nosso banco.
    assert estado["conexoes"][0]["provider"] == "gemini"


def test_combo_vira_servico_de_ia_do_tenant(client, master_token, tenant_admin):
    """É isso que faz o combo aparecer no template sem mexer no editor."""
    server, porta, _ = _fake_router()
    try:
        _registrar(client, master_token, tenant_admin["user"]["tenant_id"], porta)
        combo = client.post(
            "/api/ai-router/combos",
            json={"name": "Principal", "models": ["gemini/gemini-3.1-flash", "cc/claude-sonnet-5"]},
            headers=auth(tenant_admin["token"]),
        )
        servicos = client.get("/api/ai-services", headers=auth(tenant_admin["token"]))
    finally:
        server.should_exit = True

    assert combo.status_code == 201, combo.text
    servico_id = combo.json()["ai_service_id"]
    publicado = next(s for s in servicos.json() if s["id"] == servico_id)
    assert publicado["provider"] == "openai-compatible"
    assert publicado["model"].startswith("t_")
    assert publicado["is_active"] is True


def test_combo_nao_aceita_modelo_fora_das_contas_da_empresa(client, master_token, tenant_admin):
    server, porta, _ = _fake_router()
    try:
        _registrar(client, master_token, tenant_admin["user"]["tenant_id"], porta)
        r = client.post(
            "/api/ai-router/combos",
            json={"name": "Inventado", "models": ["provider-de-outro/modelo-x"]},
            headers=auth(tenant_admin["token"]),
        )
    finally:
        server.should_exit = True
    assert r.status_code == 400
    assert "não disponíveis" in r.json()["detail"]


def test_uma_empresa_nao_alcanca_a_instancia_da_outra(client, master_token, tenant_admin):
    """O ponto que decidiu a arquitetura: a instância vem do tenant da sessão."""
    server, porta, _ = _fake_router()
    try:
        outra = client.post(
            "/api/tenants",
            json={"name": "Outra IA", "tenant_key": f"outra-ia-{uuid.uuid4().hex[:6]}"},
            headers=auth(master_token),
        ).json()
        _registrar(client, master_token, outra["id"], porta)

        # O tenant_admin é de outra empresa e não tem instância própria.
        contas = client.get("/api/ai-router/contas", headers=auth(tenant_admin["token"]))
        combos = client.post(
            "/api/ai-router/combos",
            json={"name": "X", "models": ["gemini/gemini-3.1-flash"]},
            headers=auth(tenant_admin["token"]),
        )
    finally:
        server.should_exit = True

    assert contas.status_code == 409
    assert combos.status_code == 409


def test_credenciais_da_instancia_ficam_cifradas(client, master_token, tenant_admin):
    server, porta, _ = _fake_router()
    try:
        _registrar(client, master_token, tenant_admin["user"]["tenant_id"], porta)
    finally:
        server.should_exit = True

    with psycopg.connect(settings.database_url) as conn:
        linhas = conn.execute(
            "SELECT admin_password_encrypted, api_key_encrypted FROM tenant_routers"
        ).fetchall()
    assert linhas
    for senha, chave in linhas:
        assert "senha-da-instancia" not in (senha or "")
        assert "sk-da-instancia" not in (chave or "")
