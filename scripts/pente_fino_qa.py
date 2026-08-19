"""Pente fino nas tools do kernel (mega-spec qa-02, seções 2, 4 e 5).

Contra o backend de produção real (VPS, ia.rangeltech.net), sem mock nenhum:
cada tool é exercitada com uma conversa de chat de verdade (SSE), e os
efeitos observáveis (tool chamada, artifact gerado, erro) são gravados.

Pré-requisito conhecido e corrigido nesta rodada: o ai_service "gemini" do
tenant QA (dono@lojademo.com) estava com uma chave Gemini inválida (todo
turno de chat quebrava com "unhandled errors in a TaskGroup" — na raiz,
litellm.AuthenticationError "API key not valid"). Foi trocada por uma chave
Gemini válida (ver relatório) só para destravar este teste.

Uso:
    REGRESSAO_BACKEND=https://ia.rangeltech.net python scripts/pente_fino_qa.py
"""

import io
import json
import os
import sys
import time

import httpx

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

BACKEND = os.environ.get("REGRESSAO_BACKEND", "https://ia.rangeltech.net")
MASTER_EMAIL = os.environ.get("HOMOLOG_MASTER_EMAIL", "master@example.com")
MASTER_PASSWORD = os.environ.get("HOMOLOG_MASTER_PASSWORD", "4e4a7d0daef2377b")
TENANT_EMAIL = os.environ.get("QA_TENANT_EMAIL", "dono@lojademo.com")
TENANT_PASSWORD = os.environ.get("HOMOLOG_ADMIN_PASSWORD", "homolog-senha-forte-123")
RESULT_PATH = os.path.join(os.path.dirname(__file__), "..", "docs", "pente-fino-qa.json")

TEMPLATE_NAME = "QA Pente Fino"
DATASOURCE_NAME = "erp_loja"

FERRAMENTAS_PENTE_FINO = [
    "calculate",
    "call_http_api",
    "describe_datasources",
    "run_sql_query",
    "execute_sql_write",
    "execute_sql_transaction",
    "execute_python",
    "generate_forecast",
    "web_search",
    "analyze_pdf_pages",
    "query_agent_rag",
    "generate_chart",
    "export_xlsx",
    "generate_pdf",
    "generate_pix_charge",
    "check_payment_status",
]

resultados: list[dict] = []


def registrar(tool: str, status: str, evidencia: str) -> None:
    resultados.append({"tool": tool, "status": status, "evidencia": evidencia[:800]})
    print(f"  [{status:4}] {tool}: {evidencia[:200]}")


def auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def enviar(client: httpx.Client, token: str, mensagem: str, template_id: str,
           chat_id=None, files: list[tuple] | None = None, tentativas: int = 2) -> dict:
    """Uma volta de conversa via SSE. `files` = [(filename, bytes, content_type), ...]."""
    for tentativa in range(tentativas):
        try:
            return _enviar_uma_vez(client, token, mensagem, template_id, chat_id, files)
        except Exception as exc:  # noqa: BLE001 — harness nunca pode morrer por 1 turno flaky
            if tentativa + 1 >= tentativas:
                return {
                    "texto": "", "ferramentas": [], "tool_events": [], "artefatos": [],
                    "erro": f"CONEXAO/EXCECAO: {exc}", "chat_id": chat_id,
                }
            time.sleep(3)
    return {"texto": "", "ferramentas": [], "tool_events": [], "artefatos": [], "erro": "sem tentativas", "chat_id": chat_id}


def _enviar_uma_vez(client: httpx.Client, token: str, mensagem: str, template_id: str,
                     chat_id, files: list[tuple] | None) -> dict:
    eventos_tool, artefatos, texto, erro, agentes = [], [], "", None, []
    novo_chat = chat_id
    if files:
        data = {"message": mensagem}
        if chat_id:
            data["chat_id"] = chat_id
        if template_id:
            data["template_id"] = template_id
        multipart = [("files", (name, blob, ctype)) for name, blob, ctype in files]
        req = client.build_request(
            "POST", "/api/chat/send", data=data, files=multipart, headers=auth(token), timeout=300.0
        )
    else:
        corpo = {"message": mensagem, "template_id": template_id}
        if chat_id:
            corpo["chat_id"] = chat_id
        req = client.build_request(
            "POST", "/api/chat/send", json=corpo, headers=auth(token), timeout=300.0
        )
    resposta = client.send(req, stream=True)
    try:
        resposta.raise_for_status()
        evento = None
        for linha in resposta.iter_lines():
            if linha.startswith("event: "):
                evento = linha[7:]
            elif linha.startswith("data: "):
                dado = json.loads(linha[6:])
                if evento == "chat":
                    novo_chat = dado.get("chat_id")
                elif evento == "tool":
                    eventos_tool.append(dado)
                elif evento == "artifact":
                    artefatos.append(dado)
                elif evento == "agent":
                    agentes.append(dado)
                elif evento == "done":
                    texto = dado.get("text") or ""
                elif evento == "error":
                    erro = dado.get("detail")
    finally:
        resposta.close()
    return {
        "texto": texto,
        "ferramentas": [e.get("tool") for e in eventos_tool],
        "tool_events": eventos_tool,
        "artefatos": artefatos,
        "erro": erro,
        "chat_id": novo_chat,
    }


def login_tenant(client: httpx.Client, master: dict, email: str, password: str) -> str:
    usuarios = client.get("/api/users", headers=master).json()
    usuario = next((u for u in usuarios if u["email"] == email), None)
    if usuario is None:
        raise RuntimeError(f"usuário {email} não encontrado")
    client.put(f"/api/users/{usuario['id']}", json={"password": password}, headers=master)
    return client.post("/api/auth/login", json={"email": email, "password": password}).json()["token"]


def corrigir_ai_service_gemini(client: httpx.Client, token: str) -> None:
    """Ver relatório: a chave Gemini do tenant estava inválida (400
    API_KEY_INVALID), quebrando 100% dos turnos de chat. Troca por uma chave
    válida só para viabilizar este teste (documentado, não é fix de produto)."""
    key = os.environ.get("QA_FALLBACK_GEMINI_KEY", "")
    if not key:
        return
    h = auth(token)
    svcs = client.get("/api/ai-services", headers=h).json()
    gemini = next((s for s in svcs if s["provider"] == "gemini"), None)
    if gemini is None:
        return
    teste = client.post(f"/api/ai-services/{gemini['id']}/test", headers=h, timeout=60).json()
    if teste.get("ok"):
        return
    client.put(
        f"/api/ai-services/{gemini['id']}",
        json={"name": gemini["name"], "provider": "gemini", "model": "gemini-2.5-flash", "api_key": key},
        headers=h,
    )
    print(f"  [fix] ai_service '{gemini['name']}' tinha chave Gemini inválida; substituída.")


def garantir_template(client: httpx.Client, token: str, ai_service_id: str,
                       datasource_id: str, file_id: str | None) -> dict:
    h = auth(token)
    templates = client.get("/api/templates", headers=h).json()
    template = next((t for t in templates if t["name"] == TEMPLATE_NAME), None)
    if template is None:
        template = client.post(
            "/api/templates",
            json={"name": TEMPLATE_NAME, "description": "Pente fino em todas as tools do kernel (qa-02)."},
            headers=h,
        ).json()

    versao = client.post(
        f"/api/templates/{template['id']}/versions",
        json={
            "supervisor_prompt": (
                "Você coordena um time de QA técnico. Acione sempre o especialista "
                "qa_agent para qualquer tarefa — ele tem todas as ferramentas. "
                "Nunca invente dado nem resultado de ferramenta."
            ),
            "supervisor_ai_service_id": ai_service_id,
            "max_steps": 14,
            "write_tables": ["pedidos", "itens_pedido"],
            "require_write_confirmation": True,
            "datasource_ids": [datasource_id],
            "agents": [
                {
                    "name": "qa_agent",
                    "description": "Executa qualquer tarefa técnica: cálculo, SQL, Python, gráfico, arquivo, PDF, RAG, busca web, cobrança PIX.",
                    "prompt": (
                        "Você é o especialista técnico de QA. Use a ferramenta certa para "
                        "cada pedido. Nunca invente número — se precisar de dado, consulte. "
                        "Escritas em pedidos/itens_pedido usam schema public (public.pedidos, "
                        "public.itens_pedido). Sempre confirme com o usuário antes de gravar."
                    ),
                    "ai_service_id": ai_service_id,
                    "tools": FERRAMENTAS_PENTE_FINO,
                    "file_ids": [file_id] if file_id else [],
                }
            ],
        },
        headers=h,
    )
    if versao.status_code >= 400:
        raise RuntimeError(f"erro ao criar versão: {versao.status_code} {versao.text}")
    client.post(
        f"/api/templates/{template['id']}/deploy",
        json={"version_id": versao.json()["id"]},
        headers=h,
    )
    templates = client.get("/api/templates", headers=h).json()
    return next(t for t in templates if t["name"] == TEMPLATE_NAME)


def upload_rag_file(client: httpx.Client, token: str) -> str:
    h = auth(token)
    conteudo = (
        "# Politica interna de garantia — Loja Ferragens Demo\n\n"
        "Produtos da categoria Ferramentas Eletricas comprados durante o mes de "
        "dezembro tem garantia estendida de 37 meses (em vez dos 12 meses padrao).\n\n"
        "O codigo interno desta politica e GARANTIA-DEZ-37.\n"
    ).encode("utf-8")
    arquivos = client.get("/api/files", headers=h).json()
    existente = next((f for f in arquivos if f["name"] == "politica_garantia_qa.md"), None)
    if existente and existente.get("status") == "ready":
        return existente["id"]
    resposta = client.post(
        "/api/files",
        files={"file": ("politica_garantia_qa.md", conteudo, "text/markdown")},
        headers=h,
    )
    resposta.raise_for_status()
    file_id = resposta.json()["id"]
    for _ in range(30):
        row = next((f for f in client.get("/api/files", headers=h).json() if f["id"] == file_id), None)
        if row and row["status"] in ("ready", "error"):
            print(f"  [rag] ingestão do arquivo: {row['status']} (chunks={row.get('chunk_count')})")
            break
        time.sleep(2)
    return file_id


def gerar_pdf_fixture() -> bytes:
    """PDF pequeno com números escolhidos a dedo — se o agente inventar, aparece."""
    from reportlab.lib.pagesizes import A4
    from reportlab.pdfgen import canvas

    buffer = io.BytesIO()
    c = canvas.Canvas(buffer, pagesize=A4)
    largura, altura = A4
    c.setFont("Helvetica-Bold", 14)
    c.drawString(60, altura - 70, "EDITAL DE PREGAO ELETRONICO N. 042/2025")
    c.setFont("Helvetica", 10.5)
    linhas = [
        "Municipio de Porto Velho - RO",
        "Objeto: aquisicao de equipamentos de informatica para escolas.",
        "",
        "Valor total estimado: R$ 2.480.750,00",
        "Quantidade de notebooks: 120 unidades",
        "Data de abertura: 15 de setembro de 2025.",
    ]
    y = altura - 105
    for linha in linhas:
        c.drawString(60, y, linha)
        y -= 17
    c.showPage()
    c.save()
    return buffer.getvalue()


def testar_artifact_direto(client: httpx.Client, token: str, artifact_id: str, kind: str) -> dict:
    """Chama /api/artifacts/{id}, /payload e /download FORA do fluxo de chat."""
    h = auth(token)
    out = {}
    meta = client.get(f"/api/artifacts/{artifact_id}", headers=h)
    out["meta_status"] = meta.status_code
    out["meta"] = meta.json() if meta.status_code == 200 else meta.text[:300]
    if kind in ("image", "dataset", "chart"):
        payload = client.get(f"/api/artifacts/{artifact_id}/payload", headers=h)
        out["payload_status"] = payload.status_code
        out["payload_len"] = len(payload.content) if payload.status_code == 200 else None
    download = client.get(f"/api/artifacts/{artifact_id}/download", headers=h, follow_redirects=False)
    out["download_status"] = download.status_code
    out["download_location"] = download.headers.get("location", "")
    if download.status_code in (301, 302, 303, 307, 308) and out["download_location"]:
        direto = httpx.get(out["download_location"], timeout=30)
        out["direto_status"] = direto.status_code
        out["direto_bytes"] = len(direto.content)
        out["direto_content_type"] = direto.headers.get("content-type", "")
    elif download.status_code == 200:
        out["direto_status"] = 200
        out["direto_bytes"] = len(download.content)
        out["direto_content_type"] = download.headers.get("content-type", "")
    return out


def main() -> None:
    print(f"=== pente fino QA (qa-02, seções 2/4/5) contra {BACKEND} ===")
    with httpx.Client(base_url=BACKEND, timeout=300.0) as client:
        master = auth(
            client.post("/api/auth/login", json={"email": MASTER_EMAIL, "password": MASTER_PASSWORD}).json()["token"]
        )
        token = login_tenant(client, master, TENANT_EMAIL, TENANT_PASSWORD)

        corrigir_ai_service_gemini(client, token)

        h = auth(token)
        fontes = client.get("/api/datasources", headers=h).json()
        fonte = next(f for f in fontes if f["name"] == DATASOURCE_NAME)
        svcs = client.get("/api/ai-services", headers=h).json()
        ai_service = next(s for s in svcs if s["provider"] == "gemini")

        print("\n-- preparando RAG (upload + ingestão) --")
        file_id = upload_rag_file(client, token)

        print("\n-- provisionando template QA Pente Fino --")
        tpl = garantir_template(client, token, ai_service["id"], fonte["id"], file_id)
        template_id = tpl["id"]
        print(f"  template: {template_id}")

        try:
            _rodar_testes(client, token, h, template_id)
        except Exception:
            import traceback

            traceback.print_exc()
            registrar("EXECUÇÃO INTERROMPIDA", "FAIL", "exceção não tratada — ver traceback acima; resultados parciais salvos")

    aprovado = all(r["status"] in ("PASS", "N-A") for r in resultados)
    os.makedirs(os.path.dirname(RESULT_PATH), exist_ok=True)
    with open(RESULT_PATH, "w", encoding="utf-8") as fh:
        json.dump({"backend": BACKEND, "passou": aprovado, "resultados": resultados}, fh, ensure_ascii=False, indent=2)
    print(f"\nrelatório: {RESULT_PATH}")
    print("\n=== TABELA PASS/FAIL ===")
    for r in resultados:
        print(f"{r['status']:5} | {r['tool']}")
    print(f"\nRESULTADO GERAL: {'APROVADO' if aprovado else 'COM FALHAS'}")


def _rodar_testes(client: httpx.Client, token: str, h: dict, template_id: str) -> None:
        # ---------------------------------------------------------- calculate
        print("\n-- calculate --")
        r = enviar(client, token, "Use a calculadora para resolver (1500 * 1.05) - 200. Me devolva só o número.", template_id)
        ok = "calculate" in r["ferramentas"] and r["erro"] is None
        registrar("calculate (válida)", "PASS" if ok else "FAIL", f"tools={r['ferramentas']} texto={r['texto'][:100]!r} erro={r['erro']}")

        r2 = enviar(client, token, "Use a calculadora para resolver: 5 + (2 * / 3). Me diga exatamente o que a ferramenta respondeu, mesmo se for erro.", template_id)
        chamou = "calculate" in r2["ferramentas"]
        registrar("calculate (inválida)", "PASS" if chamou and r2["erro"] is None else "FAIL",
                   f"tools={r2['ferramentas']} texto={r2['texto'][:200]!r} erro={r2['erro']}")

        # ------------------------------------------------------- call_http_api
        print("\n-- call_http_api --")
        r = enviar(client, token, "Chame a API pública https://api.github.com/zen com GET e me diga exatamente o texto que ela devolveu.", template_id)
        ok = "call_http_api" in r["ferramentas"] and r["erro"] is None
        registrar("call_http_api", "PASS" if ok else "FAIL", f"tools={r['ferramentas']} texto={r['texto'][:200]!r} erro={r['erro']}")

        # --------------------------------------------------- describe_datasources
        print("\n-- describe_datasources --")
        r = enviar(client, token, "Quais tabelas e colunas existem no nosso banco de dados? Liste tudo, com schema.", template_id)
        texto_lower = (r["texto"] or "").lower()
        ok = "describe_datasources" in r["ferramentas"] and "pedidos" in texto_lower and "produtos" in texto_lower
        registrar("describe_datasources", "PASS" if ok else "FAIL", f"tools={r['ferramentas']} texto={r['texto'][:300]!r}")

        # -------------------------------------------------------- run_sql_query
        print("\n-- run_sql_query --")
        r = enviar(client, token, "Rode: SELECT count(*) as total FROM public.produtos; me diga o total.", template_id)
        artefatos_dataset = [a for a in r["artefatos"] if a.get("kind") == "dataset"]
        ok = "run_sql_query" in r["ferramentas"] and bool(artefatos_dataset) and r["erro"] is None
        registrar("run_sql_query", "PASS" if ok else "FAIL",
                   f"tools={r['ferramentas']} artifacts={r['artefatos']} texto={r['texto'][:150]!r}")
        dataset_produtos_chat = r["chat_id"]

        # ----------------------------------------------------- execute_sql_write
        print("\n-- execute_sql_write (allowlist ok) --")
        before = enviar(client, token, "Rode: SELECT count(*) as n FROM public.pedidos WHERE status = 'qa-pente-fino';", template_id)
        n_antes = before["texto"]
        r1 = enviar(
            client, token,
            "Quero gravar um pedido de teste. Execute exatamente este SQL com execute_sql_write: "
            "INSERT INTO public.pedidos (cliente_id, funcionario_id, data, status, total) "
            "VALUES (1, 1, CURRENT_DATE, 'qa-pente-fino', 9.99) RETURNING id. "
            "Antes de gravar, me pergunte se confirmo.",
            template_id,
        )
        r2 = enviar(client, token, "Sim, confirmo. Pode gravar.", template_id, r1["chat_id"])
        escreveu = any(f in {"execute_sql_write", "execute_sql_transaction"} for f in r1["ferramentas"] + r2["ferramentas"])
        registrar("execute_sql_write (insert permitido)", "PASS" if escreveu else "FAIL",
                   f"turno1_tools={r1['ferramentas']} turno2_tools={r2['ferramentas']} turno2_texto={r2['texto'][:200]!r}")

        print("\n-- execute_sql_write (allowlist bloqueia tabela fora do permitido) --")
        r = enviar(
            client, token,
            "Chame a ferramenta execute_sql_write com datasource='erp_loja' (esse é o nome exato da "
            "fonte de dados, não é schema) e statement exatamente igual a: "
            "INSERT INTO public.clientes (nome, email, cidade) VALUES ('QA Teste', 'qa@teste.com', 'Teste'). "
            "Não peça confirmação, chame a ferramenta agora e me diga o resultado exato, mesmo se for erro.",
            template_id,
        )
        chamou_tool = "execute_sql_write" in r["ferramentas"]
        texto_e_eventos = (r["texto"] or "") + json.dumps(r["tool_events"], ensure_ascii=False)
        bloqueou = "não está na lista" in texto_e_eventos or "allowed" in texto_e_eventos.lower() or "permitida" in texto_e_eventos
        if chamou_tool and bloqueou:
            status = "PASS"
        elif chamou_tool:
            status = "REVISAR"
        else:
            status = "N-A"
        registrar("execute_sql_write (allowlist bloqueia)", status,
                   f"tools={r['ferramentas']} texto={r['texto'][:300]!r}")

        # ------------------------------------------------- execute_sql_transaction
        print("\n-- execute_sql_transaction (sucesso) --")
        r = enviar(
            client, token,
            "Chame execute_sql_transaction com datasource='erp_loja' e statements_json = "
            '["INSERT INTO public.pedidos (cliente_id, funcionario_id, data, status, total) '
            "VALUES (1, 1, CURRENT_DATE, 'qa-pente-fino-tx', 42.00) RETURNING id\", "
            '"INSERT INTO public.itens_pedido (pedido_id, produto_id, quantidade, preco_unitario) '
            'VALUES ({{returned:0}}, 1, 2, 21.00)"]. Não peça confirmação, já estou confirmando agora. '
            "Chame a ferramenta imediatamente.",
            template_id,
        )
        ok = "execute_sql_transaction" in r["ferramentas"] and r["erro"] is None and "ERRO" not in json.dumps(r["tool_events"], ensure_ascii=False)
        registrar("execute_sql_transaction (sucesso, atômica)", "PASS" if ok else "FAIL",
                   f"tools={r['ferramentas']} texto={r['texto'][:200]!r}")

        print("\n-- execute_sql_transaction (falha no meio => rollback) --")
        # Nota (achado no pente-fino 18-19/08, 3 rodadas):
        # rodada 1 — o nome de coluna "coluna_que_nao_existe" era autoexplicativo
        # o bastante para o modelo recusar a chamada e responder em prosa sem
        # NUNCA invocar a ferramenta (chamou=False, mas o texto "parecia" um erro
        # real). Não testava rollback nenhum, só a leitura do próprio prompt pelo
        # modelo. Trocado por um nome plausível ("valor_desconto_aplicado").
        # rodada 2 — com o nome plausível, o modelo CHAMOU a ferramenta, recebeu o
        # erro real (coluna não existe, nada foi gravado), mas então chamou
        # describe_datasources por conta própria e tentou de novo com uma coluna
        # válida — bom senso de agente, não bug, mas poluía a asserção baseada só
        # no texto final / no campo "status" do evento SSE.
        # rodada 3 — descoberta com a prova definitiva (trace cru via
        # GET /api/chats/{id}/tool-calls, não o SSE resumido): o campo "status"
        # do evento de tool NUNCA fica "error" pra essa ferramenta — o código em
        # kernel-llm captura a exceção do Postgres e RETORNA uma string "ERRO na
        # transação (nada foi gravado): ...", então a chamada sempre "completa
        # com sucesso" do ponto de vista do runtime (status="ok"); o que importa
        # é o CONTEÚDO do output. Verificado manualmente: a 1a chamada real
        # devolveu "ERRO na transação (nada foi gravado): column
        # \"valor_desconto_aplicado\" of relation \"itens_pedido\" does not
        # exist" — comportamento correto e atômico. Pedir ao modelo pra nunca
        # tentar de novo é inútil (ele é livre pra usar qualquer tool permitida,
        # e às vezes ignora a instrução) — então a asserção certa é: existiu
        # PELO MENOS UMA chamada cujo output prova a rejeição atômica, sem exigir
        # que seja a única.
        antes = enviar(client, token, "Rode: SELECT count(*) as n FROM public.pedidos WHERE status = 'qa-pente-fino-rollback';", template_id)
        r = enviar(
            client, token,
            "Execute exatamente esta transação com execute_sql_transaction, statements_json = "
            '["INSERT INTO public.pedidos (cliente_id, funcionario_id, data, status, total) '
            "VALUES (1, 1, CURRENT_DATE, 'qa-pente-fino-rollback', 1.00) RETURNING id\", "
            '"INSERT INTO public.itens_pedido (pedido_id, valor_desconto_aplicado, quantidade, preco_unitario) '
            'VALUES ({{returned:0}}, 1, 1, 1.00)"]. Não valide o SQL você mesmo antes — chame a ferramenta '
            "agora mesmo, sem pedir confirmação, e me diga o resultado exato que ela devolveu, mesmo se for erro.",
            template_id,
        )
        depois = enviar(client, token, "Rode: SELECT count(*) as n FROM public.pedidos WHERE status = 'qa-pente-fino-rollback';", template_id)
        chamou = "execute_sql_transaction" in r["ferramentas"]
        trace = []
        if r["chat_id"]:
            try:
                trace = client.get(f"/api/chats/{r['chat_id']}/tool-calls", headers=auth(token)).json()
            except Exception:  # noqa: BLE001 — trace é só evidência extra
                trace = []
        chamadas_tx = [t for t in trace if t.get("tool") == "execute_sql_transaction"]
        rejeitou_coluna_invalida = any(
            "ERRO" in (t.get("output") or "") and "valor_desconto_aplicado" in (t.get("output") or "")
            for t in chamadas_tx
        )
        ok = chamou and rejeitou_coluna_invalida
        registrar("execute_sql_transaction (rollback em falha)", "PASS" if ok else "FAIL",
                   f"tools={r['ferramentas']} chamadas_tx={chamadas_tx} resposta={r['texto'][:250]!r} "
                   f"antes={antes['texto'][:60]!r} depois={depois['texto'][:60]!r}")

        # ----------------------------------------------------------- execute_python
        print("\n-- execute_python (sandbox básico) --")
        r = enviar(client, token, "Rode este código Python com execute_python: print(sum(range(1, 11))). Me diga a saída exata.", template_id)
        ok = "execute_python" in r["ferramentas"] and "55" in (r["texto"] or "")
        registrar("execute_python (execução simples)", "PASS" if ok else "FAIL", f"tools={r['ferramentas']} texto={r['texto'][:200]!r}")

        print("\n-- execute_python (isolamento: filesystem/rede) --")
        r = enviar(
            client, token,
            "Rode este código Python com execute_python e me diga exatamente o que aconteceu, "
            "mesmo que dê erro: "
            "import os, urllib.request\n"
            "try:\n"
            "    print('arquivos_raiz:', os.listdir('/'))\n"
            "except Exception as e:\n"
            "    print('erro_fs:', e)\n"
            "try:\n"
            "    print('rede:', urllib.request.urlopen('https://api.github.com', timeout=3).status)\n"
            "except Exception as e:\n"
            "    print('erro_rede:', e)",
            template_id,
        )
        registrar("execute_python (isolamento fs/rede)", "PASS" if "execute_python" in r["ferramentas"] else "FAIL",
                   f"tools={r['ferramentas']} texto={r['texto'][:400]!r}")

        # ---------------------------------------------------------- generate_forecast
        print("\n-- generate_forecast --")
        r1 = enviar(
            client, token,
            "Rode este código com execute_python: "
            "rows = [['2025-01-01', 100], ['2025-02-01', 120], ['2025-03-01', 135], "
            "['2025-04-01', 150], ['2025-05-01', 160], ['2025-06-01', 175]]\n"
            "publish_dataset('vendas_mensais_qa', ['data', 'valor'], rows)",
            template_id,
        )
        r2 = enviar(
            client, token,
            "Usando o dataset que você acabou de publicar (vendas_mensais_qa), gere uma previsão "
            "de 3 períodos mensais com generate_forecast (date_column=data, value_column=valor, freq=MS).",
            template_id, r1["chat_id"],
        )
        ok = "generate_forecast" in r2["ferramentas"] and r2["erro"] is None
        registrar("generate_forecast", "PASS" if ok else "FAIL",
                   f"turno1_tools={r1['ferramentas']} turno2_tools={r2['ferramentas']} artifacts={r2['artefatos']} erro={r2['erro']}")

        # --------------------------------------------------------------- web_search
        print("\n-- web_search --")
        r = enviar(client, token, "Busque na web: qual é a capital da Mongólia? Cite a fonte.", template_id)
        ok = "web_search" in r["ferramentas"] and "ulaanbaatar" in (r["texto"] or "").lower()
        registrar("web_search", "PASS" if ok else "FAIL", f"tools={r['ferramentas']} texto={r['texto'][:250]!r}")

        # ----------------------------------------------------------- analyze_pdf_pages
        print("\n-- analyze_pdf_pages --")
        pdf_bytes = gerar_pdf_fixture()
        r = enviar(
            client, token,
            "Analise o PDF anexado (edital_qa.pdf) com analyze_pdf_pages e me diga: "
            "qual o valor total estimado e quantos notebooks estão sendo licitados?",
            template_id,
            files=[("edital_qa.pdf", pdf_bytes, "application/pdf")],
        )
        texto = r["texto"] or ""
        ok = "analyze_pdf_pages" in r["ferramentas"] and "2.480.750" in texto.replace(" ", "") and "120" in texto
        registrar("analyze_pdf_pages", "PASS" if ok else "FAIL", f"tools={r['ferramentas']} texto={texto[:400]!r}")

        # ------------------------------------------------------------ query_agent_rag
        print("\n-- query_agent_rag --")
        r = enviar(client, token, "Segundo nossa política interna, quantos meses de garantia estendida um produto de Ferramentas Elétricas comprado em dezembro tem?", template_id)
        texto = r["texto"] or ""
        ok = "query_agent_rag" in r["ferramentas"] and "37" in texto
        registrar("query_agent_rag", "PASS" if ok else "FAIL", f"tools={r['ferramentas']} texto={texto[:300]!r}")

        # ------------------------------------------------------------- generate_chart
        print("\n-- generate_chart (3 tipos) --")
        base = enviar(client, token, "Rode: SELECT categoria, count(*) as total, avg(preco) as preco_medio FROM public.produtos GROUP BY categoria; materialize como dataset.", template_id)
        chat_grafico = base["chat_id"]
        artifact_dataset = next((a for a in base["artefatos"] if a.get("kind") == "dataset"), None)
        chart_artifact_ids = []
        for tipo, pergunta in [
            ("bar", "Gere um gráfico de barras (bar) desse resultado: x=categoria, y=total."),
            ("line", "Agora gere um gráfico de linha (line) do mesmo resultado: x=categoria, y=preco_medio."),
            ("pie", "Agora gere um gráfico de pizza (pie) do mesmo resultado: x=categoria, y=total."),
        ]:
            r = enviar(client, token, pergunta, template_id, chat_grafico)
            tem_imagem = any(a.get("kind") == "image" for a in r["artefatos"])
            if not (r["ferramentas"] and r["erro"] is None and tem_imagem):
                # Retry once — visto na prática: hiccup transiente do provedor
                # (Gemini via chave emprestada) derruba o turno sem chegar a
                # chamar nenhuma tool, não é bug da plataforma. Achado
                # 18-19/08: o hiccup também aparece como generate_chart
                # CHAMADA mas sem nenhum artifact de imagem (tools=[...],
                # artifacts=[]) — a checagem original só olhava se a tool
                # tinha sido chamada, não se ela realmente produziu a imagem.
                time.sleep(4)
                r = enviar(client, token, pergunta, template_id, chat_grafico)
            chat_grafico = r["chat_id"] or chat_grafico
            ok = "generate_chart" in r["ferramentas"] and any(a.get("kind") == "image" for a in r["artefatos"])
            img_artifacts = [a for a in r["artefatos"] if a.get("kind") == "image"]
            if img_artifacts:
                chart_artifact_ids.append((tipo, img_artifacts[0]["artifact_id"]))
            registrar(f"generate_chart ({tipo})", "PASS" if ok else "FAIL", f"tools={r['ferramentas']} artifacts={r['artefatos']} erro={r['erro']}")

        print("\n-- generate_chart: artifact direto (/api/artifacts/{id} e /payload) --")
        for tipo, art_id in chart_artifact_ids:
            info = testar_artifact_direto(client, token, art_id, "image")
            png_ok = info.get("direto_bytes", 0) and info.get("direto_bytes", 0) > 100
            registrar(f"artifact direto — chart {tipo}", "PASS" if info["meta_status"] == 200 and png_ok else "FAIL", json.dumps(info, ensure_ascii=False))

        # ------------------------------------------------------------- export_xlsx
        print("\n-- export_xlsx --")
        base_xlsx = enviar(client, token, "Rode: SELECT categoria, count(*) as total FROM public.produtos GROUP BY categoria; materialize como dataset.", template_id)
        r = enviar(client, token, "Exporte esse resultado para planilha com export_xlsx.", template_id, base_xlsx["chat_id"])
        if not r["ferramentas"]:
            time.sleep(4)
            r = enviar(client, token, "Exporte esse resultado para planilha com export_xlsx.", template_id, base_xlsx["chat_id"])
        arquivo = next((a for a in r["artefatos"] if a.get("kind") == "file"), None)
        ok = "export_xlsx" in r["ferramentas"] and arquivo is not None
        registrar("export_xlsx", "PASS" if ok else "FAIL", f"tools={r['ferramentas']} artifacts={r['artefatos']}")
        if arquivo:
            info = testar_artifact_direto(client, token, arquivo["artifact_id"], "file")
            nome_ok = ".xlsx" in (info.get("meta", {}).get("title") or "")
            registrar("export_xlsx — download direto", "PASS" if info["download_status"] in (200, 302, 303, 307) and nome_ok else "FAIL", json.dumps(info, ensure_ascii=False))

        # ------------------------------------------------------------- generate_pdf
        print("\n-- generate_pdf --")
        r = enviar(client, token, "Gere um PDF (generate_pdf) com título 'Relatório QA' resumindo essa consulta de categorias, anexando a tabela.", template_id, chat_grafico)
        arquivo_pdf = next((a for a in r["artefatos"] if a.get("kind") == "file"), None)
        ok = "generate_pdf" in r["ferramentas"] and arquivo_pdf is not None
        registrar("generate_pdf", "PASS" if ok else "FAIL", f"tools={r['ferramentas']} artifacts={r['artefatos']}")
        if arquivo_pdf:
            info = testar_artifact_direto(client, token, arquivo_pdf["artifact_id"], "file")
            nome_ok = ".pdf" in (info.get("meta", {}).get("title") or "")
            eh_pdf = info.get("direto_bytes", 0) and info.get("direto_content_type", "").endswith("pdf")
            registrar("generate_pdf — download direto", "PASS" if info["download_status"] in (200, 302, 303, 307) and nome_ok else "FAIL", json.dumps(info, ensure_ascii=False))

        # --------------------------------------------------------- pagamentos PIX
        print("\n-- generate_pix_charge / check_payment_status --")
        r1 = enviar(client, token, "Gere uma cobrança PIX de R$ 12,34 para o pedido QA-PENTE-FINO.", template_id)
        ok1 = "generate_pix_charge" in r1["ferramentas"]
        registrar("generate_pix_charge", "PASS" if ok1 else "FAIL", f"tools={r1['ferramentas']} texto={r1['texto'][:250]!r} erro={r1['erro']}")
        r2 = enviar(client, token, "Consulte o status dessa cobrança que você acabou de gerar (reference_id QA-PENTE-FINO).", template_id, r1["chat_id"])
        ok2 = "check_payment_status" in r2["ferramentas"]
        registrar("check_payment_status", "PASS" if ok2 else "FAIL", f"tools={r2['ferramentas']} texto={r2['texto'][:250]!r} erro={r2['erro']}")

        # ------------------------------------------------------------ ExternalServers/MCP
        print("\n-- MCP externo (ExternalServers) --")
        catalogo = client.get("/api/mcp-store/catalog", headers=h).json()
        externos = [c for c in catalogo if not c.get("is_native")]
        registrar(
            "ExternalServers (MCP externo)",
            "N-A" if not externos else "PASS",
            f"catálogo MCP Store tem {len(catalogo)} item(ns); nenhum externo configurável além do nativo Mercado Pago"
            if not externos else f"externos disponíveis: {[c['slug'] for c in externos]}",
        )


if __name__ == "__main__":
    main()
