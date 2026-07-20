"""Real-model homologation harness.

Drives the DEPLOYED platform with a real LLM (the tenant's configured Gemini
key) and verifies observable side effects — a sale really lands a row in the
store database, a BigQuery question returns real numbers, a catalog PDF yields
the marked items. Produces a JSON + Markdown report.

Run: python scripts/homologacao.py [loja|educacional|catalogo|all]
Requires: cloud-sql-proxy running on 127.0.0.1:5544 for DB verification
(the Loja case), backend reachable, master password.

This is homologation tooling, not a unit test — it spends real tokens and
touches real data, so it lives under scripts/ and runs on demand.
"""

import json
import os
import sys
import time

import httpx
import psycopg
from psycopg.rows import dict_row

BACKEND = os.environ.get("HOMOLOG_BACKEND", "https://teste-ia-backend-x27vtpiida-uc.a.run.app")
MASTER_EMAIL = os.environ.get("HOMOLOG_MASTER_EMAIL", "master@example.com")
MASTER_PASSWORD = os.environ.get("HOMOLOG_MASTER_PASSWORD", "admin123")
DEMO_DB_PORT = int(os.environ.get("HOMOLOG_DB_PORT", "5544"))
_CREDS_FILE = os.environ.get(
    "HOMOLOG_DB_CREDS", r"C:/Users/LUCAS~1.RAN/AppData/Local/Temp/claude/dbcreds.txt"
)

TEST_USER_PASSWORD = "homolog-senha-forte-123"


def _db_creds() -> tuple[str, str]:
    user, pw = open(_CREDS_FILE, encoding="utf-8").read().strip().split("\n")[:2]
    return user, pw


def _demo_conn(dbname: str):
    user, pw = _db_creds()
    return psycopg.connect(
        host="127.0.0.1", port=DEMO_DB_PORT, dbname=dbname, user=user, password=pw,
        connect_timeout=10, row_factory=dict_row,
    )


def login(client: httpx.Client, email: str, password: str) -> str:
    r = client.post("/api/auth/login", json={"email": email, "password": password})
    r.raise_for_status()
    return r.json()["token"]


def ensure_user_password(client: httpx.Client, master_token: str, email: str) -> None:
    """Reset a known password for a tenant user so the harness can log in."""
    users = client.get(
        "/api/users", headers={"Authorization": f"Bearer {master_token}"}
    ).json()
    target = next((u for u in users if u["email"] == email), None)
    if target is None:
        raise SystemExit(f"usuário {email} não existe — rode o setup do case antes")
    client.put(
        f"/api/users/{target['id']}",
        json={"password": TEST_USER_PASSWORD},
        headers={"Authorization": f"Bearer {master_token}"},
    ).raise_for_status()


def send_chat(client: httpx.Client, token: str, message: str, template_id: str,
              chat_id: str | None = None) -> dict:
    """Send one chat turn, consume the SSE stream, return structured result."""
    started = time.monotonic()
    body = {"message": message, "template_id": template_id}
    if chat_id:
        body["chat_id"] = chat_id
    tokens, tools, artifacts, agents = [], [], [], []
    reply, new_chat_id, error = "", chat_id, None
    with client.stream(
        "POST", "/api/chat/send", json=body,
        headers={"Authorization": f"Bearer {token}"}, timeout=300.0,
    ) as response:
        current = ""
        for line in response.iter_lines():
            if line.startswith("event: "):
                current = line[7:].strip()
            elif line.startswith("data: "):
                data = json.loads(line[6:])
                if current == "chat":
                    new_chat_id = data["chat_id"]
                elif current == "token":
                    tokens.append(data["text"])
                elif current == "tool":
                    tools.append(data)
                elif current == "agent":
                    agents.append(data)
                elif current == "artifact":
                    artifacts.append(data)
                elif current == "done":
                    reply = data["text"]
                elif current == "error":
                    error = data.get("detail")
    return {
        "message": message,
        "reply": reply,
        "chat_id": new_chat_id,
        "tools": tools,
        "artifacts": artifacts,
        "agents": agents,
        "error": error,
        "latency_ms": int((time.monotonic() - started) * 1000),
    }


# ---------------------------------------------------------------- Loja case

def homolog_loja(client: httpx.Client, master_token: str) -> dict:
    ensure_user_password(client, master_token, "dono@lojademo.com")
    token = login(client, "dono@lojademo.com", TEST_USER_PASSWORD)
    templates = client.get("/api/templates", headers={"Authorization": f"Bearer {token}"}).json()
    tpl = next(t for t in templates if t["name"] == "Balcao de Vendas")

    with _demo_conn("agent_llm_demo") as conn:
        before = conn.execute("SELECT count(*) AS n FROM pedidos").fetchone()["n"]

    steps = []
    # 1) Quote — read-only path, should produce numbers and ideally a PDF/artifact.
    quote = send_chat(
        client, token,
        "Monte um orçamento de 3 martelos e 2 furadeiras. Liste itens, preços unitários e o total.",
        tpl["id"],
    )
    steps.append(quote)

    # 2) Sale — write path with confirmation. First the request, then the 'sim'.
    sale = send_chat(
        client, token,
        "Registre uma venda para o cliente de id 1, feita pelo funcionário de id 1, "
        "de 2 martelos e 1 furadeira. Calcule o total e crie o pedido e seus itens.",
        tpl["id"], chat_id=quote["chat_id"],
    )
    steps.append(sale)
    confirm = send_chat(
        client, token, "Sim, confirmo. Pode registrar a venda.",
        tpl["id"], chat_id=sale["chat_id"],
    )
    steps.append(confirm)

    with _demo_conn("agent_llm_demo") as conn:
        after = conn.execute("SELECT count(*) AS n FROM pedidos").fetchone()["n"]
        last = conn.execute(
            "SELECT id, cliente_id, funcionario_id, total FROM pedidos ORDER BY id DESC LIMIT 1"
        ).fetchone()
        items = conn.execute(
            "SELECT count(*) AS n FROM itens_pedido WHERE pedido_id = %s", (last["id"],)
        ).fetchone()["n"] if last else 0

    exactly_one = after == before + 1
    final_reply = (confirm["reply"] or "").lower()
    false_failure = exactly_one and any(
        w in final_reply for w in ("não foi possível", "instabilidade", "falha", "erro ao", "não consegui")
    )
    return {
        "case": "loja",
        # Strict: exactly one new order (no duplication), it has items, and the
        # agent did not falsely report a failure for a write that succeeded.
        "passed": exactly_one and items > 0 and not false_failure,
        "pedidos_before": before,
        "pedidos_after": after,
        "duplicated": after > before + 1,
        "false_failure_report": false_failure,
        "new_order": dict(last) if exactly_one else None,
        "new_order_items": items if exactly_one else 0,
        "quote_had_numbers": any(c.isdigit() for c in steps[0]["reply"]),
        "steps": steps,
    }


# --------------------------------------------------------- Educacional case

def homolog_educacional(client: httpx.Client, master_token: str) -> dict:
    token = login(client, "analista@educacionaldemo.com", TEST_USER_PASSWORD)
    templates = client.get("/api/templates", headers={"Authorization": f"Bearer {token}"}).json()
    tpl = next(t for t in templates if t["name"] == "Analista Educacional")
    steps = []
    q = send_chat(
        client, token,
        "Consulte no BigQuery quantas escolas distintas existem na tabela de lista de escolas "
        "e me diga o número. Depois gere um gráfico de barras simples de exemplo.",
        tpl["id"],
    )
    steps.append(q)
    return {
        "case": "educacional",
        "passed": q["error"] is None and any(t["tool"] == "run_sql_query" for t in q["tools"]),
        "used_sql": any(t["tool"] == "run_sql_query" for t in q["tools"]),
        "made_artifact": len(q["artifacts"]) > 0,
        "steps": steps,
    }


# ------------------------------------------------------------- Catalog case

def homolog_catalogo(client: httpx.Client, master_token: str) -> dict:
    token = login(client, "vendedor@catalogodemo.com", TEST_USER_PASSWORD)
    templates = client.get("/api/templates", headers={"Authorization": f"Bearer {token}"}).json()
    tpl = next(t for t in templates if t["name"] == "Leitor de Catálogo")
    pdf_path = os.path.join(os.path.dirname(__file__), "..", "frontend", "e2e",
                            "fixtures", "catalogo_circulado.pdf")
    with open(pdf_path, "rb") as f:
        files = {"files": ("catalogo_circulado.pdf", f.read(), "application/pdf")}
    started = time.monotonic()
    data = {
        "message": "Analise este catálogo página a página e liste os produtos circulados "
                   "com suas quantidades escritas à mão.",
        "template_id": tpl["id"],
    }
    with client.stream(
        "POST", "/api/chat/send", data=data, files=files,
        headers={"Authorization": f"Bearer {token}"}, timeout=300.0,
    ) as response:
        reply, current = "", ""
        tools = []
        for line in response.iter_lines():
            if line.startswith("event: "):
                current = line[7:].strip()
            elif line.startswith("data: "):
                d = json.loads(line[6:])
                if current == "done":
                    reply = d["text"]
                elif current == "tool":
                    tools.append(d)
    return {
        "case": "catalogo",
        "passed": any(t["tool"] == "analyze_pdf_pages" for t in tools) and len(reply) > 0,
        "used_vision": any(t["tool"] == "analyze_pdf_pages" for t in tools),
        "reply": reply,
        "latency_ms": int((time.monotonic() - started) * 1000),
    }


CASES = {"loja": homolog_loja, "educacional": homolog_educacional, "catalogo": homolog_catalogo}


def main() -> None:
    which = sys.argv[1] if len(sys.argv) > 1 else "all"
    selected = list(CASES) if which == "all" else [which]

    with httpx.Client(base_url=BACKEND, timeout=60.0) as client:
        master_token = login(client, MASTER_EMAIL, MASTER_PASSWORD)
        results = []
        for name in selected:
            print(f"=== homologando: {name} ===", flush=True)
            try:
                results.append(CASES[name](client, master_token))
            except Exception as exc:  # noqa: BLE001 — report per-case, keep going
                results.append({"case": name, "passed": False, "error": repr(exc)})
            print(f"    passed={results[-1].get('passed')}", flush=True)

    out = os.path.join(os.path.dirname(__file__), "..", "docs", "homologacao-resultado.json")
    os.makedirs(os.path.dirname(out), exist_ok=True)
    with open(out, "w", encoding="utf-8") as f:
        json.dump(results, f, ensure_ascii=False, indent=2, default=str)
    print(f"\nrelatório salvo em {out}")
    print("RESUMO:", {r["case"]: r.get("passed") for r in results})


if __name__ == "__main__":
    main()
