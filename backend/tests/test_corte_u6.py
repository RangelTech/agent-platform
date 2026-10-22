"""U-6: o motor nao tem UI nem login. Contratos M2M e internos continuam."""

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

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


BACKEND = Path(__file__).resolve().parent.parent

_SONDA = """
import json, sys
from fastapi.testclient import TestClient
from app.main import app
alvos = json.loads(sys.argv[1])
c = TestClient(app, raise_server_exceptions=False)
print(json.dumps({
    "paths": sorted(app.openapi()["paths"]),
    "status": {f"{m} {p}": c.request(m, p).status_code for m, p in alvos},
}))
"""


@pytest.fixture(scope="module")
def sonda():
    """Interpretador limpo: o app de producao puro, sem a semente dos testes."""
    alvos = [list(a) for a in REMOVIDAS] + [["POST", "/v1/messages"]]
    out = subprocess.run(
        [sys.executable, "-c", _SONDA, json.dumps(alvos)],
        cwd=BACKEND,
        env={**os.environ, "PYTHONPATH": str(BACKEND)},
        capture_output=True,
        text=True,
        timeout=120,
        check=True,
    )
    return json.loads(out.stdout.strip().splitlines()[-1])


@pytest.mark.parametrize(("metodo", "caminho"), REMOVIDAS)
def test_rota_removida_devolve_404(sonda, metodo, caminho):
    assert sonda["status"][f"{metodo} {caminho}"] == 404, (metodo, caminho)


def test_contratos_m2m_e_internos_continuam(sonda):
    caminhos = set(sonda["paths"])
    for esperado in (
        "/v1/messages",
        "/health",
        "/internal/runs/{run_id}/messages",
        "/internal/runs/{run_id}/usage",
        "/internal/ui/me",
        "/api/omnichannel/inboxes/{inbox_id}/ia",
        "/internal/templates/{template_id}/status",
    ):
        assert esperado in caminhos, (esperado, len(caminhos), sorted(caminhos)[:40])


def test_m2m_sem_chave_continua_exigindo_autenticacao(sonda):
    assert sonda["status"]["POST /v1/messages"] in (401, 403, 422)
