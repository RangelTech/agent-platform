from app.main import app
from fastapi.testclient import TestClient


def test_health():
    # lifespan intentionally not started: /health must not depend on the DB
    client = TestClient(app, raise_server_exceptions=False)
    r = client.get("/health")
    assert r.status_code == 200
    assert r.json() == {"status": "ok", "service": "backend"}


def test_ready_reports_startup_not_run_without_lifespan():
    client = TestClient(app, raise_server_exceptions=False)
    r = client.get("/health/ready")
    assert r.status_code == 503
    assert r.json()["status"] == "not_ready"
    assert r.json()["migrations_ok"] is False


def test_ready_reports_migration_failure(monkeypatch, caplog):
    from app import main

    def fail_migrations():
        raise RuntimeError("database unavailable")

    monkeypatch.setattr(main, "run_migrations", fail_migrations)
    with caplog.at_level("ERROR"):
        with TestClient(main.app, raise_server_exceptions=False) as client:
            assert client.get("/health").status_code == 200
            r = client.get("/health/ready")

    assert r.status_code == 503
    body = r.json()
    assert body["status"] == "not_ready"
    assert body["migrations_ok"] is False
    assert "MIGRATION_FAILED" in caplog.text
    # O motivo é para quem lê o log, não para quem chama a URL: o endpoint é
    # público e a mensagem de uma falha real do psycopg traz host, porta e
    # usuário do banco. Casar o corpo inteiro é de propósito — um campo novo
    # que carregue diagnóstico quebra este teste em vez de vazar em silêncio.
    assert body == {"status": "not_ready", "service": "backend", "migrations_ok": False}
    assert "database unavailable" in caplog.text
