import time
import uuid

import psycopg
import pytest
from app.config import settings
from app.ui_context import sign_context
from tests.conftest import auth

pytestmark = pytest.mark.integration


def _seed_run(tenant_id, user_id, status="done", finished=True):
    run_id = str(uuid.uuid4())
    with psycopg.connect(settings.database_url) as conn:
        conn.execute(
            """INSERT INTO chat_runs (run_id, tenant_id, user_id, chat_id, kernel, status,
                                      created_at, finished_at)
               VALUES (%s, %s, %s, 'c', 'llm', %s, now() - interval '5 seconds',
                       CASE WHEN %s THEN now() ELSE NULL END)""",
            (run_id, tenant_id, user_id, status, finished),
        )
        conn.commit()
    return run_id


def test_runs_list_is_tenant_scoped_and_paginated(client, master_token, tenant_admin):
    other = client.post(
        "/api/tenants",
        json={"name": "R", "tenant_key": f"r-{uuid.uuid4().hex[:6]}"},
        headers=auth(master_token),
    ).json()
    tid = tenant_admin["user"]["tenant_id"]
    mine = [_seed_run(tid, tenant_admin["user"]["id"]) for _ in range(3)]
    foreign = _seed_run(other["id"], None)

    body = client.get("/api/runs?per_page=2", headers=auth(tenant_admin["token"])).json()
    assert body["total"] == 3
    assert len(body["items"]) == 2
    assert body["per_page"] == 2
    page2 = client.get("/api/runs?per_page=2&page=2", headers=auth(tenant_admin["token"])).json()
    ids = {i["id"] for i in body["items"] + page2["items"]}
    assert ids == set(mine)
    assert foreign not in ids
    first = body["items"][0]
    assert first["status"] == "done"
    assert first["duration_ms"] >= 4000


def test_runs_running_has_no_duration(client, tenant_admin):
    _seed_run(tenant_admin["user"]["tenant_id"], tenant_admin["user"]["id"], "running", False)
    items = client.get("/api/runs", headers=auth(tenant_admin["token"])).json()["items"]
    assert any(i["status"] == "running" and i["duration_ms"] is None for i in items)


def _ctx(tenant_id, scopes):
    now = int(time.time())
    return {
        "X-UI-Context": sign_context(
            {
                "aud": "ui",
                "scopes": list(scopes),
                "nonce": uuid.uuid4().hex,
                "iat": now,
                "exp": now + 30,
                "engine_tenant_id": tenant_id,
                "chatwoot_user_id": 777,
                "account_id": 1,
                "email": "u777@x.com",
                "name": "U",
                "flags": {},
            }
        )
    }


def test_runs_common_user_sees_only_own(client, tenant_admin):
    tid = tenant_admin["user"]["tenant_id"]
    _seed_run(tid, tenant_admin["user"]["id"])  # de outra pessoa
    first = client.get("/api/runs", headers=_ctx(tid, ["ai.runs.view"]))
    assert first.status_code == 200
    assert first.json()["total"] == 0
    with psycopg.connect(settings.database_url) as conn:
        me = conn.execute(
            "SELECT id FROM users WHERE tenant_id = %s AND chatwoot_user_id = 777", (tid,)
        ).fetchone()[0]
    mine = _seed_run(tid, str(me))
    own = client.get("/api/runs", headers=_ctx(tid, ["ai.runs.view"])).json()
    assert [i["id"] for i in own["items"]] == [mine]
    wide = client.get("/api/runs", headers=_ctx(tid, ["ai.runs.view", "ai.usage.view"])).json()
    assert wide["total"] >= 2


def test_runs_requires_permission(client, tenant_admin):
    assert client.get("/api/runs").status_code == 401
    tid = tenant_admin["user"]["tenant_id"]
    assert client.get("/api/runs", headers=_ctx(tid, ["ai.chat.view"])).status_code == 403
