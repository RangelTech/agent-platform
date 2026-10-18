"""Entradas inválidas viram 4xx em pt-BR (nunca 500) e runs presas são encerradas."""

import psycopg
import pytest
from app import chat_runs
from app.config import settings
from tests.conftest import auth

pytestmark = pytest.mark.integration


def _sql(query, params=()):
    with psycopg.connect(settings.database_url, autocommit=True) as conn:
        cur = conn.execute(query, params)
        return cur.fetchall() if cur.description else None


@pytest.mark.parametrize(
    "body",
    [
        {"message": ""},
        {"message": "   "},
        {"message": "a" * 32_001},
        {"message": "oi\x00mundo"},
        {"message": "oi", "chat_id": "nao-e-uuid"},
        {"message": "oi", "template_id": "../../etc/passwd"},
    ],
)
def test_chat_send_invalid_input_is_4xx_with_message(client, tenant_admin, body):
    r = client.post("/api/chat/send", json=body, headers=auth(tenant_admin["token"]))
    assert r.status_code in (400, 404, 422), (body, r.status_code, r.text[:200])
    assert r.json()["detail"]


def test_chat_get_with_malformed_id_is_404(client, tenant_admin):
    h = auth(tenant_admin["token"])
    assert client.get("/api/chats/xyz", headers=h).status_code == 404
    assert client.get("/api/chats/xyz/messages", headers=h).status_code == 404


def test_sweep_stale_closes_old_running_runs_only(client, tenant, tenant_admin):
    tid = tenant["id"]
    old, fresh = "00000000-0000-4000-8000-0000000000a1", "00000000-0000-4000-8000-0000000000a2"
    for rid, age in ((old, "2 hours"), (fresh, "1 minute")):
        _sql(
            "INSERT INTO chat_runs (run_id, tenant_id, chat_id, kernel, status, created_at) "
            f"VALUES (%s, %s, 'c', 'harness', 'running', now() - interval '{age}')",
            (rid, tid),
        )
    assert chat_runs.sweep_stale(25) >= 1
    rows = _sql("SELECT run_id::text, status FROM chat_runs WHERE run_id::text IN (%s, %s)", (old, fresh))
    st = dict(rows)
    assert st[old] == "error" and st[fresh] == "running"
