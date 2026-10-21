from app import gateway_runs
from app.config import settings
from app.crypto import decrypt


class Row(dict):
    pass


class FakeConn:
    def __init__(self, own=None, stored=None):
        self.own, self.stored, self.inserts = own, stored or {}, []

    def execute(self, sql, params=()):
        conn = self

        class R:
            def fetchone(self_inner):
                if "FROM tenant_routers" in sql:
                    return {"gateway_tenant_id": conn.own} if conn.own else None
                if "FROM gateway_run_keys" in sql:
                    from app.crypto import encrypt

                    k = conn.stored.get((params[0], params[1]))
                    return {"key_encrypted": encrypt(k)} if k else None

        if sql.lstrip().startswith("INSERT"):
            self.inserts.append(params)
        return R()


def _on(monkeypatch, combos):
    monkeypatch.setattr(settings, "gateway_runs_enabled", True)
    monkeypatch.setattr(gateway_runs, "resolver_segredo", lambda n: "x")
    monkeypatch.setattr(
        gateway_runs.gateway_client, "combo_names", lambda b, t, gw: combos.get(gw, set())
    )
    calls = []
    monkeypatch.setattr(
        gateway_runs.gateway_client,
        "create_combo_key",
        lambda b, t, gw, combo: (calls.append((gw, combo)) or ("sk-new", "kid")),
    )
    return calls


def test_disabled_returns_none(monkeypatch):
    monkeypatch.setattr(settings, "gateway_runs_enabled", False)
    assert gateway_runs.run_gateway(FakeConn(), "t", "c") is None


def test_own_tenant_combo_creates_and_stores_key(monkeypatch):
    calls = _on(monkeypatch, {"own": {"main"}, "platform": {"main"}})
    conn = FakeConn(own="own")
    out = gateway_runs.run_gateway(conn, "t", "main")
    assert out["api_key"] == "sk-new" and out["base_url"].endswith("/v1")
    assert calls == [("own", "main")]
    assert conn.inserts[0][0] == "own" and decrypt(conn.inserts[0][3]) == "sk-new"


def test_falls_back_to_platform_when_own_lacks_combo(monkeypatch):
    calls = _on(monkeypatch, {"own": set(), "platform": {"ninerouter-gpt"}})
    out = gateway_runs.run_gateway(FakeConn(own="own"), "t", "ninerouter-gpt")
    assert out and calls == [("platform", "ninerouter-gpt")]


def test_reuses_stored_key(monkeypatch):
    calls = _on(monkeypatch, {})
    out = gateway_runs.run_gateway(FakeConn(stored={("platform", "c"): "sk-old"}), "t", "c")
    assert out["api_key"] == "sk-old" and calls == []


def test_unknown_combo_or_gateway_error_falls_back_to_none(monkeypatch):
    _on(monkeypatch, {})
    assert gateway_runs.run_gateway(FakeConn(), "t", "nope") is None

    def boom(*_a):
        raise RuntimeError("x")

    monkeypatch.setattr(gateway_runs.gateway_client, "combo_names", boom)
    assert gateway_runs.run_gateway(FakeConn(), "t", "c") is None


def test_litellm_client_refuses_when_disabled(monkeypatch):
    import asyncio

    import pytest

    from app import litellm_client

    monkeypatch.setattr(settings, "litellm_enabled", False)
    with pytest.raises(litellm_client.LiteLLMError):
        asyncio.run(litellm_client._request("http://x", "k", "GET", "/health"))
    assert asyncio.run(litellm_client.health("http://x")) is False
