"""Idempotent setup of homologation tenants/templates on the deployed platform.

Covers the baseline demo tenants plus the expanded QA matrix for Mega Spec 1:
- AI services for Gemini, OpenAI and Anthropic
- Datasources for BigQuery, PostgreSQL and MySQL
- A long-conversation QA template with artifact chaining

Safe to re-run: existing tenants/services/datasources/templates are reused.
"""

import json
import os
from typing import Any

import httpx

BACKEND = os.environ.get("HOMOLOG_BACKEND", "https://teste-ia-backend-x27vtpiida-uc.a.run.app").strip()
MASTER = (
    os.environ.get("HOMOLOG_MASTER_EMAIL", "master@example.com").strip(),
    os.environ.get("HOMOLOG_MASTER_PASSWORD", "admin123").strip(),
)
ADMIN_PW = os.environ.get("HOMOLOG_ADMIN_PASSWORD", "homolog-senha-forte-123").strip()

GEMINI_KEY_FILE = os.environ.get(
    "GEMINI_KEY_FILE", r"C:/Users/lucas.rangel/Desktop/agent llm/gemini_api_key.txt"
).strip()
OPENAI_KEY_FILE = os.environ.get("OPENAI_KEY_FILE", "").strip()
ANTHROPIC_KEY_FILE = os.environ.get("ANTHROPIC_KEY_FILE", "").strip()

POSTGRES_HOST = os.environ.get("HOMOLOG_PG_HOST", "127.0.0.1").strip()
POSTGRES_PORT = int(os.environ.get("HOMOLOG_PG_PORT", "5432").strip())
POSTGRES_DB = os.environ.get("HOMOLOG_PG_DATABASE", "agent_homolog").strip()
POSTGRES_USER = os.environ.get("HOMOLOG_PG_USER", "agent").strip()
POSTGRES_PASSWORD = os.environ.get("HOMOLOG_PG_PASSWORD", "").strip()
POSTGRES_SCHEMA = os.environ.get("HOMOLOG_PG_SCHEMA", "public").strip()

MYSQL_HOST = os.environ.get("HOMOLOG_MYSQL_HOST", "127.0.0.1").strip()
MYSQL_PORT = int(os.environ.get("HOMOLOG_MYSQL_PORT", "3306").strip())
MYSQL_DB = os.environ.get("HOMOLOG_MYSQL_DATABASE", "agent_homolog").strip()
MYSQL_USER = os.environ.get("HOMOLOG_MYSQL_USER", "agent").strip()
MYSQL_PASSWORD = os.environ.get("HOMOLOG_MYSQL_PASSWORD", "").strip()

BILLING_PROJECT = os.environ.get("HOMOLOG_BQ_PROJECT", "eduk-prd-lake")
BILLING_DATASET = os.environ.get("HOMOLOG_BQ_DATASET", "")

DEFAULT_PROVIDER = os.environ.get("HOMOLOG_DEFAULT_PROVIDER", "gemini")


EDU_TABLES = (
    "Tabelas disponíveis no BigQuery (sempre use nomes totalmente qualificados "
    "entre crases, ex.: `mi-prd-lake.semantic_zone.d_lista_escola`):\n"
    "- `mi-prd-lake.semantic_zone.d_lista_escola` (escolas): IdEscola, NomeEscola, "
    "Cidade, Estado, Instituicao, UltimoAnoAtivacao.\n"
    "- `mi-prd-lake.semantic_zone.d_licoes` (cursos): CourseID, CourseName, "
    "CourseType_Identifier, IsDeleted.\n"
    "NUNCA use describe_datasources para BigQuery quando a pergunta já explicitar "
    "essas tabelas. Escreva o SQL direto com os nomes acima."
)


def _read_file(path: str) -> str:
    if not path:
        return ""
    with open(path, encoding="utf-8") as fh:
        return fh.read().strip()


def provider_keys() -> dict[str, str]:
    return {
        "gemini": _read_file(GEMINI_KEY_FILE),
        "openai": _read_file(OPENAI_KEY_FILE),
        "anthropic": _read_file(ANTHROPIC_KEY_FILE),
    }


def h(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def ensure_tenant(c: httpx.Client, mt: str, name: str, key: str, admin_email: str) -> str:
    tenants = c.get("/api/tenants", headers=h(mt)).json()
    found = next((t for t in tenants if t["tenant_key"] == key), None)
    if found:
        return found["id"]
    r = c.post(
        "/api/tenants",
        json={
            "name": name,
            "tenant_key": key,
            "admin_name": name + " Admin",
            "admin_email": admin_email,
            "admin_password": ADMIN_PW,
        },
        headers=h(mt),
    )
    r.raise_for_status()
    return r.json()["id"]


def admin_token(c: httpx.Client, email: str) -> str:
    return c.post("/api/auth/login", json={"email": email, "password": ADMIN_PW}).json()["token"]


def ensure_ai_service(
    c: httpx.Client,
    tok: str,
    *,
    name: str,
    provider: str,
    model: str,
    api_key: str,
    api_base: str | None = None,
) -> str:
    svcs = c.get("/api/ai-services", headers=h(tok)).json()
    found = next((s for s in svcs if s["name"] == name), None)
    payload = {
        "name": name,
        "provider": provider,
        "model": model,
        "api_key": api_key,
    }
    if api_base:
        payload["api_base"] = api_base
    if found:
        c.put(f"/api/ai-services/{found['id']}", json=payload, headers=h(tok)).raise_for_status()
        return found["id"]
    r = c.post("/api/ai-services", json=payload, headers=h(tok))
    r.raise_for_status()
    return r.json()["id"]


def ensure_provider_services(c: httpx.Client, tok: str) -> dict[str, str]:
    keys = provider_keys()
    models = {
        "gemini": os.environ.get("HOMOLOG_GEMINI_MODEL", "gemini-flash-latest"),
        "openai": os.environ.get("HOMOLOG_OPENAI_MODEL", "gpt-4o-mini"),
        "anthropic": os.environ.get("HOMOLOG_ANTHROPIC_MODEL", "claude-3-5-sonnet-latest"),
    }
    ids: dict[str, str] = {}
    for provider in ("gemini", "openai", "anthropic"):
        key = keys.get(provider, "")
        if not key:
            continue
        ids[provider] = ensure_ai_service(
            c,
            tok,
            name=provider,
            provider=provider,
            model=models[provider],
            api_key=key,
        )
    return ids


def ensure_datasource(
    c: httpx.Client,
    tok: str,
    *,
    name: str,
    kind: str,
    config: dict[str, Any],
    secret: str | None = None,
) -> str:
    datasources = c.get("/api/datasources", headers=h(tok)).json()
    found = next((d for d in datasources if d["name"] == name), None)
    payload = {"name": name, "kind": kind, "config": config}
    if secret:
        payload["secret"] = secret
    if found:
        c.put(f"/api/datasources/{found['id']}", json=payload, headers=h(tok)).raise_for_status()
        return found["id"]
    r = c.post("/api/datasources", json=payload, headers=h(tok))
    r.raise_for_status()
    return r.json()["id"]


def ensure_test_datasources(c: httpx.Client, tok: str) -> dict[str, str]:
    out: dict[str, str] = {}
    out["bigquery"] = ensure_datasource(
        c,
        tok,
        name="lake_educacional",
        kind="bigquery",
        config={k: v for k, v in {"project": BILLING_PROJECT, "dataset": BILLING_DATASET}.items() if v},
    )
    if POSTGRES_PASSWORD:
        out["postgresql"] = ensure_datasource(
            c,
            tok,
            name="homolog_postgres",
            kind="postgresql",
            config={
                "host": POSTGRES_HOST,
                "port": POSTGRES_PORT,
                "database": POSTGRES_DB,
                "user": POSTGRES_USER,
                "schema": POSTGRES_SCHEMA,
            },
            secret=POSTGRES_PASSWORD,
        )
    if MYSQL_PASSWORD:
        out["mysql"] = ensure_datasource(
            c,
            tok,
            name="homolog_mysql",
            kind="mysql",
            config={
                "host": MYSQL_HOST,
                "port": MYSQL_PORT,
                "database": MYSQL_DB,
                "user": MYSQL_USER,
            },
            secret=MYSQL_PASSWORD,
        )
    return out


def ensure_template(c: httpx.Client, tok: str, name: str, payload: dict[str, Any]) -> str:
    tpls = c.get("/api/templates", headers=h(tok)).json()
    existing = next((t for t in tpls if t["name"] == name), None)
    if existing:
        tpl_id = existing["id"]
    else:
        tpl_id = c.post(
            "/api/templates",
            json={"name": name, "description": payload["_desc"]},
            headers=h(tok),
        ).json()["id"]
    version = {k: v for k, v in payload.items() if not k.startswith("_")}
    v = c.post(f"/api/templates/{tpl_id}/versions", json=version, headers=h(tok))
    v.raise_for_status()
    c.post(
        f"/api/templates/{tpl_id}/deploy",
        json={"version_id": v.json()["id"]},
        headers=h(tok),
    ).raise_for_status()
    return tpl_id


def _service_for(services: dict[str, str], provider: str) -> str:
    service_id = services.get(provider) or next(iter(services.values()), None)
    if not service_id:
        raise RuntimeError("Nenhum AI service disponível para os templates de homologação")
    return service_id


def educacional(c: httpx.Client, mt: str) -> None:
    ensure_tenant(c, mt, "Educacional Demo", "educacional-demo", "analista@educacionaldemo.com")
    tok = admin_token(c, "analista@educacionaldemo.com")
    services = ensure_provider_services(c, tok)
    datasources = ensure_test_datasources(c, tok)
    svc = _service_for(services, DEFAULT_PROVIDER)
    ds = datasources["bigquery"]
    ensure_template(
        c,
        tok,
        "Analista Educacional",
        {
            "_desc": "Análise de dados educacionais da MindLab no BigQuery",
            "supervisor_prompt": "Você coordena analistas de dados educacionais da MindLab. "
            "Roteie perguntas de consulta para o consultor, de visualização para o analista "
            "visual e de projeção para o previsor. Responda em português, de forma objetiva.",
            "supervisor_ai_service_id": svc,
            "max_steps": 8,
            "datasource_ids": [ds],
            "agents": [
                {
                    "name": "consultor_dados",
                    "description": "consultas SQL a dados educacionais (escolas, cursos)",
                    "prompt": "Você consulta o BigQuery educacional da MindLab. "
                    + EDU_TABLES
                    + " Use a fonte 'lake_educacional' na tool run_sql_query. Materialize "
                    "resultados como dataset para gráficos.",
                    "ai_service_id": svc,
                    "tools": ["run_sql_query", "calculate"],
                },
                {
                    "name": "analista_visual",
                    "description": "gera gráficos, planilhas e PDFs a partir de datasets",
                    "prompt": "Você cria gráficos (generate_chart), planilhas (export_xlsx) e "
                    "PDFs (generate_pdf) a partir de dataset artifacts já materializados.",
                    "ai_service_id": svc,
                    "tools": ["generate_chart", "export_xlsx", "generate_pdf"],
                },
                {
                    "name": "previsor",
                    "description": "projeções temporais e cálculos em Python",
                    "prompt": "Você gera previsões (generate_forecast) e roda análises em "
                    "Python (execute_python) sobre datasets.",
                    "ai_service_id": svc,
                    "tools": ["generate_forecast", "execute_python"],
                },
            ],
        },
    )
    print("educacional: pronto")


def catalogo(c: httpx.Client, mt: str) -> None:
    ensure_tenant(c, mt, "Catálogo Demo", "catalogo-demo", "vendedor@catalogodemo.com")
    tok = admin_token(c, "vendedor@catalogodemo.com")
    services = ensure_provider_services(c, tok)
    svc = _service_for(services, DEFAULT_PROVIDER)
    ensure_template(
        c,
        tok,
        "Leitor de Catálogo",
        {
            "_desc": "Extrai produtos circulados e quantidades de catálogos em PDF",
            "supervisor_prompt": "Você lê catálogos de produtos em PDF enviados pelo vendedor. "
            "Use o leitor para analisar cada página e devolva a lista de produtos que estão "
            "circulados/marcados com a quantidade escrita à mão ao lado. Responda em uma "
            "lista clara: produto — quantidade.",
            "supervisor_ai_service_id": svc,
            "max_steps": 6,
            "agents": [
                {
                    "name": "leitor_agent",
                    "description": "analisa páginas de PDF com visão para achar itens marcados",
                    "prompt": "Você recebe um PDF de catálogo. Use analyze_pdf_pages com a "
                    "instrução: 'Liste os produtos que estão circulados ou marcados à mão e a "
                    "quantidade manuscrita ao lado de cada um; ignore produtos não marcados.' "
                    "Depois consolide o resultado das páginas em uma lista final.",
                    "ai_service_id": svc,
                    "tools": ["analyze_pdf_pages"],
                }
            ],
        },
    )
    print("catalogo: pronto")


def qa_matrix(c: httpx.Client, mt: str) -> None:
    ensure_tenant(c, mt, "QA Matrix Demo", "qa-matrix-demo", "qa@matrixdemo.com")
    tok = admin_token(c, "qa@matrixdemo.com")
    services = ensure_provider_services(c, tok)
    datasources = ensure_test_datasources(c, tok)
    svc = _service_for(services, DEFAULT_PROVIDER)
    ds_ids = [datasources[k] for k in ("bigquery", "postgresql", "mysql") if k in datasources]
    ensure_template(
        c,
        tok,
        "QA Matrix Runner",
        {
            "_desc": "Template de homologação profunda multi-LLM/multi-datasource",
            "supervisor_prompt": "Você coordena uma bateria de homologação técnica. "
            "Sempre escolha a tool adequada, cite limitações explicitamente e preserve referências "
            "a artifacts gerados anteriormente na conversa.",
            "supervisor_ai_service_id": svc,
            "max_steps": 10,
            "datasource_ids": ds_ids,
            "write_tables": [
                "qa_items",
                "public.qa_items",
                "qa_tx_items",
                "public.qa_tx_items",
            ],
            "require_write_confirmation": True,
            "agents": [
                {
                    "name": "qa_dados",
                    "description": "lê e escreve nos bancos de homologação",
                    "prompt": "Você faz homologação multi-datasource. Use describe_datasources para orientar, "
                    "run_sql_query para leitura e execute_sql_write / execute_sql_transaction apenas nas tabelas "
                    "qa_items e qa_tx_items quando solicitado explicitamente.",
                    "ai_service_id": svc,
                    "tools": [
                        "describe_datasources",
                        "run_sql_query",
                        "execute_sql_write",
                        "execute_sql_transaction",
                    ],
                },
                {
                    "name": "qa_artifacts",
                    "description": "gera e reutiliza artifacts em conversas longas",
                    "prompt": "Você recebe dataset artifacts previamente materializados e pode gerar gráficos, "
                    "planilhas, PDFs e análises em Python, sempre reutilizando artifact_id quando possível.",
                    "ai_service_id": svc,
                    "tools": ["generate_chart", "export_xlsx", "generate_pdf", "execute_python", "generate_forecast"],
                },
            ],
        },
    )
    print("qa-matrix: pronto")


def main() -> None:
    with httpx.Client(base_url=BACKEND, timeout=60.0) as c:
        mt = c.post("/api/auth/login", json={"email": MASTER[0], "password": MASTER[1]}).json()["token"]
        educacional(c, mt)
        catalogo(c, mt)
        qa_matrix(c, mt)
        print(json.dumps({"status": "ok", "backend": BACKEND}, ensure_ascii=False))


if __name__ == "__main__":
    main()
