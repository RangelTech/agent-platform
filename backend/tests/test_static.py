from app.main import _mount_spa
from fastapi import FastAPI
from fastapi.testclient import TestClient


def _app_with_spa(tmp_path):
    (tmp_path / "index.html").write_text("<html>spa</html>", encoding="utf-8")
    (tmp_path / "assets").mkdir()
    (tmp_path / "assets" / "app.js").write_text("console.log(1)", encoding="utf-8")

    app = FastAPI()

    @app.get("/api/ping")
    def ping():
        return {"pong": True}

    from app import main

    original = main._resolve_static_dir
    main._resolve_static_dir = lambda: tmp_path
    try:
        _mount_spa(app)
    finally:
        main._resolve_static_dir = original
    return app


def test_api_routes_are_not_shadowed_by_the_spa_fallback(tmp_path):
    client = TestClient(_app_with_spa(tmp_path))
    assert client.get("/api/ping").json() == {"pong": True}


def test_unknown_path_falls_back_to_index(tmp_path):
    client = TestClient(_app_with_spa(tmp_path))
    r = client.get("/chats/123")
    assert r.status_code == 200
    assert "spa" in r.text


def test_real_build_file_is_served(tmp_path):
    client = TestClient(_app_with_spa(tmp_path))
    assert client.get("/assets/app.js").text == "console.log(1)"


def test_traversal_outside_the_build_dir_is_refused(tmp_path):
    secret = tmp_path.parent / "secret.txt"
    secret.write_text("TOP SECRET", encoding="utf-8")

    build = tmp_path / "dist"
    build.mkdir()
    client = TestClient(_app_with_spa(build))

    r = client.get("/../secret.txt")
    assert "TOP SECRET" not in r.text
