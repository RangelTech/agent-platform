"""Black-box tests of /v1/runs over the HTTP seam with the stub provider.

Everything here needs Postgres (the checkpointer) — marked integration.
"""

import json
import uuid

import pytest
from app.main import app
from httpx import ASGITransport, AsyncClient

pytestmark = [pytest.mark.integration, pytest.mark.anyio]


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.fixture
async def client():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://kernel") as c:
        yield c
    from app.graph import close_graph

    await close_graph()


def _events(sse_text: str) -> list[tuple[str, dict]]:
    events = []
    for block in sse_text.strip().split("\n\n"):
        event, data = None, None
        for line in block.splitlines():
            if line.startswith("event: "):
                event = line[len("event: ") :]
            elif line.startswith("data: "):
                data = json.loads(line[len("data: ") :])
        if event:
            events.append((event, data))
    return events


def _run_payload(message: str, thread_id: str | None = None) -> dict:
    return {
        "thread_id": thread_id or f"t-{uuid.uuid4().hex[:8]}",
        "message": message,
        "model": {"provider": "stub", "model": "stub-1"},
        "system_prompt": "Você é um assistente de testes.",
    }


async def test_run_streams_tokens_and_finishes_with_done(client):
    await client.post(
        "/stub/script",
        json={"rules": [], "default": "uma resposta com cinco palavras"},
    )
    r = await client.post("/v1/runs", json=_run_payload("olá"))
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/event-stream")

    events = _events(r.text)
    tokens = [d["text"] for e, d in events if e == "token"]
    dones = [d for e, d in events if e == "done"]

    assert len(tokens) == 5  # streamed word by word, not one blob
    assert len(dones) == 1
    assert "".join(tokens) == "uma resposta com cinco palavras"
    assert dones[0]["text"] == "uma resposta com cinco palavras"


async def test_conversation_history_persists_across_turns(client):
    thread = f"t-{uuid.uuid4().hex[:8]}"
    await client.post(
        "/stub/script", json={"rules": [], "default": "entendido"}
    )
    await client.post("/v1/runs", json=_run_payload("primeira mensagem", thread))
    await client.post("/v1/runs", json=_run_payload("segunda mensagem", thread))

    # The checkpointer must hold the full exchange: 2 user + 2 assistant.
    from app.graph import get_graph

    graph = await get_graph()
    state = await graph.aget_state({"configurable": {"thread_id": thread}})
    contents = [m.content for m in state.values["messages"]]
    assert "primeira mensagem" in contents
    assert "segunda mensagem" in contents
    assert len(state.values["messages"]) == 4


async def test_scripted_rule_matches_by_substring(client):
    await client.post(
        "/stub/script",
        json={
            "rules": [["previsão do tempo", "vai chover amanhã"]],
            "default": "não sei",
        },
    )
    r = await client.post(
        "/v1/runs", json=_run_payload("qual a previsão do tempo em SP?")
    )
    done = next(d for e, d in _events(r.text) if e == "done")
    assert done["text"] == "vai chover amanhã"


async def test_provider_failure_becomes_an_error_event(client):
    await client.post(
        "/stub/script",
        json={"rules": [["erro proposital", "__RAISE__"]], "default": "ok"},
    )
    r = await client.post("/v1/runs", json=_run_payload("erro proposital"))
    events = _events(r.text)
    assert events[-1][0] == "error"
    assert not any(e == "done" for e, _ in events)


async def test_distinct_threads_do_not_share_history(client):
    a, b = f"t-{uuid.uuid4().hex[:6]}", f"t-{uuid.uuid4().hex[:6]}"
    await client.post("/v1/runs", json=_run_payload("mensagem só do A", a))
    await client.post("/v1/runs", json=_run_payload("mensagem só do B", b))

    from app.graph import get_graph

    graph = await get_graph()
    state_b = await graph.aget_state({"configurable": {"thread_id": b}})
    contents = " ".join(m.content for m in state_b.values["messages"])
    assert "mensagem só do A" not in contents
