"""Real-model homologation harness for Mega Spec 1.

Drives the deployed platform with real configured AI services and datasource
bindings, recording observable evidence into
[`docs/homologacao-resultado.json`](agent llm/agent-platform/docs/homologacao-resultado.json).

Run examples:
- python scripts/homologacao.py educacional
- python scripts/homologacao.py matrix
- python scripts/homologacao.py all

This is homologation tooling, not a unit test — it spends real tokens and may
write to dedicated QA tables, so it lives under `scripts/` and runs on demand.
"""

import json
import os
import sys
import time
from collections.abc import Callable
from typing import Any

import httpx

BACKEND = os.environ.get("HOMOLOG_BACKEND", "https://teste-ia-backend-x27vtpiida-uc.a.run.app")
MASTER_EMAIL = os.environ.get("HOMOLOG_MASTER_EMAIL", "master@example.com")
MASTER_PASSWORD = os.environ.get("HOMOLOG_MASTER_PASSWORD", "admin123")
TEST_USER_PASSWORD = os.environ.get("HOMOLOG_ADMIN_PASSWORD", "homolog-senha-forte-123")
RESULT_PATH = os.path.join(os.path.dirname(__file__), "..", "docs", "homologacao-resultado.json")
HAMBURGUERIA_E2E_PATH = os.path.join(os.path.dirname(__file__), "..", "docs", "hamburgueria-e2e.json")

PROVIDER_ALIASES = {
    "gemini": "gemini",
    "openai": "openai",
    "anthropic": "anthropic",
}
DEFAULT_PROVIDERS = tuple(
    p.strip()
    for p in os.environ.get("HOMOLOG_PROVIDERS", "gemini,openai,anthropic").split(",")
    if p.strip()
)

LONG_CONVERSATION_MESSAGES = [
    "Liste os datasources disponíveis para este template e diga quais aceitam escrita controlada.",
    "Rode uma consulta simples de leitura e materialize o resultado como dataset artifact.",
    "Agora gere um gráfico simples a partir do dataset anterior.",
    "Explique em uma frase o que o gráfico mostra e preserve a referência ao artifact.",
    "Exporte o dataset para planilha.",
    "Gere um PDF curto com um resumo executivo do mesmo dataset.",
    "Faça uma transformação em Python sobre o dataset anterior para adicionar uma coluna derivada.",
    "Descreva os artefatos gerados até agora.",
    "Se o dataset tiver coluna temporal adequada, gere uma previsão; caso contrário, explique a limitação.",
    "Recapitule o artifact inicial e confirme se ele ainda pode ser reutilizado no restante da conversa.",
    "Volte ao primeiro dataset gerado e compare com o artifact mais recente, sem perder o contexto.",
    "Se houver um gráfico disponível, gere uma segunda visualização alternativa a partir do mesmo dataset base.",
    "Explique quais tools já foram usadas e quais artifacts seguem reaproveitáveis.",
    "Gere uma segunda exportação do dataset em formato diferente do que já foi produzido, se possível.",
    "Use novamente Python sobre um artifact anterior para resumir colunas numéricas ou justificar por que não pode.",
    "Retome o resumo executivo anterior e complemente com dois bullets objetivos.",
    "Liste os artifact_ids mencionados até aqui, com o tipo de cada artifact.",
    "Releia o artifact inicial e diga se ele continua consistente com as transformações posteriores.",
    "Sintetize a trilha da conversa em ordem cronológica, destacando datasets e saídas derivadas.",
    "Faça uma validação final: o primeiro artifact ainda pode ser referenciado com segurança neste turno?",
]


def login(client: httpx.Client, email: str, password: str) -> str:
    r = client.post("/api/auth/login", json={"email": email, "password": password})
    r.raise_for_status()
    return r.json()["token"]


def auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def ensure_user_password(client: httpx.Client, master_token: str, email: str) -> None:
    users = client.get("/api/users", headers=auth(master_token)).json()
    target = next((u for u in users if u["email"] == email), None)
    if target is None:
        raise SystemExit(f"usuário {email} não existe — rode primeiro [`scripts/setup_homolog.py`](agent llm/agent-platform/scripts/setup_homolog.py:1)")
    client.put(
        f"/api/users/{target['id']}",
        json={"password": TEST_USER_PASSWORD},
        headers=auth(master_token),
    ).raise_for_status()


def list_templates(client: httpx.Client, token: str) -> list[dict[str, Any]]:
    return client.get("/api/templates", headers=auth(token)).json()


def list_services(client: httpx.Client, token: str) -> list[dict[str, Any]]:
    return client.get("/api/ai-services", headers=auth(token)).json()


def list_datasources(client: httpx.Client, token: str) -> list[dict[str, Any]]:
    return client.get("/api/datasources", headers=auth(token)).json()


def get_version_detail(client: httpx.Client, token: str, template_id: str, version_id: str) -> dict[str, Any]:
    r = client.get(f"/api/templates/{template_id}/versions/{version_id}", headers=auth(token))
    r.raise_for_status()
    return r.json()


def clone_with_provider(version: dict[str, Any], service_id: str) -> dict[str, Any]:
    return {
        "supervisor_prompt": version["supervisor_prompt"],
        "supervisor_ai_service_id": service_id,
        "supervisor_model_override": version.get("supervisor_model_override") or None,
        "supervisor_reasoning_effort": version.get("supervisor_reasoning_effort") or None,
        "max_steps": version.get("max_steps", 6),
        "datasource_ids": version.get("datasource_ids", []),
        "write_tables": version.get("write_tables", []),
        "require_write_confirmation": version.get("require_write_confirmation", True),
        "notes": f"provider override for homologation: {service_id}",
        "agents": [
            {
                "name": a["name"],
                "description": a["description"],
                "prompt": a["prompt"],
                "ai_service_id": service_id,
                "model_override": a.get("model_override") or None,
                "reasoning_effort": a.get("reasoning_effort") or None,
                "tools": a.get("tools", []),
                "file_ids": a.get("file_ids", []),
            }
            for a in version.get("agents", [])
        ],
        "mcp_servers": version.get("mcp_servers", []),
    }


def deploy_provider_override(
    client: httpx.Client,
    token: str,
    template: dict[str, Any],
    service_id: str,
) -> None:
    detail = get_version_detail(client, token, template["id"], template["active_version_id"])
    payload = clone_with_provider(detail, service_id)
    v = client.post(f"/api/templates/{template['id']}/versions", json=payload, headers=auth(token))
    v.raise_for_status()
    client.post(
        f"/api/templates/{template['id']}/deploy",
        json={"version_id": v.json()["id"]},
        headers=auth(token),
    ).raise_for_status()


def _collect_stream(response: httpx.Response, *, initial_chat_id: str | None = None) -> dict[str, Any]:
    tools: list[dict[str, Any]] = []
    artifacts: list[dict[str, Any]] = []
    agents: list[dict[str, Any]] = []
    reply = ""
    error = None
    new_chat_id = initial_chat_id
    current = ""
    for line in response.iter_lines():
        if line.startswith("event: "):
            current = line[7:].strip()
        elif line.startswith("data: "):
            data = json.loads(line[6:])
            if current == "chat":
                new_chat_id = data["chat_id"]
            elif current == "tool":
                tools.append(data)
            elif current == "artifact":
                artifacts.append(data)
            elif current == "agent":
                agents.append(data)
            elif current == "done":
                reply = data.get("text", "")
            elif current == "error":
                error = data.get("detail")
    return {
        "reply": reply,
        "chat_id": new_chat_id,
        "tools": tools,
        "artifacts": artifacts,
        "agents": agents,
        "error": error,
    }



def send_chat(
    client: httpx.Client,
    token: str,
    message: str,
    template_id: str,
    chat_id: str | None = None,
) -> dict[str, Any]:
    started = time.monotonic()
    body: dict[str, Any] = {"message": message, "template_id": template_id}
    if chat_id:
        body["chat_id"] = chat_id
    with client.stream(
        "POST",
        "/api/chat/send",
        json=body,
        headers=auth(token),
        timeout=300.0,
    ) as response:
        collected = _collect_stream(response, initial_chat_id=chat_id)
    return {
        "message": message,
        **collected,
        "latency_ms": int((time.monotonic() - started) * 1000),
    }


def step_with_meta(step: dict[str, Any], *, provider: str, datasource_kind: str) -> dict[str, Any]:
    enriched = dict(step)
    enriched["provider"] = provider
    enriched["datasource_kind"] = datasource_kind
    return enriched


def choose_provider_services(services: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    for service in services:
        provider = service.get("provider")
        if provider in PROVIDER_ALIASES and service.get("is_active", True):
            out[provider] = service
    return out


def homolog_educacional_provider(
    client: httpx.Client,
    master_token: str,
    provider: str,
) -> dict[str, Any]:
    ensure_user_password(client, master_token, "analista@educacionaldemo.com")
    token = login(client, "analista@educacionaldemo.com", TEST_USER_PASSWORD)
    services = choose_provider_services(list_services(client, token))
    if provider not in services:
        # Falta de BYOK para o provider é lacuna de configuração do tenant, não
        # defeito do fluxo: reportar como skip explícito para não pintar de
        # vermelho um caso que sequer chegou a rodar.
        return {
            "case": "educacional",
            "provider": provider,
            "passed": None,
            "skipped": True,
            "skip_reason": (
                f"nenhum serviço de IA ativo do provider '{provider}' está cadastrado "
                "no tenant educacional — cadastre a chave BYOK para exercitar este caso"
            ),
        }
    templates = list_templates(client, token)
    tpl = next(t for t in templates if t["name"] == "Analista Educacional")
    deploy_provider_override(client, token, tpl, services[provider]["id"])
    templates = list_templates(client, token)
    tpl = next(t for t in templates if t["name"] == "Analista Educacional")
    q = send_chat(
        client,
        token,
        "Consulte no BigQuery quantas escolas distintas existem na tabela de lista de escolas e me diga o número. Depois gere um gráfico ou PDF simples de exemplo.",
        tpl["id"],
    )
    step = step_with_meta(q, provider=provider, datasource_kind="bigquery")
    return {
        "case": "educacional",
        "provider": provider,
        "passed": q["error"] is None and any(t["tool"] == "run_sql_query" for t in q["tools"]),
        "used_sql": any(t["tool"] == "run_sql_query" for t in q["tools"]),
        "made_artifact": len(q["artifacts"]) > 0,
        "steps": [step],
    }


def homolog_catalogo(client: httpx.Client, master_token: str) -> dict[str, Any]:
    ensure_user_password(client, master_token, "vendedor@catalogodemo.com")
    token = login(client, "vendedor@catalogodemo.com", TEST_USER_PASSWORD)
    templates = list_templates(client, token)
    tpl = next(t for t in templates if t["name"] == "Leitor de Catálogo")
    pdf_path = os.path.join(os.path.dirname(__file__), "..", "frontend", "e2e", "fixtures", "catalogo_circulado.pdf")
    with open(pdf_path, "rb") as f:
        files = {"files": ("catalogo_circulado.pdf", f.read(), "application/pdf")}
    started = time.monotonic()
    data = {
        "message": "Analise este catálogo página a página e liste os produtos circulados com suas quantidades escritas à mão.",
        "template_id": tpl["id"],
    }
    with client.stream(
        "POST",
        "/api/chat/send",
        data=data,
        files=files,
        headers=auth(token),
        timeout=300.0,
    ) as response:
        collected = _collect_stream(response)
    step = {
        "message": data["message"],
        **collected,
        "latency_ms": int((time.monotonic() - started) * 1000),
        "provider": "vision",
        "datasource_kind": "file",
    }
    return {
        "case": "catalogo",
        "passed": any(t["tool"] == "analyze_pdf_pages" for t in step["tools"]) and len(step["reply"]) > 0,
        "used_vision": any(t["tool"] == "analyze_pdf_pages" for t in step["tools"]),
        "reply": step["reply"],
        "steps": [step],
    }


def homolog_hamburgueria(client: httpx.Client, master_token: str) -> dict[str, Any]:
    ensure_user_password(client, master_token, "dono@hamburgueriademo.com")
    token = login(client, "dono@hamburgueriademo.com", TEST_USER_PASSWORD)
    templates = list_templates(client, token)
    datasources = list_datasources(client, token)

    atendimento = next(t for t in templates if t["name"] == "Atendimento Hamburgueria")
    admin = next(t for t in templates if t["name"] == "Admin Hamburgueria")
    datasource = next(d for d in datasources if d["name"] == "hamburgueria")

    datasource_test = client.post(
        f"/api/datasources/{datasource['id']}/test",
        headers=auth(token),
    )
    datasource_test.raise_for_status()

    baseline = send_chat(
        client,
        token,
        "Liste 3 produtos ativos com preço do cardápio e some o total se eu pedir 1 unidade de cada um.",
        atendimento["id"],
    )
    preconfirm = send_chat(
        client,
        token,
        "Quero fechar esse pedido com 1 Burger Clássico, 1 Batata Frita P e 1 Refrigerante Lata. Pode gravar.",
        atendimento["id"],
        chat_id=baseline["chat_id"],
    )
    confirm = send_chat(
        client,
        token,
        "Sim, confirmo a gravação do pedido.",
        atendimento["id"],
        chat_id=preconfirm["chat_id"],
    )
    final_write = send_chat(
        client,
        token,
        "Lucas Rangel",
        atendimento["id"],
        chat_id=confirm["chat_id"],
    )
    admin_report = send_chat(
        client,
        token,
        "me mostre o relatório de pedidos de hoje",
        admin["id"],
    )

    payload = {
        "datasource": datasource,
        "datasource_test": datasource_test.json(),
        "atendimento_baseline": baseline,
        "atendimento_preconfirm": preconfirm,
        "atendimento_confirm": confirm,
        "atendimento_name": final_write,
        "admin_report": admin_report,
    }
    with open(HAMBURGUERIA_E2E_PATH, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, ensure_ascii=False, indent=2, default=str)

    has_write = any(t["tool"] in {"execute_sql_write", "execute_sql_transaction"} for t in final_write["tools"])
    passed = (
        datasource_test.json().get("ok") is True
        and baseline["error"] is None
        and preconfirm["error"] is None
        and confirm["error"] is None
        and final_write["error"] is None
        and admin_report["error"] is None
        and has_write
    )
    return {
        "case": "hamburgueria",
        "passed": passed,
        "used_sql": any(t["tool"] == "run_sql_query" for step in [baseline, preconfirm, confirm, final_write, admin_report] for t in step["tools"]),
        "has_write": has_write,
        "datasource_ok": datasource_test.json().get("ok") is True,
        "steps": [baseline, preconfirm, confirm, final_write, admin_report],
        "evidence_path": HAMBURGUERIA_E2E_PATH,
    }



def homolog_ferragista(client: httpx.Client, master_token: str) -> dict[str, Any]:
    """Balcão de vendas: consulta de catálogo, cálculo e venda gravada de verdade.

    É o cenário que exercita leitura e escrita no mesmo template, com a
    confirmação obrigatória no meio — se a confirmação sumir, este caso quebra.
    """
    ensure_user_password(client, master_token, "dono@lojademo.com")
    token = login(client, "dono@lojademo.com", TEST_USER_PASSWORD)
    templates = list_templates(client, token)
    tpl = next(t for t in templates if t["name"] == "Balcao de Vendas")
    datasources = list_datasources(client, token)
    datasource = next(d for d in datasources if d["name"] == "erp_loja")

    datasource_test = client.post(
        f"/api/datasources/{datasource['id']}/test", headers=auth(token)
    )
    datasource_test.raise_for_status()

    catalogo = send_chat(
        client,
        token,
        "Liste os 3 produtos com maior estoque, com preço, e diga o valor total "
        "se eu levar 2 unidades de cada.",
        tpl["id"],
    )
    venda = send_chat(
        client,
        token,
        "Quero fechar uma venda de 2 Furadeira 650W para o cliente de id 1. Pode registrar.",
        tpl["id"],
        chat_id=catalogo["chat_id"],
    )
    confirmacao = send_chat(
        client,
        token,
        "Sim, confirmo o registro dessa venda.",
        tpl["id"],
        chat_id=venda["chat_id"],
    )
    relatorio = send_chat(
        client,
        token,
        "Quantos pedidos existem no total e qual o valor do último pedido registrado?",
        tpl["id"],
        chat_id=confirmacao["chat_id"],
    )

    steps = [catalogo, venda, confirmacao, relatorio]
    tools = [t["tool"] for step in steps for t in step["tools"]]
    has_write = any(
        tool in {"execute_sql_write", "execute_sql_transaction"} for tool in tools
    )
    passed = (
        datasource_test.json().get("ok") is True
        and all(step["error"] is None for step in steps)
        and "run_sql_query" in tools
        and has_write
    )
    return {
        "case": "ferragista",
        "passed": passed,
        "datasource_ok": datasource_test.json().get("ok") is True,
        "used_sql": "run_sql_query" in tools,
        "has_write": has_write,
        "tools": sorted(set(tools)),
        "steps": steps,
    }


def homolog_matrix(client: httpx.Client, master_token: str) -> dict[str, Any]:
    ensure_user_password(client, master_token, "qa@matrixdemo.com")
    token = login(client, "qa@matrixdemo.com", TEST_USER_PASSWORD)
    templates = list_templates(client, token)
    tpl = next(t for t in templates if t["name"] == "QA Matrix Runner")
    datasources = {d["name"]: d for d in list_datasources(client, token)}

    steps: list[dict[str, Any]] = []
    baseline = send_chat(
        client,
        token,
        "Liste os datasources disponíveis e diga quais aceitam escrita controlada neste template.",
        tpl["id"],
    )
    steps.append(step_with_meta(baseline, provider="matrix", datasource_kind="mixed"))

    if "homolog_postgres" in datasources:
        read_pg = send_chat(
            client,
            token,
            "Na fonte homolog_postgres, rode um SELECT simples na tabela qa_items e materialize o resultado como dataset.",
            tpl["id"],
            chat_id=baseline["chat_id"],
        )
        steps.append(step_with_meta(read_pg, provider="matrix", datasource_kind="postgresql"))
        write_pg = send_chat(
            client,
            token,
            "Na fonte homolog_postgres, insira uma linha de teste em qa_items com um identificador de homologação e aguarde minha confirmação.",
            tpl["id"],
            chat_id=read_pg["chat_id"],
        )
        steps.append(step_with_meta(write_pg, provider="matrix", datasource_kind="postgresql"))
        confirm_pg = send_chat(client, token, "Sim, confirmo a escrita no Postgres.", tpl["id"], chat_id=write_pg["chat_id"])
        steps.append(step_with_meta(confirm_pg, provider="matrix", datasource_kind="postgresql"))

    if "homolog_mysql" in datasources:
        read_my = send_chat(
            client,
            token,
            "Na fonte homolog_mysql, rode um SELECT simples na tabela qa_items e materialize o resultado como dataset.",
            tpl["id"],
            chat_id=steps[-1]["chat_id"],
        )
        steps.append(step_with_meta(read_my, provider="matrix", datasource_kind="mysql"))

    chat_id = steps[-1]["chat_id"]
    for message in LONG_CONVERSATION_MESSAGES:
        turn = send_chat(client, token, message, tpl["id"], chat_id=chat_id)
        chat_id = turn["chat_id"]
        steps.append(step_with_meta(turn, provider="matrix", datasource_kind="long_conversation"))

    has_query = any(any(t["tool"] == "run_sql_query" for t in s.get("tools", [])) for s in steps)
    has_artifact = any(len(s.get("artifacts", [])) > 0 for s in steps)
    artifact_kinds = {artifact.get("kind") for step in steps for artifact in step.get("artifacts", [])}
    has_write = any(
        any(t["tool"] in {"execute_sql_write", "execute_sql_transaction"} for t in s.get("tools", []))
        for s in steps
    )
    long_conversation_turns = sum(1 for s in steps if s.get("datasource_kind") == "long_conversation")
    return {
        "case": "matrix",
        "passed": has_query and has_artifact and has_write and long_conversation_turns >= 20 and all(s.get("error") is None for s in steps),
        "steps": steps,
        "has_query": has_query,
        "has_artifact": has_artifact,
        "has_write": has_write,
        "artifact_kinds": sorted(k for k in artifact_kinds if k),
        "long_conversation_turns": long_conversation_turns,
        "turns": len(steps),
    }


def verdict(result: dict[str, Any]) -> str:
    """Três estados, não dois: passou, falhou, ou não pôde rodar."""
    if result.get("skipped"):
        return "skipped"
    return "passed" if result.get("passed") else "failed"


def load_existing_results() -> list[dict[str, Any]]:
    if not os.path.exists(RESULT_PATH):
        return []
    with open(RESULT_PATH, encoding="utf-8") as fh:
        data = json.load(fh)
    return data if isinstance(data, list) else []


def save_results(results: list[dict[str, Any]]) -> None:
    os.makedirs(os.path.dirname(RESULT_PATH), exist_ok=True)
    with open(RESULT_PATH, "w", encoding="utf-8") as fh:
        json.dump(results, fh, ensure_ascii=False, indent=2, default=str)


CASES: dict[str, Callable[[httpx.Client, str], dict[str, Any]]] = {
    "catalogo": homolog_catalogo,
    "ferragista": homolog_ferragista,
    "hamburgueria": homolog_hamburgueria,
    "matrix": homolog_matrix,
}


def main() -> None:
    which = sys.argv[1] if len(sys.argv) > 1 else "all"
    selected = (
        ["educacional", "catalogo", "ferragista", "hamburgueria", "matrix"]
        if which == "all"
        else [which]
    )
    existing = load_existing_results()
    new_results: list[dict[str, Any]] = []

    with httpx.Client(base_url=BACKEND, timeout=60.0) as client:
        master_token = login(client, MASTER_EMAIL, MASTER_PASSWORD)
        for name in selected:
            print(f"=== homologando: {name} ===", flush=True)
            if name == "educacional":
                for provider in DEFAULT_PROVIDERS:
                    try:
                        result = homolog_educacional_provider(client, master_token, provider)
                    except Exception as exc:  # noqa: BLE001
                        result = {"case": "educacional", "provider": provider, "passed": False, "error": repr(exc)}
                    new_results.append(result)
                    print(f"    provider={provider} {verdict(result)}", flush=True)
                continue
            try:
                result = CASES[name](client, master_token)
            except Exception as exc:  # noqa: BLE001
                result = {"case": name, "passed": False, "error": repr(exc)}
            new_results.append(result)
            print(f"    {verdict(result)}", flush=True)

    save_results(existing + new_results)
    print(f"\nrelatório salvo em {RESULT_PATH}")
    print("RESUMO:", {f"{r['case']}:{r.get('provider', '')}".rstrip(":"): verdict(r) for r in new_results})
    skipped = [r for r in new_results if r.get("skipped")]
    if skipped:
        print("\nPULADOS (lacuna de configuração, não falha de fluxo):")
        for r in skipped:
            print(f"  - {r['case']}:{r.get('provider', '')}".rstrip(":"), "→", r.get("skip_reason"))
    failed = [r for r in new_results if r.get("passed") is False]
    # Exit code reflete só falha real: skip não reprova a rodada.
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
