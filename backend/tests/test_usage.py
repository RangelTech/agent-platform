import uuid

import psycopg
import pytest
from app.config import settings
from tests.conftest import auth

pytestmark = pytest.mark.integration


def _seed_usage(tenant_id, model="gemini-flash-latest", cost=0.001):
    with psycopg.connect(settings.database_url) as conn:
        conn.execute(
            """INSERT INTO usage_records
                   (tenant_id, agent_name, provider, model, prompt_tokens,
                    completion_tokens, cost_usd, latency_ms)
               VALUES (%s, 'supervisor', 'gemini', %s, 100, 50, %s, 800)""",
            (tenant_id, model, cost),
        )
        conn.commit()


def test_usage_summary_is_tenant_scoped(client, master_token, tenant_admin):
    other = client.post(
        "/api/tenants",
        json={"name": "U", "tenant_key": f"u-{uuid.uuid4().hex[:6]}"},
        headers=auth(master_token),
    ).json()
    _seed_usage(tenant_admin["user"]["tenant_id"])
    _seed_usage(other["id"], cost=99.0)

    summary = client.get("/api/usage", headers=auth(tenant_admin["token"])).json()
    assert summary["totals"]["calls"] == 1
    assert summary["totals"]["cost_usd"] < 1  # the other tenant's 99 USD is invisible
    assert summary["by_model"][0]["model"] == "gemini-flash-latest"


def test_feedback_upsert_and_listing(client, tenant_admin, master_token, fake_kernel):
    import uuid as _uuid

    email = f"fb-{_uuid.uuid4().hex[:6]}@acme.com"
    client.post(
        "/api/users",
        json={"email": email, "name": "FB", "password": "senha-forte-123"},
        headers=auth(tenant_admin["token"]),
    )
    utoken = client.post(
        "/api/auth/login", json={"email": email, "password": "senha-forte-123"}
    ).json()["token"]

    from app.config import settings as backend_settings

    backend_settings.kernel_url = fake_kernel
    with client.stream(
        "POST", "/api/chat/send", json={"message": "oi"}, headers=auth(utoken)
    ) as response:
        "".join(response.iter_text())

    chats = client.get("/api/chats", headers=auth(utoken)).json()
    messages = client.get(f"/api/chats/{chats[0]['id']}/messages", headers=auth(utoken)).json()
    assistant = next(m for m in messages if m["role"] == "assistant")

    r = client.post(
        f"/api/chats/{chats[0]['id']}/feedback",
        json={"message_id": assistant["id"], "rating": 1, "comment": "boa"},
        headers=auth(utoken),
    )
    assert r.status_code == 201
    # Upsert: same user re-rates the same message.
    client.post(
        f"/api/chats/{chats[0]['id']}/feedback",
        json={"message_id": assistant["id"], "rating": -1, "comment": "mudei de ideia"},
        headers=auth(utoken),
    )

    listing = client.get("/api/feedback", headers=auth(tenant_admin["token"])).json()
    mine = [f for f in listing if f["comment"] == "mudei de ideia"]
    assert len(mine) == 1 and mine[0]["rating"] == -1


def test_feedback_rejects_foreign_message(client, tenant_admin, master_token, fake_kernel):
    r = client.post(
        f"/api/chats/{uuid.uuid4()}/feedback",
        json={"message_id": str(uuid.uuid4()), "rating": 1},
        headers=auth(tenant_admin["token"]),
    )
    assert r.status_code == 404
