import time
import uuid

from app.db import get_connection
from app.ui_context import sign_context


def _ctx(tenant_id, scopes, uid=501):
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
                "chatwoot_user_id": uid,
                "account_id": 1,
                "email": f"u{uid}@x.com",
                "name": "U",
                "flags": {},
            }
        )
    }


def _tenant(client):
    h = {
        "X-UI-Context": sign_context(
            {
                "aud": "install",
                "scopes": ["lifecycle"],
                "nonce": uuid.uuid4().hex,
                "iat": int(time.time()),
                "exp": int(time.time()) + 30,
            }
        )
    }
    return client.post(
        "/internal/ui/provision",
        json={"account_id": 900 + int(time.time()) % 90, "name": "Br"},
        headers=h,
    ).json()["engine_tenant_id"]


def test_chatwoot_context_replaces_login_and_scopes_gate_routes(client):
    with get_connection() as conn:
        conn.execute("TRUNCATE ui_nonces")
    tid = _tenant(client)
    ok = client.get("/api/chats", headers=_ctx(tid, ["ai.chat.view"]))
    assert ok.status_code == 200
    # sem o escopo, a mesma rota nega
    assert client.get("/api/chats", headers=_ctx(tid, ["ai.usage.view"])).status_code == 403
    # rota com permissao legada: so quem tem ai.agents.view lista ferramentas
    assert (
        client.get("/api/tools/catalog", headers=_ctx(tid, ["ai.agents.view"])).status_code == 200
    )
    assert client.get("/api/tools/catalog", headers=_ctx(tid, ["ai.chat.view"])).status_code == 403
    # contexto repetido e sem contexto/sessao
    h = _ctx(tid, ["ai.chat.view"])
    assert client.get("/api/chats", headers=h).status_code == 200
    assert client.get("/api/chats", headers=h).status_code == 401
    assert client.get("/api/chats").status_code == 401


def test_scopes_translate_only_what_they_say():
    from app.permissions import scopes_to_permissions

    p = scopes_to_permissions(["ai.environment.view"])
    assert p == {"environment": ["view"]}
    assert "manage" in scopes_to_permissions(["ai.environment.manage"])["environment"]
    assert scopes_to_permissions(["ai.nada"]) == {}
