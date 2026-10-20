"""AMB-2/AMB-4/AMB-5: serviços em execução no ambiente — tenant da sessão, permissões, auditoria,
ambiente parado sem acordar, redação e isolamento entre empresas."""

import json

import psycopg
import pytest
from app.config import settings
from app.runtime_client import rm_tenant
from tests.conftest import auth
from tests.test_skills_agents import FORBIDDEN_WORDS, _enable, _new_user

pytestmark = pytest.mark.integration

PAYLOAD = {
    "ambiente": "ativo",
    "servicos": [
        {
            "nome": "web",
            "estado": "rodando",
            "pid": 40,
            "uptime_s": 90,
            "cpu_pct": 1.0,
            "mem_mb": 30.0,
            "portas": [8000],
            "url_publica": None,
            "origem": "supervisionado",
        }
    ],
    "soltos": [
        {
            "pid": 9,
            "comando": "python3",
            "argumentos": "python3 x.py token=abc123 --flag",
            "uptime_s": 3,
            "cpu_pct": 0.0,
            "mem_mb": 5.0,
            "portas": [],
            "origem": "solto",
        }
    ],
    "conteineres": [],
    "docker": False,
    "avisos": [],
}


class Resp:
    def __init__(self, status, body):
        self.status_code = status
        self._b = body

    def json(self):
        return self._b


@pytest.fixture
def rm(monkeypatch):
    calls = []
    state = {"payload": PAYLOAD, "logs": "linha 1\nDB_PASSWORD=supersecreta\n"}

    def fake_request(method, url, **kw):
        path = url.split("rm.test", 1)[1]
        calls.append((method, path, kw["headers"]["X-RM-Scope"]))
        if method == "GET" and path.endswith("/services"):
            return Resp(200, state["payload"])
        if method == "GET" and "/logs" in path:
            return Resp(200, {"service": "web", "lines": 200, "log": state["logs"]})
        if method == "POST" and "/services/" in path:
            return Resp(200, {"ok": True})
        return Resp(404, {"error": "not_found"})

    monkeypatch.setattr(settings, "runtime_manager_url", "http://rm.test")
    monkeypatch.setattr(settings, "runtime_manager_secret", "segredo-de-teste-123456")
    monkeypatch.setattr("app.runtime_client.httpx.request", fake_request)
    return type("RM", (), {"calls": calls, "state": state})


@pytest.fixture
def enabled(client, master_token, tenant):
    _enable(client, master_token, tenant)
    return tenant


def _events(tenant_id):
    with psycopg.connect(settings.database_url, autocommit=True) as c:
        return c.execute(
            "SELECT kind, detail FROM tenant_environment_event "
            "WHERE tenant_id = %s ORDER BY created_at",
            (tenant_id,),
        ).fetchall()


def test_list_uses_session_tenant_and_scrubs(client, enabled, tenant_admin, rm):
    h = auth(tenant_admin["token"])
    r = client.get("/api/environment/services?tenant_id=outra", headers=h)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["servicos"][0]["nome"] == "web"
    assert "abc123" not in json.dumps(body)
    assert rm.calls == [
        ("GET", f"/v1/tenants/{rm_tenant(enabled['id'])}/services", rm_tenant(enabled["id"]))
    ]


def test_two_tenants_never_see_each_other(
    client, master_token, enabled, tenant_admin, other_tenant_admin, rm
):
    _enable(client, master_token, other_tenant_admin["tenant"])
    a = client.get("/api/environment/services", headers=auth(tenant_admin["token"]))
    b = client.get("/api/environment/services", headers=auth(other_tenant_admin["token"]))
    assert a.status_code == 200 and b.status_code == 200
    paths = [c[1] for c in rm.calls]
    ta, tb = rm_tenant(enabled["id"]), rm_tenant(other_tenant_admin["tenant"]["id"])
    assert paths == [f"/v1/tenants/{ta}/services", f"/v1/tenants/{tb}/services"]
    # ação sobre serviço em nome de outra empresa não existe: só há o tenant da sessão
    assert all(ta not in p for p in paths[1:])
    assert client.post("/api/environment/services/web/stop").status_code == 401


def test_member_without_permission_gets_403(client, master_token, enabled, rm):
    member = _new_user(client, master_token, enabled)
    h = auth(member["token"])
    assert client.get("/api/environment/services", headers=h).status_code == 403
    assert client.post("/api/environment/services/web/stop", headers=h).status_code == 403
    assert client.get("/api/environment/services/web/logs", headers=h).status_code == 403
    assert rm.calls == []


def test_actions_are_audited_and_validated(client, enabled, tenant_admin, rm):
    h = auth(tenant_admin["token"])
    for action in ("stop", "start", "restart"):
        r = client.post(f"/api/environment/services/web/{action}", headers=h)
        assert r.status_code == 200 and r.json()["action"] == action
    kinds = [k for k, _ in _events(enabled["id"])]
    assert kinds == ["service_stop", "service_start", "service_restart"]
    assert _events(enabled["id"])[0][1] == {"service": "web"}
    n = len(rm.calls)
    assert client.post("/api/environment/services/Web;x/stop", headers=h).status_code in (400, 404)
    assert client.post("/api/environment/services/9a/stop", headers=h).status_code == 400
    assert client.post("/api/environment/services/web/rm", headers=h).status_code == 404
    assert len(rm.calls) == n


def test_logs_redacted_and_audited(client, enabled, tenant_admin, rm):
    r = client.get("/api/environment/services/web/logs", headers=auth(tenant_admin["token"]))
    assert r.status_code == 200
    assert "supersecreta" not in r.text and "linha 1" in r.json()["log"]
    assert _events(enabled["id"])[-1][0] == "service_logs"


def test_stopped_environment_is_shown_without_waking(client, enabled, tenant_admin, rm):
    rm.state["payload"] = {
        "ambiente": "parado",
        "servicos": [],
        "soltos": [],
        "conteineres": [],
        "docker": False,
        "avisos": [],
    }
    r = client.get("/api/environment/services", headers=auth(tenant_admin["token"]))
    assert r.status_code == 200 and r.json()["ambiente"] == "parado"
    assert all(m == "GET" for m, _, _ in rm.calls)  # nada de start/wake


def test_disabled_tenant_and_unknown_environment_do_not_error(client, tenant_admin, rm):
    r = client.get("/api/environment/services", headers=auth(tenant_admin["token"]))
    assert r.status_code == 200 and r.json()["ambiente"] == "desativado"
    assert rm.calls == []


def test_no_internal_vocabulary(client, enabled, tenant_admin, rm):
    h = auth(tenant_admin["token"])
    text = (
        client.get("/api/environment/services", headers=h).text
        + client.get("/api/environment/services/web/logs", headers=h).text
    ).lower()
    assert not any(w in text for w in FORBIDDEN_WORDS)
    import inspect

    from app.routes import environment

    src = inspect.getsource(environment).lower().split("def list_services")[1]
    assert not any(f'"{w}' in src for w in FORBIDDEN_WORDS)
