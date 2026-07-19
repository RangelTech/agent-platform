"""Conversation runtime.

Topology: one supervisor talks to the user and calls specialists as tools
(agents-as-tools). Each specialist is a single LLM call with its own prompt
and model. The internal tool-loop is ephemeral — only the user/assistant
exchange is checkpointed, which keeps history small and token-cheap.

Hard limits (max_steps per turn) make runaway loops impossible.
"""

import json
import logging

from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from langgraph.config import get_stream_writer
from langgraph.graph import END, START, MessagesState, StateGraph
from psycopg_pool import AsyncConnectionPool

from app.config import settings
from app.providers import ModelConfig, complete

logger = logging.getLogger(__name__)


class RunState(MessagesState):
    """`run_config` rides along per run and is not part of the history."""

    run_config: dict


def _history_messages(state: RunState, system_prompt: str) -> list[dict]:
    messages: list[dict] = []
    if system_prompt:
        messages.append({"role": "system", "content": system_prompt})
    for m in state["messages"]:
        role = "assistant" if m.type == "ai" else "user"
        messages.append({"role": role, "content": m.content})
    return messages


def _agent_tool_defs(agents: list[dict]) -> list[dict]:
    """Each specialist becomes one callable tool for the supervisor."""
    return [
        {
            "type": "function",
            "function": {
                "name": agent["name"],
                "description": agent["description"],
                "parameters": {
                    "type": "object",
                    "properties": {
                        "task": {
                            "type": "string",
                            "description": (
                                "Tarefa ou pergunta, completa e autocontida, "
                                "para este especialista resolver."
                            ),
                        }
                    },
                    "required": ["task"],
                },
            },
        }
        for agent in agents
    ]


async def _run_specialist(agent: dict, task: str, writer) -> str:
    writer({"type": "agent_start", "name": agent["name"]})
    config = ModelConfig(**agent["model"])
    messages = [
        {"role": "system", "content": agent["prompt"]},
        {"role": "user", "content": task},
    ]

    async def swallow(_delta: str):
        # Specialist output goes to the supervisor, not to the user stream.
        return None

    try:
        result = await complete(config, messages, swallow)
        output = result.content or "(especialista não retornou conteúdo)"
    except Exception as exc:  # noqa: BLE001 — reported into the loop, not fatal
        logger.exception("specialist %s failed", agent["name"])
        output = f"ERRO no especialista {agent['name']}: {exc}"
    writer({"type": "agent_done", "name": agent["name"]})
    return output


async def _supervisor_node(state: RunState) -> dict:
    run_config = state["run_config"]
    supervisor = run_config["supervisor"]
    agents = {a["name"]: a for a in run_config.get("agents", [])}
    max_steps = int(run_config.get("max_steps", settings.max_steps_default))
    writer = get_stream_writer()

    tool_defs = _agent_tool_defs(list(agents.values())) or None
    supervisor_config = ModelConfig(**supervisor["model"])
    messages = _history_messages(state, supervisor.get("prompt", ""))

    async def emit(delta: str):
        writer({"type": "token", "text": delta})

    final_text = ""
    for _step in range(max_steps):
        result = await complete(supervisor_config, messages, emit, tools=tool_defs)
        if not result.tool_calls:
            final_text = result.content
            break

        # Record the assistant turn that requested the calls, then answer them.
        messages.append(
            {
                "role": "assistant",
                "content": result.content or None,
                "tool_calls": [
                    {
                        "id": tc.id,
                        "type": "function",
                        "function": {"name": tc.name, "arguments": tc.arguments},
                    }
                    for tc in result.tool_calls
                ],
            }
        )
        for tc in result.tool_calls:
            agent = agents.get(tc.name)
            if agent is None:
                output = f"ERRO: especialista '{tc.name}' não existe."
            else:
                try:
                    task = json.loads(tc.arguments or "{}").get("task", "")
                except json.JSONDecodeError:
                    task = tc.arguments
                output = await _run_specialist(agent, task, writer)
            messages.append(
                {"role": "tool", "tool_call_id": tc.id, "content": output}
            )
    else:
        # Step budget exhausted: force a final answer, no tools allowed.
        writer({"type": "limit", "detail": "max_steps"})
        result = await complete(
            supervisor_config,
            messages
            + [
                {
                    "role": "user",
                    "content": (
                        "Limite de passos atingido. Responda agora ao usuário "
                        "com o que você já tem."
                    ),
                }
            ],
            emit,
        )
        final_text = result.content

    return {"messages": [{"role": "assistant", "content": final_text}]}


def build_graph(checkpointer) -> object:
    graph = StateGraph(RunState)
    graph.add_node("supervisor", _supervisor_node)
    graph.add_edge(START, "supervisor")
    graph.add_edge("supervisor", END)
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
