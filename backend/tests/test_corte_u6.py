"""U-6: o motor nao tem UI nem login. Contratos M2M e internos continuam."""

import pytest
from conftest import rotas_de_producao
from fastapi import FastAPI
from fastapi.testclient import TestClient

REMOVIDAS = [
    ("POST", "/api/auth/login"),
    ("GET", "/api/auth/me"),
    ("POST", "/api/auth/logout"),
    ("POST", "/api/auth/heartbeat"),
    ("GET", "/api/users"),
    ("POST", "/api/users"),
    ("PUT", "/api/users/x"),
    ("GET", "/api/user-profiles"),
    ("POST", "/api/user-profiles"),
    ("PUT", "/api/user-profiles/x"),
    ("GET", "/api/tenants"),
    ("POST", "/api/tenants"),
    ("PUT", "/api/tenants/x"),
    ("PUT", "/api/tenants/branding"),
    ("POST", "/api/tenants/branding/reconcile"),
    ("POST", "/api/tenants/branding/logo"),
    ("GET", "/api/tenants/branding/logo/x"),
    ("POST", "/api/omnichannel/provision"),
    ("GET", "/api/omnichannel/sso"),
    ("GET", "/api/mcp-store/catalog"),
    ("GET", "/"),
    ("GET", "/index.html"),
    ("GET", "/assets/app.js"),
]


@pytest.fixture
def app_producao():
    app = FastAPI()
    app.router.routes = rotas_de_producao()
    return TestClient(app)


@pytest.mark.parametrize(("metodo", "caminho"), REMOVIDAS)
def test_rota_removida_devolve_404(app_producao, metodo, caminho):
    r = app_producao.request(metodo, caminho)
    assert r.status_code in (404, 405), (metodo, caminho, r.status_code)
    if r.status_code == 405:
        # existe o caminho para outro metodo: so e aceitavel se nao for do legado
        pytest.fail(f"{metodo} {caminho} ainda registrado (405)")


def test_contratos_m2m_e_internos_continuam():
    caminhos = {getattr(r, "path", "") for r in rotas_de_producao()}
    for esperado in (
        "/v1/messages",
        "/health",
        "/internal/runs/{run_id}/messages",
        "/internal/runs/{run_id}/usage",
        "/internal/ui/me",
        "/api/omnichannel/inboxes/{inbox_id}/ia",
        "/api/internal/templates/{template_id}/status",
    ):
        assert esperado in caminhos, esperado


def test_m2m_sem_chave_continua_exigindo_autenticacao(app_producao):
    r = app_producao.post("/v1/messages", json={})
    assert r.status_code in (401, 403, 422)
