"""Rotas internas do kernel de skills: artefatos, cobranças, documentos e memória."""

import base64
import uuid

import psycopg
import pytest
from app.config import settings
from app.run_tokens import issue_run_token
from tests.conftest import auth
from tests.test_skills_agents import SERVICE, _enable, _run_for

pytestmark = pytest.mark.integration


def _sql(query, params=()):
    with psycopg.connect(settings.database_url, autocommit=True) as conn:
        cur = conn.execute(query, params)
        return cur.fetchall() if cur.description else None


@pytest.fixture
def enabled(client, master_token, tenant):
    _enable(client, master_token, tenant)
    return tenant


def _bearer_for(run_id):
    row = _sql(
        "SELECT tenant_id, user_id, chat_id, template_id, version_id FROM chat_runs "
        "WHERE run_id::text=%s",
        (run_id,),
    )[0]
    _sql("UPDATE chat_runs SET status='running' WHERE run_id::text=%s", (run_id,))
    tok = issue_run_token(
        run_id=run_id,
        tenant_id=str(row[0]),
        user_id=str(row[1]),
        chat_id=str(row[2]),
        template_id=str(row[3]),
        version_id=str(row[4]),
    )
    return {"Authorization": f"Bearer {tok}"}, row


def test_artifact_put_stores_file_and_registers_in_the_run_chat(
    client, enabled, tenant_admin, fake_harness
):
    run_id, _ = _run_for(client, tenant_admin, fake_harness)
    png = b"\x89PNG\r\n\x1a\nfake"
    r = client.post(
        f"/internal/runs/{run_id}/artifacts/put",
        json={
            "kind": "image",
            "title": "grafico.png",
            "extension": "png",
            "content_type": "image/png",
            "content_base64": base64.b64encode(png).decode(),
        },
        headers=SERVICE,
    )
    assert r.status_code == 201, r.text
    aid = r.json()["artifact_id"]
    row = _sql(
        "SELECT kind, title, tenant_id::text, storage_path FROM artifacts WHERE id::text=%s", (aid,)
    )[0]
    assert row[0] == "image" and row[1] == "grafico.png" and row[2] == str(enabled["id"])
    assert enabled["id"] in row[3]
    bad_ext = client.post(
        f"/internal/runs/{run_id}/artifacts/put",
        json={"extension": "p/g", "content_base64": "AAAA"},
        headers=SERVICE,
    )
    assert bad_ext.status_code == 400
    bad_b64 = client.post(
        f"/internal/runs/{run_id}/artifacts/put",
        json={"extension": "png", "content_base64": "@@@"},
        headers=SERVICE,
    )
    assert bad_b64.status_code == 400


def test_charge_record_and_lookup_roundtrip(client, enabled, tenant_admin, fake_harness):
    run_id, _ = _run_for(client, tenant_admin, fake_harness)
    ref = "PED-" + uuid.uuid4().hex[:6]
    url = f"/internal/payments/charges?reference_id={ref}&run_id={run_id}"
    assert client.get(url, headers=SERVICE).status_code == 404
    body = {
        "payment_id": "999" + uuid.uuid4().hex[:6],
        "reference_id": ref,
        "amount": "9.90",
        "status": "pending",
        "description": "Guia",
        "copy_paste": "000201PIX",
    }
    posted = client.post(f"/internal/runs/{run_id}/payments", json=body, headers=SERVICE)
    assert posted.status_code == 201
    got = client.get(url, headers=SERVICE).json()
    assert got["payment_id"] == body["payment_id"] and got["amount"] == "9.90"
    assert got["status"] == "pending"
    # webhook aprovando: status interno 'paid' volta como 'approved' (vocabulário do provedor)
    _sql("UPDATE payment_charges SET status='paid' WHERE reference_id=%s", (ref,))
    assert client.get(url, headers=SERVICE).json()["status"] == "approved"


def test_docs_search_is_scoped_to_the_runs_tenant(client, enabled, tenant_admin, fake_harness):
    run_id, _ = _run_for(client, tenant_admin, fake_harness)
    bearer, row = _bearer_for(run_id)
    fid = str(uuid.uuid4())
    _sql(
        "INSERT INTO files (id, tenant_id, name, content_type, size_bytes, storage_path, status) "
        "VALUES (%s,%s,'politica.txt','text/plain',10,'x','ready')",
        (fid, row[0]),
    )
    _sql(
        "INSERT INTO file_chunks (file_id, tenant_id, chunk_index, content) VALUES (%s,%s,0,%s)",
        (fid, row[0], "Política de troca: sete dias corridos para devolução"),
    )
    r = client.post("/internal/search/docs", json={"query": "troca devolução"}, headers=bearer)
    assert r.status_code == 200 and "sete dias" in r.json()["results"][0]["text"]
    # token de serviço sozinho não vale (precisa de uma run)
    denied = client.post("/internal/search/docs", json={"query": "troca"}, headers=SERVICE)
    assert denied.status_code == 401


def test_memory_proposals_review_flow_and_recall(client, enabled, tenant_admin, fake_harness):
    run_id, _ = _run_for(client, tenant_admin, fake_harness)
    h = auth(tenant_admin["token"])
    r = client.post(
        f"/internal/runs/{run_id}/memory-proposals",
        json={"text": "Prefere respostas curtas e e-mail às 8h"},
        headers=SERVICE,
    )
    assert r.status_code == 201
    pid = r.json()["id"]
    bearer, _ = _bearer_for(run_id)
    recall = {"query": "e-mail"}
    # só as aprovadas entram na recordação
    empty = client.post("/internal/memories/recall", json=recall, headers=bearer).json()
    assert empty["memories"] == []
    assert [p["id"] for p in client.get("/api/memories/proposals", headers=h).json()] == [pid]
    approved = client.post(f"/api/memories/proposals/{pid}/approve", headers=h).json()
    assert approved["status"] == "approved"
    got = client.post("/internal/memories/recall", json=recall, headers=bearer).json()
    assert got["memories"][0]["text"].startswith("Prefere")
    discarded = client.post(f"/api/memories/proposals/{pid}/discard", headers=h).json()
    assert discarded["status"] == "discarded"
    final = client.post("/internal/memories/recall", json=recall, headers=bearer).json()
    assert final["memories"] == []


def test_run_files_read_attachment_of_the_runs_tenant_only(
    client, enabled, tenant_admin, fake_harness
):
    from app.storage import save_bytes

    run_id, _ = _run_for(client, tenant_admin, fake_harness)
    rel = f"tenants/{enabled['id']}/chats/attachments/x/planilha.xlsx"
    ok_path = save_bytes(rel, b"XLSX", "x/y")
    got = client.get(f"/internal/runs/{run_id}/files", params={"ref": ok_path}, headers=SERVICE)
    assert got.status_code == 200 and got.content == b"XLSX"
    other = save_bytes("tenants/00000000-0000-0000-0000-000000000000/a.bin", b"OUTRA", "x/y")
    assert client.get(f"/internal/runs/{run_id}/files", params={"ref": other},
                      headers=SERVICE).status_code == 403
    assert client.get(f"/internal/runs/{run_id}/files", params={"ref": ok_path + "/../x"},
                      headers=SERVICE).status_code == 403
