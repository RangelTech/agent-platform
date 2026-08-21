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
            "isActive": True,
            "priority": 1,
            "testStatus": "active",
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

    @app.get("/api/oauth/{provider}/authorize")
    async def autorizar(provider: str):
        return {
            "authUrl": f"https://exemplo.invalido/{provider}",
            "state": "s",
            "codeVerifier": "v",
            "redirectUri": "http://localhost:8080/callback",
        }

    @app.get("/api/oauth/{provider}/device-code")
    async def codigo_de_dispositivo(provider: str):
        return {
            "device_code": "d-123",
            "user_code": "ABCD-1234",
            "verification_uri": f"https://exemplo.invalido/{provider}/device",
            "codeVerifier": "v",
        }

    @app.get("/api/usage/stats")
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


def _registrar_litellm(client, master_token, tenant_id, *, team_id="team-x", bridge="sk-bridge", ai_assist="sk-ai-assist"):
    return client.put(
        "/api/ai-router/instancias-litellm",
        json={
            "tenant_id": tenant_id,
            "litellm_team_id": team_id,
            "bridge_key": bridge,
            "ai_assist_key": ai_assist,
        },
        headers=auth(master_token),
    )


def test_somente_o_master_registra_instancia_litellm(client, tenant_admin):
    r = _registrar_litellm(client, tenant_admin["token"], tenant_admin["user"]["tenant_id"])
    assert r.status_code == 403


def test_registra_instancia_litellm_e_status_reflete(client, master_token, tenant_admin):
    """Achado de auditoria (infra-04 Fase C, achado durante o desenho): o
    tenant LiteLLM não tem `base_url` nem senha — se `/status` ou o registro
    dependessem desses campos sem querer, um tenant só-LiteLLM apareceria
    como se nunca tivesse sido provisionado. Prova que não é o caso."""
    r = _registrar_litellm(client, master_token, tenant_admin["user"]["tenant_id"])
    assert r.status_code == 201, r.text
    assert r.json()["litellm_team_id"] == "team-x"

    status = client.get("/api/ai-router/status", headers=auth(tenant_admin["token"]))
    assert status.status_code == 200
    assert status.json()["provisionado"] is True


def test_as_2_keys_do_tenant_litellm_nunca_voltam_na_api(client, master_token, tenant_admin):
    r = _registrar_litellm(client, master_token, tenant_admin["user"]["tenant_id"], bridge="chave-bridge-secreta", ai_assist="chave-ai-assist-secreta")
    assert "chave-bridge-secreta" not in r.text
    assert "chave-ai-assist-secreta" not in r.text


def test_registro_litellm_nao_preenche_campos_do_9router(client, master_token, tenant_admin):
    """A constraint de banco (`0028_litellm_router_nullable.sql`) é a garantia
    de verdade contra as duas formas ficarem pela metade — este teste prova
    que o registro por aqui realmente deixa os campos do 9Router nulos, não
    preenche com placeholder pra "passar" na constraint."""
    from app.db import get_connection

    _registrar_litellm(client, master_token, tenant_admin["user"]["tenant_id"])
    with get_connection() as conn:
        linha = conn.execute(
            "SELECT base_url, litellm_team_id FROM tenant_routers WHERE tenant_id = %s",
            (tenant_admin["user"]["tenant_id"],),
        ).fetchone()
    assert linha["base_url"] is None
    assert linha["litellm_team_id"] == "team-x"


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
        for provedor in ("gemini", "claude"):
            client.post(
                "/api/ai-router/contas",
                json={"provider": provedor, "api_key": "k", "label": provedor},
                headers=auth(tenant_admin["token"]),
            )
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


def test_modelo_sem_conta_conectada_nao_entra_em_combo(client, master_token, tenant_admin):
    """O catálogo da instância traz 752 modelos, conectados ou não.

    Se `/v1/models` fosse a fonte de "disponível", o cliente montaria um combo
    com um modelo que nenhuma conta dele atende — aceito na criação e quebrando
    só no meio de um atendimento. Aqui a empresa tem conta Gemini e nenhuma
    Claude, então o modelo `cc/…` não pode entrar.
    """
    server, porta, _ = _fake_router()
    try:
        _registrar(client, master_token, tenant_admin["user"]["tenant_id"], porta)
        client.post(
            "/api/ai-router/contas",
            json={"provider": "gemini", "api_key": "k", "label": "Gemini"},
            headers=auth(tenant_admin["token"]),
        )
        modelos = client.get("/api/ai-router/modelos", headers=auth(tenant_admin["token"]))
        combo = client.post(
            "/api/ai-router/combos",
            json={"name": "Mistura", "models": ["cc/claude-sonnet-5"]},
            headers=auth(tenant_admin["token"]),
        )
    finally:
        server.should_exit = True

    oferecidos = {m["id"] for m in modelos.json()}
    assert oferecidos == {"gemini/gemini-3.1-flash"}
    assert combo.status_code == 400
    assert "cc/claude-sonnet-5" in combo.json()["detail"]


def test_catalogo_nao_expoe_provedor_fora_da_curadoria(client, master_token, tenant_admin):
    """A instância conhece 80 provedores; a tela oferece os que fazem sentido
    aqui. Provedor fora da lista não conecta nem por chamada direta."""
    server, porta, _ = _fake_router()
    try:
        _registrar(client, master_token, tenant_admin["user"]["tenant_id"], porta)
        catalogo = client.get("/api/ai-router/catalogo", headers=auth(tenant_admin["token"]))
        recusado = client.post(
            "/api/ai-router/contas/oauth/iniciar",
            json={"provider": "provedor-inexistente"},
            headers=auth(tenant_admin["token"]),
        )
    finally:
        server.should_exit = True

    ids = {p["id"] for p in catalogo.json()}
    assert {"claude", "codex", "gemini"} <= ids
    assert all(p["modo"] in {"apikey", "redirect", "device"} for p in catalogo.json())
    assert recusado.status_code == 400


def test_assinatura_devolve_o_que_a_tela_precisa_mostrar(client, master_token, tenant_admin):
    """Os dois fluxos de assinatura chegam à tela no mesmo formato.

    A instância responde em camelCase no fluxo de redirecionamento e em
    snake_case no de código de dispositivo, e são endpoints diferentes. Se essa
    diferença vazasse para a tela, o campo do código apareceria vazio — que foi
    exatamente o defeito encontrado antes de existir este teste.
    """
    server, porta, _ = _fake_router()
    try:
        _registrar(client, master_token, tenant_admin["user"]["tenant_id"], porta)
        redirecionado = client.post(
            "/api/ai-router/contas/oauth/iniciar",
            json={"provider": "claude"},
            headers=auth(tenant_admin["token"]),
        ).json()
        dispositivo = client.post(
            "/api/ai-router/contas/oauth/iniciar",
            json={"provider": "github"},
            headers=auth(tenant_admin["token"]),
        ).json()
    finally:
        server.should_exit = True

    assert redirecionado["modo"] == "redirect"
    assert redirecionado["auth_url"].startswith("https://")
    assert redirecionado["code_verifier"]

    assert dispositivo["modo"] == "device"
    assert dispositivo["user_code"] == "ABCD-1234"
    assert dispositivo["device_code"] == "d-123"
    assert dispositivo["auth_url"].startswith("https://")


def test_chave_de_api_nao_passa_pelo_fluxo_de_assinatura(client, master_token, tenant_admin):
    """Gemini conecta por chave; pedir autorização dele é erro de uso."""
    server, porta, _ = _fake_router()
    try:
        _registrar(client, master_token, tenant_admin["user"]["tenant_id"], porta)
        resposta = client.post(
            "/api/ai-router/contas/oauth/iniciar",
            json={"provider": "gemini"},
            headers=auth(tenant_admin["token"]),
        )
    finally:
        server.should_exit = True

    assert resposta.status_code == 400
