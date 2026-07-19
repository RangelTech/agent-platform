from app.main import app
from fastapi.testclient import TestClient


def test_health():
    # lifespan intentionally not started: /health must not depend on the DB
    client = TestClient(app, raise_server_exceptions=False)
    r = client.get("/health")
    assert r.status_code == 200
    assert r.json() == {"status": "ok", "service": "backend"}
