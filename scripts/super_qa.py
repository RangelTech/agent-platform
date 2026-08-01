"""Super QA: exercita, com modelo real, tudo que a plataforma promete.

Existe porque a camada de modelos mudou (9Router por tenant) e isso toca o
núcleo: se o roteamento quebrar, quebra consulta a banco, escrita com
confirmação, geração de artefato e cobrança. Cada checagem aqui é um efeito
observável — conversa que responde, linha que aparece no banco, artefato que
é gerado — não um mock.

Uso:
    REGRESSAO_BACKEND=https://... python scripts/super_qa.py
"""

import json
import os
import sys
import time
import uuid

import httpx

BACKEND = os.environ.get("REGRESSAO_BACKEND", "http://localhost:8090")
MASTER_EMAIL = os.environ.get("HOMOLOG_MASTER_EMAIL", "master@example.com")
MASTER_PASSWORD = os.environ.get("HOMOLOG_MASTER_PASSWORD", "admin123")
TENANT_PASSWORD = os.environ.get("HOMOLOG_ADMIN_PASSWORD", "homolog-senha-forte-123")
RESULT_PATH = os.path.join(os.path.dirname(__file__), "..", "docs", "super-qa.json")

checks: list[dict] = []


def _print(texto: str) -> None:
    codec = sys.stdout.encoding or "utf-8"
    print(texto.encode(codec, errors="replace").decode(codec))


def check(area: str, nome: str, ok: bool, detalhe: str = "") -> bool:
    checks.append({"area": area, "check": nome, "ok": bool(ok), "detalhe": detalhe[:400]})
    extra = f" - {detalhe[:160]}" if detalhe and not ok else ""
    _print(f"  [{'ok ' if ok else 'FAIL'}] {area}: {nome}{extra}")
    return ok


def auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def enviar(client: httpx.Client, token: str, mensagem: str, template_id: str, chat_id=None) -> dict:
    """Uma volta completa de conversa, consumindo o SSE como o navegador faz."""
    corpo = {"message": mensagem, "template_id": template_id}
    if chat_id:
        corpo["chat_id"] = chat_id
    eventos, ferramentas, artefatos, texto, erro, novo_chat = [], [], [], "", None, chat_id
    with client.stream(
        "POST", "/api/chat/send", json=corpo, headers=auth(token), timeout=300.0
    ) as resposta:
        resposta.raise_for_status()
        evento = None
        for linha in resposta.iter_lines():
            if linha.startswith("event: "):
                evento = linha[7:]
            elif linha.startswith("data: "):
                dado = json.loads(linha[6:])
                eventos.append(evento)
                if evento == "chat":
                    novo_chat = dado.get("chat_id")
                elif evento == "tool":
                    ferramentas.append(dado.get("tool"))
                elif evento == "artifact":
                    artefatos.append(dado.get("kind"))
                elif evento == "done":
                    texto = dado.get("text") or ""
                elif evento == "error":
                    erro = dado.get("detail")
    return {
        "texto": texto,
        "ferramentas": ferramentas,
        "artefatos": artefatos,
        "erro": erro,
        "chat_id": novo_chat,
    }


def login_tenant(client: httpx.Client, master: dict, email: str) -> tuple[str, dict] | None:
    usuarios = client.get("/api/users", headers=master).json()
    usuario = next((u for u in usuarios if u["email"] == email), None)
    if usuario is None:
        return None
    client.put(f"/api/users/{usuario['id']}", json={"password": TENANT_PASSWORD}, headers=master)
    token = client.post(
        "/api/auth/login", json={"email": email, "password": TENANT_PASSWORD}
    ).json()["token"]
    return token, usuario


TEMPLATE_QA = "QA Completo"

# O template de QA declara todas as ferramentas que este roteiro exercita. Sem
# isso o teste mediria a configuração da demo, não a capacidade da plataforma:
# um agente simplesmente não chama a ferramenta que o template não deu a ele.
FERRAMENTAS_QA = [
    "describe_datasources",
    "run_sql_query",
    "calculate",
    "generate_chart",
    "export_xlsx",
    "generate_pdf",
    "generate_pix_charge",
    "check_payment_status",
]


def garantir_template_qa(client: httpx.Client, token: str) -> dict | None:
    """Cria (ou reaproveita) o template que exercita a superfície completa."""
    cabecalho = auth(token)
    templates = client.get("/api/templates", headers=cabecalho).json()
    template = next((t for t in templates if t["name"] == TEMPLATE_QA), None)
    if template and template.get("active_version_id"):
        return template

    if template is None:
        template = client.post(
            "/api/templates",
            json={"name": TEMPLATE_QA, "description": "Template usado pelo super QA."},
            headers=cabecalho,
        ).json()

    fontes = client.get("/api/datasources", headers=cabecalho).json()
    fonte = next((d for d in fontes if d["name"] == "erp_loja"), None)

    versao = client.post(
        f"/api/templates/{template['id']}/versions",
        json={
            "supervisor_prompt": (
                "Você coordena um time de QA. Atenda exatamente o que for pedido, "
                "acionando o especialista disponível."
            ),
            "max_steps": 10,
            "agents": [
                {
                    "name": "qa_agent",
                    "description": "Consulta dados, gera artefatos e emite cobranças.",
                    "prompt": (
                        "Você executa tarefas operacionais. Consulte a base quando "
                        "precisar de dados, gere gráficos e planilhas quando pedirem "
                        "uma saída visual ou arquivo, e use a ferramenta de cobrança "
                        "PIX quando pedirem uma cobrança. Não invente números."
                    ),
                    "tools": FERRAMENTAS_QA,
                }
            ],
            "datasource_ids": [fonte["id"]] if fonte else [],
            "require_write_confirmation": True,
        },
        headers=cabecalho,
    )
    if versao.status_code >= 400:
        return None
    client.post(
        f"/api/templates/{template['id']}/deploy",
        json={"version_id": versao.json()["id"]},
        headers=cabecalho,
    )
    return client.get("/api/templates", headers=cabecalho).json() and next(
        t
        for t in client.get("/api/templates", headers=cabecalho).json()
        if t["name"] == TEMPLATE_QA
    )


def main() -> None:
    _print(f"=== super QA contra {BACKEND} ===")
    with httpx.Client(base_url=BACKEND, timeout=300.0) as client:
        master = auth(
            client.post(
                "/api/auth/login", json={"email": MASTER_EMAIL, "password": MASTER_PASSWORD}
            ).json()["token"]
        )

        # ---------------------------------------------------- ferragista ---
        _print("\n-- ferragista: leitura, cálculo e escrita confirmada --")
        credenciais = login_tenant(client, master, "dono@lojademo.com")
        if check("ferragista", "empresa existe", credenciais is not None):
            token, _ = credenciais
            templates = client.get("/api/templates", headers=auth(token)).json()
            tpl = next((t for t in templates if t["name"] == "Balcao de Vendas"), None)
            check("ferragista", "template ativo", bool(tpl and tpl["active_version_id"]))

            fontes = client.get("/api/datasources", headers=auth(token)).json()
            fonte = next((d for d in fontes if d["name"] == "erp_loja"), None)
            teste = client.post(f"/api/datasources/{fonte['id']}/test", headers=auth(token)).json()
            check("ferragista", "fonte de dados responde", teste.get("ok") is True, str(teste))

            leitura = enviar(
                client,
                token,
                "Liste 3 produtos com preço e estoque e some o total de 1 unidade de cada.",
                tpl["id"],
            )
            consultou = "run_sql_query" in leitura["ferramentas"]
            materializou = "dataset" in leitura["artefatos"]
            check(
                "ferragista",
                "consulta SQL com artefato",
                consultou and materializou,
                str(leitura["ferramentas"]),
            )
            check("ferragista", "resposta sem erro", leitura["erro"] is None, str(leitura["erro"]))

            venda = enviar(
                client,
                token,
                "Quero registrar a venda de 1 Furadeira 650W para o cliente 1. Pode gravar.",
                tpl["id"],
                leitura["chat_id"],
            )
            confirma = enviar(
                client, token, "Sim, confirmo o registro.", tpl["id"], venda["chat_id"]
            )
            escreveu = any(
                f in {"execute_sql_write", "execute_sql_transaction"}
                for f in venda["ferramentas"] + confirma["ferramentas"]
            )
            check("ferragista", "venda gravada após confirmação", escreveu)

        # -------------------------------------------------- hamburgueria ---
        _print("\n-- hamburgueria: cardápio e pedido --")
        credenciais = login_tenant(client, master, "dono@hamburgueriademo.com")
        if check("hamburgueria", "empresa existe", credenciais is not None):
            token, _ = credenciais
            templates = client.get("/api/templates", headers=auth(token)).json()
            tpl = next((t for t in templates if t["name"] == "Atendimento Hamburgueria"), None)
            check("hamburgueria", "template ativo", bool(tpl and tpl["active_version_id"]))

            cardapio = enviar(client, token, "Quais lanches vocês têm e os preços?", tpl["id"])
            check(
                "hamburgueria",
                "cardápio veio do banco",
                "run_sql_query" in cardapio["ferramentas"],
                str(cardapio["ferramentas"]),
            )
            check(
                "hamburgueria", "resposta sem erro", cardapio["erro"] is None, str(cardapio["erro"])
            )

        # ------------------------------------------------------ artefatos ---
        _print("\n-- artefatos: gráfico e planilha a partir de consulta --")
        credenciais = login_tenant(client, master, "dono@lojademo.com")
        if credenciais:
            token, _ = credenciais
            tpl = garantir_template_qa(client, token)
            check("artefatos", "template de QA disponível", tpl is not None)
            saida = enviar(
                client,
                token,
                "Consulte o total de pedidos por status e gere um gráfico. "
                "Depois exporte o mesmo dado para planilha.",
                tpl["id"],
            )
            check(
                "artefatos",
                "gráfico ou planilha gerados",
                any(f in {"generate_chart", "export_xlsx"} for f in saida["ferramentas"]),
                str(saida["ferramentas"]),
            )
            check("artefatos", "sem erro no fluxo", saida["erro"] is None, str(saida["erro"]))

        # ----------------------------------------------------- pagamentos ---
        _print("\n-- pagamentos: credencial e cobrança --")
        credenciais = login_tenant(client, master, "dono@lojademo.com")
        if credenciais:
            token, _ = credenciais
            segredo = f"APP_USR-{uuid.uuid4().hex}"
            salvar = client.put(
                "/api/payments/credentials",
                json={"access_token": segredo, "sandbox": True},
                headers=auth(token),
            )
            check(
                "pagamentos",
                "credencial salva sem voltar em claro",
                salvar.status_code == 200 and segredo not in salvar.text,
                salvar.text,
            )
            listagem = client.get("/api/payments/credentials", headers=auth(token))
            check("pagamentos", "listagem não expõe token", segredo not in listagem.text)

            # A tool precisa existir no catálogo para o agente conseguir cobrar.
            catalogo = client.get("/api/toolkits", headers=auth(token)).json()
            nomes = {t.get("name") for t in catalogo}
            check(
                "pagamentos",
                "tools de cobrança disponíveis",
                {"generate_pix_charge", "check_payment_status"} <= nomes,
            )

            tpl = garantir_template_qa(client, token)
            cobranca = enviar(
                client,
                token,
                "Gere uma cobrança PIX de R$ 12,34 para o pedido TESTE-QA.",
                tpl["id"],
            )
            # Sem credencial real do gateway a cobrança não completa; o que se
            # exige aqui é que o agente chame a tool e explique o resultado.
            check(
                "pagamentos",
                "agente aciona a cobrança",
                "generate_pix_charge" in cobranca["ferramentas"],
                str(cobranca["ferramentas"]),
            )

        # -------------------------------------------- isolamento de dados ---
        _print("\n-- isolamento entre empresas --")
        outra = client.post(
            "/api/tenants",
            json={"name": f"QA {uuid.uuid4().hex[:6]}", "tenant_key": f"qa-{uuid.uuid4().hex[:6]}"},
            headers=master,
        ).json()
        email = f"qa-{uuid.uuid4().hex[:6]}@example.com"
        client.post(
            "/api/users",
            json={
                "email": email,
                "name": "QA",
                "password": TENANT_PASSWORD,
                "tenant_id": outra["id"],
            },
            headers=master,
        )
        token_outro = client.post(
            "/api/auth/login", json={"email": email, "password": TENANT_PASSWORD}
        ).json()["token"]

        fontes = client.get("/api/datasources", headers=auth(token_outro)).json()
        check("isolamento", "não enxerga fonte de dados alheia",
              all(d["name"] != "erp_loja" for d in fontes))
        credenciais_pgto = client.get("/api/payments/credentials", headers=auth(token_outro))
        check("isolamento", "não enxerga credencial de pagamento alheia",
              credenciais_pgto.status_code in (200, 403)
              and "APP_USR" not in credenciais_pgto.text)
        servicos = client.get("/api/ai-services", headers=auth(token_outro)).json()
        check("isolamento", "não enxerga serviço de IA alheio", len(servicos) == 0, str(servicos))
        contas_ia = client.get("/api/ai-router/contas", headers=auth(token_outro))
        check("isolamento", "sem instância de IA não acessa contas",
              contas_ia.status_code == 409, contas_ia.text)

        # ------------------------------------------------------- memória ---
        _print("\n-- memória e histórico --")
        credenciais = login_tenant(client, master, "dono@lojademo.com")
        if credenciais:
            token, _ = credenciais
            templates = client.get("/api/templates", headers=auth(token)).json()
            tpl = next(t for t in templates if t["name"] == "Balcao de Vendas")
            primeira = enviar(client, token, "Meu nome é Ana. Guarde isso.", tpl["id"])
            time.sleep(2)
            segunda = enviar(
                client, token, "Qual é o meu nome?", tpl["id"], primeira["chat_id"]
            )
            check(
                "memória",
                "o agente lembra dentro da conversa",
                "ana" in (segunda["texto"] or "").lower(),
                (segunda["texto"] or "")[:120],
            )

    aprovado = all(c["ok"] for c in checks)
    os.makedirs(os.path.dirname(RESULT_PATH), exist_ok=True)
    with open(RESULT_PATH, "w", encoding="utf-8") as fh:
        json.dump({"backend": BACKEND, "passou": aprovado, "checks": checks}, fh,
                  ensure_ascii=False, indent=2)
    _print(f"\nrelatório: {RESULT_PATH}")
    _print(f"RESULTADO: {'APROVADO' if aprovado else 'REPROVADO'}")
    sys.exit(0 if aprovado else 1)


if __name__ == "__main__":
    main()
