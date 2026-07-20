"""execute_sql_write: guardrails, real writes and the confirmation clause."""

import sqlite3
import uuid

import pytest
from app.datasources import make_temp_sqlite, validate_write
from app.graph import WRITE_CONFIRMATION_CLAUSE, build_supervisor_prompt
from tests.conftest import sse_events as _events

pytestmark = [pytest.mark.integration, pytest.mark.anyio]

STUB = {"provider": "stub", "model": "stub-1"}
ALLOWED = ["pedidos", "itens_pedido"]

SEED = """
CREATE TABLE pedidos (id INTEGER PRIMARY KEY, cliente TEXT, total REAL);
CREATE TABLE clientes (id INTEGER PRIMARY KEY, nome TEXT);
INSERT INTO clientes VALUES (1, 'ACME');
"""


# ---------- guardrails (pure unit) ----------

def test_guard_blocks_ddl():
    with pytest.raises(ValueError, match="INSERT, UPDATE ou DELETE"):
        validate_write("DROP TABLE pedidos", ALLOWED)
    with pytest.raises(ValueError, match="um statement"):
        validate_write("INSERT INTO pedidos SELECT * FROM x; DROP TABLE y", ALLOWED)
    with pytest.raises(ValueError, match="proibidos"):
        validate_write("UPDATE pedidos SET a=1 WHERE id=1 AND alter = 2", ALLOWED)


def test_guard_blocks_update_without_where():
    with pytest.raises(ValueError, match="sem WHERE"):
        validate_write("UPDATE pedidos SET total = 0", ALLOWED)
    with pytest.raises(ValueError, match="sem WHERE"):
        validate_write("DELETE FROM pedidos", ALLOWED)


def test_guard_blocks_multi_statement():
    with pytest.raises(ValueError, match="um statement"):
        validate_write(
            "INSERT INTO pedidos (id) VALUES (1); INSERT INTO pedidos (id) VALUES (2)",
            ALLOWED,
        )


def test_guard_blocks_table_outside_allowlist():
    with pytest.raises(ValueError, match="não está na lista"):
        validate_write("INSERT INTO clientes (nome) VALUES ('x')", ALLOWED)


def test_guard_accepts_valid_statements():
    assert validate_write("INSERT INTO pedidos (cliente) VALUES ('ACME')", ALLOWED) == "pedidos"
    assert (
        validate_write("UPDATE pedidos SET total = 10 WHERE id = 1", ALLOWED) == "pedidos"
    )
    assert validate_write("insert into public.pedidos (id) values (9)", ALLOWED) == "pedidos"


# ---------- confirmation clause ----------

def test_confirmation_clause_only_when_flag_and_tables():
    on = build_supervisor_prompt("Base.", [], True)
    off = build_supervisor_prompt("Base.", [], False)
    assert WRITE_CONFIRMATION_CLAUSE in on
    assert WRITE_CONFIRMATION_CLAUSE not in off


# ---------- end to end over the HTTP seam ----------

async def test_write_flow_with_confirmation(client):
    db = make_temp_sqlite(SEED)
    payload = {
        "thread_id": f"t-{uuid.uuid4().hex[:8]}",
        "message": "registre um pedido de 2 parafusos para ACME",
        "supervisor": {"prompt": "Você vende.", "model": STUB},
        "agents": [
            {
                "name": "vendas_agent",
                "description": "registra pedidos",
                "prompt": "Você registra pedidos.",
                "model": STUB,
                "tools": ["execute_sql_write"],
            }
        ],
        "max_steps": 4,
        "datasources": [{"name": "erp", "kind": "sqlite", "config": {"path": db}}],
        "write_tables": ["pedidos"],
        "require_write_confirmation": True,
    }

    # Turn 1: the scripted supervisor proposes and asks for confirmation.
    await client.post(
        "/stub/script",
        json={
            "rules": [
                [
                    "registre um pedido",
                    "Vou inserir o pedido de ACME no valor de R$ 5,00. Confirma?",
                ],
                ["sim, confirmo", 'TOOL:vendas_agent:{"task": "gravar pedido confirmado"}'],
                [
                    "gravar pedido confirmado",
                    'TOOL:execute_sql_write:{"datasource": "erp", "statement": '
                    '"INSERT INTO pedidos (cliente, total) VALUES (\'ACME\', 5.0)"}',
                ],
                ["affected_rows", "Pedido registrado com sucesso!"],
                ["com sucesso", "Pedido registrado com sucesso!"],
            ],
            "default": "aguardando",
        },
    )
    r1 = await client.post("/v1/runs", json=payload)
    done1 = next(d for e, d in _events(r1.text) if e == "done")
    assert "Confirma?" in done1["text"]

    # Nothing written yet.
    conn = sqlite3.connect(db)
    assert conn.execute("SELECT count(*) FROM pedidos").fetchone()[0] == 0
    conn.close()

    # Turn 2: user confirms; the write happens exactly once.
    payload["message"] = "sim, confirmo"
    r2 = await client.post("/v1/runs", json=payload)
    events = _events(r2.text)
    tool_events = [d for e, d in events if e == "tool"]
    assert any(t["tool"] == "execute_sql_write" and t["status"] == "ok" for t in tool_events)
    done2 = next(d for e, d in events if e == "done")
    assert "sucesso" in done2["text"]

    conn = sqlite3.connect(db)
    rows = conn.execute("SELECT cliente, total FROM pedidos").fetchall()
    conn.close()
    assert rows == [("ACME", 5.0)]


async def test_write_outside_allowlist_is_refused_at_runtime(client):
    db = make_temp_sqlite(SEED)
    await client.post(
        "/stub/script",
        json={
            "rules": [
                ["apague os clientes", 'TOOL:vendas_agent:{"task": "limpar clientes"}'],
                [
                    "limpar clientes",
                    'TOOL:execute_sql_write:{"datasource": "erp", "statement": '
                    '"DELETE FROM clientes WHERE id = 1"}',
                ],
                ["não está na lista", "não tenho permissão para mexer em clientes"],
                ["não tenho permissão", "não tenho permissão para mexer em clientes"],
            ],
            "default": "ok",
        },
    )
    r = await client.post(
        "/v1/runs",
        json={
            "thread_id": f"t-{uuid.uuid4().hex[:8]}",
            "message": "apague os clientes",
            "supervisor": {"prompt": "Coordene.", "model": STUB},
            "agents": [
                {
                    "name": "vendas_agent",
                    "description": "pedidos",
                    "prompt": "Você registra pedidos.",
                    "model": STUB,
                    "tools": ["execute_sql_write"],
                }
            ],
            "max_steps": 4,
            "datasources": [{"name": "erp", "kind": "sqlite", "config": {"path": db}}],
            "write_tables": ["pedidos"],
        },
    )
    done = next(d for e, d in _events(r.text) if e == "done")
    assert "permissão" in done["text"]

    conn = sqlite3.connect(db)
    assert conn.execute("SELECT count(*) FROM clientes").fetchone()[0] == 1
    conn.close()
