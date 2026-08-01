"""Conversation runtime.

Topology: one supervisor talks to the user and calls specialists as tools
(agents-as-tools). Each specialist is a single LLM call with its own prompt
and model. The internal tool-loop is ephemeral — only the user/assistant
exchange is checkpointed, which keeps history small and token-cheap.

Hard limits (max_steps per turn) make runaway loops impossible.
"""

import json
import logging
import time

from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from langgraph.config import get_stream_writer
from langgraph.graph import END, START, MessagesState, StateGraph
from psycopg_pool import AsyncConnectionPool

from app.config import settings
from app.providers import ModelConfig, complete
from app.tools import (
    ExternalServers,
    open_catalog_session,
    set_current_agent,
    set_run_context,
)

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


async def _record_usage(run_config: dict, agent_name: str, config: ModelConfig, result) -> None:
    from app.trace import insert_usage

    await insert_usage(
        tenant_id=run_config.get("tenant_id"),
        user_id=run_config.get("user_id"),
        chat_id=run_config.get("thread_id"),
        agent_name=agent_name,
        provider=config.provider,
        model=config.model,
        prompt_tokens=result.prompt_tokens,
        completion_tokens=result.completion_tokens,
        cost_usd=result.cost_usd,
        latency_ms=result.latency_ms,
    )


async def _record_tool_call(
    run_config: dict, agent: str, tool: str, arguments: dict, output: str,
    status: str, started: float, writer,
) -> None:
    duration_ms = int((time.monotonic() - started) * 1000)
    writer(
        {
            "type": "tool_call",
            "agent": agent,
            "tool": tool,
            "status": status,
            "duration_ms": duration_ms,
        }
    )
    from app.trace import insert_tool_call

    await insert_tool_call(
        tenant_id=run_config.get("tenant_id"),
        chat_id=run_config.get("thread_id"),
        agent_name=agent,
        tool_name=tool,
        input=arguments,
        output=output[:10_000],
        status=status,
        duration_ms=duration_ms,
    )


def _find_artifact_descriptors(parsed) -> list[dict]:
    """Artifact descriptors at the top level, one nesting level down, or in
    an 'artifacts' list — covers every catalog tool's return shape."""
    if not isinstance(parsed, dict):
        return []
    if "artifact_id" in parsed:
        return [parsed]
    found = []
    for value in parsed.values():
        if isinstance(value, dict) and "artifact_id" in value:
            found.append(value)
        elif isinstance(value, list):
            found.extend(v for v in value if isinstance(v, dict) and "artifact_id" in v)
    return found


async def _execute_tool(
    name: str, arguments: dict, catalog_session, external: ExternalServers
) -> str:
    if name in external.tools:
        return await external.call(name, arguments)
    result = await catalog_session.call_tool(name, arguments)
    parts = [c.text for c in result.content if getattr(c, "text", None)]
    return "\n".join(parts) or "(sem conteúdo)"


async def _run_specialist(
    agent: dict, task: str, writer, run_config: dict, tool_defs: dict,
    catalog_session, external: ExternalServers,
) -> str:
    """One specialist turn: its own model, its own tools, a short bounded
    tool-loop. Output goes back to the supervisor, not to the user stream."""
    writer({"type": "agent_start", "name": agent["name"]})
    set_current_agent(agent["name"], agent.get("model"))
    config = ModelConfig(**agent["model"])
    allowed_names = {t for t in agent.get("tools", []) if t in tool_defs}
    allowed = [tool_defs[t] for t in allowed_names] or None
    messages = [
        {"role": "system", "content": agent["prompt"]},
        {"role": "user", "content": task},
    ]

    async def swallow(_delta: str):
        return None

    output = ""
    try:
        for _round in range(settings.specialist_max_tool_rounds):
            result = await complete(config, messages, swallow, tools=allowed)
            await _record_usage(run_config, agent["name"], config, result)
            if not result.tool_calls:
                output = result.content or "(especialista não retornou conteúdo)"
                break
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
                try:
                    arguments = json.loads(tc.arguments or "{}")
                except json.JSONDecodeError:
                    arguments = {"_raw": tc.arguments}
                started = time.monotonic()
                if tc.name not in allowed_names:
                    # A model may hallucinate a tool it was never offered;
                    # refuse without executing.
                    tool_output = f"ERRO: tool {tc.name} não disponível para este agente"
                    status = "error"
                else:
                    try:
                        tool_output = await _execute_tool(
                            tc.name, arguments, catalog_session, external
                        )
                        status = "ok"
                    except Exception as exc:  # noqa: BLE001 — reported into the loop
                        tool_output = f"ERRO na tool {tc.name}: {exc}"
                        status = "error"
                await _record_tool_call(
                    run_config, agent["name"], tc.name, arguments, tool_output,
                    status, started, writer,
                )
                # Materialized artifacts surface as their own stream event so
                # the frontend can render/download them. Tools may return one
                # descriptor or nest several (forecast, sandbox).
                if status == "ok" and '"artifact_id"' in tool_output:
                    try:
                        parsed = json.loads(tool_output)
                    except json.JSONDecodeError:
                        parsed = None
                    for descriptor in _find_artifact_descriptors(parsed):
                        writer(
                            {
                                "type": "artifact",
                                "artifact_id": descriptor["artifact_id"],
                                "kind": descriptor.get("kind", ""),
                                "title": descriptor.get("title", ""),
                            }
                        )
                messages.append(
                    {"role": "tool", "tool_call_id": tc.id, "content": tool_output}
                )
        else:
            result = await complete(
                config,
                messages
                + [
                    {
                        "role": "user",
                        "content": (
                            "Responda agora com o que você tem. Se alguma operação "
                            "não foi executada, diga claramente que NÃO foi feita — "
                            "nunca afirme sucesso de algo que você não executou."
                        ),
                    }
                ],
                swallow,
            )
            await _record_usage(run_config, agent["name"], config, result)
            output = result.content or "(sem resposta)"
    except Exception as exc:  # noqa: BLE001 — reported into the loop, not fatal
        logger.exception("specialist %s failed", agent["name"])
        output = f"ERRO no especialista {agent['name']}: {exc}"
    writer({"type": "agent_done", "name": agent["name"]})
    return output


async def _load_tool_defs(catalog_session, external: ExternalServers) -> dict:
    """All available tools as OpenAI-style function defs, keyed by name."""
    defs: dict[str, dict] = {}
    listed = await catalog_session.list_tools()
    for tool in listed.tools:
        defs[tool.name] = {
            "type": "function",
            "function": {
                "name": tool.name,
                "description": tool.description or "",
                "parameters": tool.inputSchema or {"type": "object", "properties": {}},
            },
        }
    for public, (_server, _tool, schema) in external.tools.items():
        defs[public] = {
            "type": "function",
            "function": {
                "name": public,
                "description": f"Tool externa {public}",
                "parameters": schema or {"type": "object", "properties": {}},
            },
        }
    return defs


WRITE_CONFIRMATION_CLAUSE = (
    "\n\nREGRA DE ESCRITA: antes de qualquer operação que altere dados "
    "(criar pedido, efetuar venda, atualizar registro), apresente ao usuário um "
    "resumo claro da operação e SÓ execute depois que ele confirmar "
    "explicitamente na conversa (ex.: 'sim', 'confirmo'). Se a confirmação ainda "
    "não veio nesta conversa, pergunte e aguarde. "
    "Para criar um registro com filhos (um pedido e seus itens), use SEMPRE a "
    "tool execute_sql_transaction com TODOS os statements de uma vez — o pedido "
    "com RETURNING id e os itens referenciando {{returned:0}} — para que seja "
    "atômico. NUNCA crie o pedido e os itens em chamadas separadas e NUNCA repita "
    "um INSERT: se uma tool retornar status ok, a operação já foi gravada. Após "
    "executar, relate o resultado REAL retornado pela tool (affected_rows); "
    "NUNCA afirme que uma operação foi concluída sem tê-la executado, e NUNCA "
    "afirme falha se a tool retornou status ok."
)


def build_supervisor_prompt(
    base_prompt: str, memories: list[str], require_write_confirmation: bool
) -> str:
    prompt = base_prompt
    if memories:
        prompt += "\n\nO que você sabe sobre este usuário (memórias):\n" + "\n".join(
            f"- {m}" for m in memories
        )
    if require_write_confirmation:
        prompt += WRITE_CONFIRMATION_CLAUSE
    return prompt


async def _supervisor_node(state: RunState) -> dict:
    run_config = state["run_config"]
    supervisor = run_config["supervisor"]
    agents = {a["name"]: a for a in run_config.get("agents", [])}
    max_steps = int(run_config.get("max_steps", settings.max_steps_default))
    writer = get_stream_writer()

    set_run_context(
        secrets=run_config.get("secrets", {}),
        datasources=run_config.get("datasources", []),
        tenant_id=run_config.get("tenant_id"),
        chat_id=run_config.get("thread_id"),
        embedding=run_config.get("embedding") or None,
        agent_files={
            a["name"]: a.get("file_ids", []) for a in run_config.get("agents", [])
        },
        write_tables=run_config.get("write_tables", []),
        attachments=run_config.get("attachments", []),
        payment=run_config.get("payment") or {},
    )
    tool_defs = _agent_tool_defs(list(agents.values())) or None
    supervisor_config = ModelConfig(**supervisor["model"])
    system_prompt = build_supervisor_prompt(
        supervisor.get("prompt", ""),
        run_config.get("memories") or [],
        bool(run_config.get("require_write_confirmation"))
        and bool(run_config.get("write_tables")),
    )
    messages = _history_messages(state, system_prompt)

    async def emit(delta: str):
        writer({"type": "token", "text": delta})

    final_text = ""
    async with (
        open_catalog_session() as catalog_session,
        ExternalServers(run_config.get("mcp_servers", [])) as external,
    ):
        platform_tools = await _load_tool_defs(catalog_session, external)

        for _step in range(max_steps):
            result = await complete(supervisor_config, messages, emit, tools=tool_defs)
            await _record_usage(run_config, "supervisor", supervisor_config, result)
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
                    output = await _run_specialist(
                        agent, task, writer, run_config, platform_tools,
                        catalog_session, external,
                    )
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
            await _record_usage(run_config, "supervisor", supervisor_config, result)
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
            # `check` descarta conexão morta antes de entregá-la: o banco fica
            # atrás do Traefik da VPS, e um restart lá deixa conexões ociosas
            # em estado zumbi que só falham na hora do uso.
            check=AsyncConnectionPool.check_connection,
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
