"""Conversation graph.

T03 scope: a single-agent graph — one LLM node over MessagesState with a
Postgres checkpointer (thread = chat). The supervisor/specialist topology
arrives with the templates ticket; this file already isolates the seam where
that will land (build_graph).
"""

from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from langgraph.config import get_stream_writer
from langgraph.graph import END, START, MessagesState, StateGraph
from psycopg_pool import AsyncConnectionPool

from app.config import settings
from app.providers import ModelConfig, stream_completion


class RunState(MessagesState):
    """Conversation state. `model` and `system_prompt` ride along per run and
    are not persisted as messages."""

    model: dict
    system_prompt: str


async def _call_llm(state: RunState) -> dict:
    config = ModelConfig(**state["model"])
    writer = get_stream_writer()

    messages: list[dict] = []
    if state.get("system_prompt"):
        messages.append({"role": "system", "content": state["system_prompt"]})
    for m in state["messages"]:
        role = "assistant" if m.type == "ai" else "user"
        messages.append({"role": role, "content": m.content})

    parts: list[str] = []
    async for delta in stream_completion(config, messages):
        parts.append(delta)
        writer({"type": "token", "text": delta})

    return {"messages": [{"role": "assistant", "content": "".join(parts)}]}


def build_graph(checkpointer) -> object:
    graph = StateGraph(RunState)
    graph.add_node("llm", _call_llm)
    graph.add_edge(START, "llm")
    graph.add_edge("llm", END)
    return graph.compile(checkpointer=checkpointer)


_pool: AsyncConnectionPool | None = None
_checkpointer: AsyncPostgresSaver | None = None
_graph = None


async def get_graph():
    """Lazily build the singleton graph with its Postgres checkpointer."""
    global _pool, _checkpointer, _graph
    if _graph is None:
        _pool = AsyncConnectionPool(
            settings.database_url,
            min_size=1,
            max_size=settings.checkpoint_pool_size,
            kwargs={"autocommit": True, "prepare_threshold": 0},
            open=False,
        )
        await _pool.open()
        _checkpointer = AsyncPostgresSaver(_pool)
        await _checkpointer.setup()
        _graph = build_graph(_checkpointer)
    return _graph


async def close_graph() -> None:
    global _pool, _checkpointer, _graph
    if _pool is not None:
        await _pool.close()
    _pool = _checkpointer = _graph = None
