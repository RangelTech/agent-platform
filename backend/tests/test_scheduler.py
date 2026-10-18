"""Rotinas agendadas: criação pelo agente (rota interna), isolamento, limites e disparo."""

import asyncio

import psycopg
import pytest
from app import scheduler
from app.config import settings
from fastapi import HTTPException
from tests.conftest import auth
from tests.test_skills_agents import SERVICE, _enable, _new_user, _run_for

pytestmark = pytest.mark.integration


def _sql(query, params=()):
    with psycopg.connect(settings.database_url, autocommit=True) as conn:
        cur = conn.execute(query, params)
        return cur.fetchall() if cur.description else None


@pytest.fixture
def enabled(client, master_token, tenant):
    _enable(client, master_token, tenant)
    return tenant


def _create(client, run_id, **over):
    body = {"op": "create", "name": "Resumo diário", "cron": "0 8 * * *",
            "timezone": "America/Sao_Paulo",
            "instruction": "Envie a cada usuário o resumo do dia por e-mail."}
    body.update(over)
    return client.post(f"/internal/runs/{run_id}/schedules", json=body, headers=SERVICE)


def test_next_run_respects_timezone_and_minimum_interval():
    from datetime import UTC, datetime

    nxt = scheduler.next_run("0 8 * * *", "America/Sao_Paulo",
                             datetime(2026, 10, 18, 12, 0, tzinfo=UTC))
    assert nxt == datetime(2026, 10, 19, 11, 0, tzinfo=UTC)  # 8h em Brasília = 11h UTC
    for bad in ("*/5 * * * *", "* * * * *"):
        with pytest.raises(Exception) as e:
            scheduler.next_run(bad, "America/Sao_Paulo")
        assert "15 minutos" in str(e.value.detail)
    with pytest.raises(HTTPException):
        scheduler.next_run("não é cron", "America/Sao_Paulo")
    with pytest.raises(HTTPException):
        scheduler.next_run("0 8 * * *", "Marte/Olimpo")


def test_agent_creates_lists_pauses_resumes_deletes(client, enabled, tenant_admin, fake_harness):
    run_id, _ = _run_for(client, tenant_admin, fake_harness)
    r = _create(client, run_id)
    assert r.status_code == 200, r.text
    job = r.json()
    assert job["status"] == "active" and job["next_run_at"]
    listed = client.post(f"/internal/runs/{run_id}/schedules", json={"op": "list"}, headers=SERVICE)
    assert [s["id"] for s in listed.json()["schedules"]] == [job["id"]]
    p = client.post(f"/internal/runs/{run_id}/schedules",
                    json={"op": "pause", "id": job["id"]}, headers=SERVICE).json()
    assert p["status"] == "paused" and p["next_run_at"] is None
    p = client.post(f"/internal/runs/{run_id}/schedules",
                    json={"op": "resume", "id": job["id"]}, headers=SERVICE).json()
    assert p["status"] == "active" and p["next_run_at"]
    d = client.post(f"/internal/runs/{run_id}/schedules",
                    json={"op": "delete", "id": job["id"]}, headers=SERVICE)
    assert d.status_code == 200
    assert client.post(f"/internal/runs/{run_id}/schedules", json={"op": "list"},
                       headers=SERVICE).json()["schedules"] == []


def test_schedule_validation_errors_are_400(client, enabled, tenant_admin, fake_harness):
    run_id, _ = _run_for(client, tenant_admin, fake_harness)
    assert _create(client, run_id, cron="*/5 * * * *").status_code == 400
    assert _create(client, run_id, cron="nada").status_code == 400
    assert _create(client, run_id, timezone="Marte/Olimpo").status_code == 400


def test_user_sees_only_own_schedules_and_cannot_touch_others(
    client, master_token, enabled, tenant_admin, fake_harness
):
    run_id, _ = _run_for(client, tenant_admin, fake_harness)
    job = _create(client, run_id).json()
    mine = client.get("/api/schedules", headers=auth(tenant_admin["token"])).json()["schedules"]
    assert [s["id"] for s in mine] == [job["id"]]
    other = _new_user(client, master_token, enabled)
    oh = auth(other["token"])
    assert client.get("/api/schedules", headers=oh).json()["schedules"] == []
    assert client.post(f"/api/schedules/{job['id']}/pause", headers=oh).status_code == 404
    assert client.delete(f"/api/schedules/{job['id']}", headers=oh).status_code == 404
    # o dono pausa e apaga pela API de usuário
    h = auth(tenant_admin["token"])
    assert client.post(f"/api/schedules/{job['id']}/pause", headers=h).json()["status"] == "paused"
    assert client.delete(f"/api/schedules/{job['id']}", headers=h).status_code == 200


def test_run_token_cannot_create_in_a_finished_run(client, enabled, tenant_admin, fake_harness):
    run_id, _ = _run_for(client, tenant_admin, fake_harness)
    from app.run_tokens import issue_run_token

    row = _sql("SELECT tenant_id, user_id, chat_id, template_id, version_id FROM chat_runs "
               "WHERE run_id::text=%s", (run_id,))[0]
    tok = issue_run_token(run_id=run_id, tenant_id=str(row[0]), user_id=str(row[1]),
                          chat_id=str(row[2]), template_id=str(row[3]), version_id=str(row[4]))
    _sql("UPDATE chat_runs SET status='done' WHERE run_id::text=%s", (run_id,))
    r = client.post(f"/internal/runs/{run_id}/schedules", json={"op": "list"},
                    headers={"Authorization": f"Bearer {tok}"})
    assert r.status_code == 401


def test_due_job_is_dispatched_once_with_schedule_channel(
    client, enabled, tenant_admin, fake_harness
):
    run_id, _ = _run_for(client, tenant_admin, fake_harness)
    job = _create(client, run_id).json()
    _sql("UPDATE scheduled_jobs SET next_run_at = now() - interval '1 minute' WHERE id::text=%s",
         (job["id"],))
    fake_harness.reset()

    async def go():
        n1 = await scheduler.tick()
        await asyncio.sleep(1.5)  # deixa a tarefa de fundo terminar
        n2 = await scheduler.tick()
        return n1, n2

    n1, n2 = asyncio.run(go())
    assert (n1, n2) == (1, 0)  # disparou uma vez; próximo horário já foi reagendado
    sent = fake_harness.calls["runs"]
    assert len(sent) == 1 and sent[0]["channel"] == "schedule"
    assert "resumo do dia" in sent[0]["message"]
    row = _sql("SELECT last_status, run_count, next_run_at > now(), last_chat_id FROM "
               "scheduled_jobs WHERE id::text=%s", (job["id"],))[0]
    assert row[0] == "ok" and row[1] == 1 and row[2] is True and row[3] is not None
    msgs = _sql("SELECT role, content FROM chat_messages WHERE chat_id = %s ORDER BY created_at",
                (row[3],))
    assert [m[0] for m in msgs] == ["user", "assistant"]


def test_paused_job_never_dispatches(client, enabled, tenant_admin, fake_harness):
    run_id, _ = _run_for(client, tenant_admin, fake_harness)
    job = _create(client, run_id).json()
    client.post(f"/internal/runs/{run_id}/schedules", json={"op": "pause", "id": job["id"]},
                headers=SERVICE)
    _sql("UPDATE scheduled_jobs SET next_run_at = now() - interval '1 hour' WHERE id::text=%s",
         (job["id"],))
    assert asyncio.run(scheduler.tick()) == 0


def test_per_user_limit(client, enabled, tenant_admin, fake_harness):
    run_id, _ = _run_for(client, tenant_admin, fake_harness)
    for i in range(scheduler.MAX_PER_USER):
        assert _create(client, run_id, name=f"r{i}").status_code == 200
    assert _create(client, run_id, name="demais").status_code == 409
