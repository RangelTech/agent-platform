import contextlib

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


def test_health_db_does_a_real_round_trip():
    """Ao contrário de /health/ready (reflete o boot, congelado desde o
    startup), /health/db reconecta a cada chamada -- é o que teria detectado
    em minutos, não em dias, o incidente real de 29/09/2026 (Postgres em
    crash-loop desde 25/09 sem nenhum alerta)."""
    with TestClient(app, raise_server_exceptions=False) as client:
        r = client.get("/health/db")
    assert r.status_code == 200
    assert r.json() == {"status": "ok", "service": "backend"}


def test_health_db_reports_failure_instead_of_hanging_or_lying(monkeypatch, caplog):
    from app import main

    @contextlib.contextmanager
    def broken_connection():
        raise RuntimeError("connection to server at 1.2.3.4, port 5433 failed: FATAL")
        yield  # pragma: no cover -- never reached, the raise above always fires first

    monkeypatch.setattr(main, "get_connection", broken_connection)
    with caplog.at_level("ERROR"):
        with TestClient(main.app, raise_server_exceptions=False) as client:
            r = client.get("/health/db")

    assert r.status_code == 503
    # Mesmo corpo minimo do /health/ready: publico, sem host/porta/usuario.
    assert r.json() == {"status": "db_unreachable", "service": "backend"}
    assert "HEALTH_DB_FAILED" in caplog.text
    assert "1.2.3.4" in caplog.text  # o diagnóstico completo vai pro log, não pra resposta
