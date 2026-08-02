"""Monta o template grande da LicitaEnterprisse e as memórias iniciais.

Seis especialistas de propósito: o objetivo do teste é ver o supervisor
escolhendo entre muitos, não um agente sozinho fazendo tudo.
"""

import json
import os
import sys

import httpx

B = "https://teste-ia-backend-x27vtpiida-uc.a.run.app"
EMAIL = "dono@licita.com"
SENHA = "licita-senha-forte-123"

FERRAMENTAS_DADOS = ["describe_datasources", "run_sql_query", "calculate"]
FERRAMENTAS_SAIDA = ["generate_chart", "export_xlsx", "generate_pdf", "generate_forecast"]
FERRAMENTAS_PESADAS = ["execute_python", "analyze_pdf_pages", "query_agent_rag"]
FERRAMENTAS_COBRANCA = ["generate_pix_charge", "check_payment_status"]

# Todo especialista que consulta dado também entrega o resultado. Observado no
# QA: perguntado por uma projeção, o supervisor mandou a pergunta inteira para
# `despesas` e não chamou `analista` — e `despesas` não tinha execute_python,
# então a resposta foi "não tenho essa ferramenta". Depender de o supervisor
# encadear dois especialistas é frágil; dar a ferramenta a quem já está no
# caminho não é.
FERRAMENTAS_ANALISE = FERRAMENTAS_DADOS + FERRAMENTAS_SAIDA + ["execute_python"]

AGENTES = [
    {
        "name": "editais",
        "description": "Especialista em editais e contratações do PNCP.",
        "prompt": (
            "Você lê editais e contratações públicas no PNCP. As tabelas principais são "
            "`obt_pncp_editais_semantico`, `obt_pncp_contratacoes`, `obt_pncp_contratos` e "
            "`obt_pncp_atas`, todas em `mi-prd-lake.semantic_zone`. Sempre filtre por "
            "município ou UF quando a pergunta indicar. Nunca invente número: se a consulta "
            "não trouxer linha, diga que não há registro para aquele filtro."
        ),
        "tools": FERRAMENTAS_ANALISE,
    },
    {
        "name": "itens",
        "description": "Especialista em itens de licitação e planos de contratação.",
        "prompt": (
            "Você analisa itens de licitação. Use `obt_pncp_pca` e `obt_pncp_pca_itens` em "
            "`mi-prd-lake.semantic_zone`. Quando pedirem comparação de preço de item, "
            "agregue e mostre a dispersão, não só a média — média sozinha esconde outlier."
        ),
        "tools": FERRAMENTAS_ANALISE,
    },
    {
        "name": "despesas",
        "description": "Especialista em despesa pública de educação (SIOPE).",
        "prompt": (
            "Você lê despesa do SIOPE. Tabelas: "
            "`obt_fnde_siope_despesa_educacao_municipio_ano` e "
            "`obt_fnde_siope_despesa_funcao_municipio_ano` em `mi-prd-lake.semantic_zone`. "
            "Sempre informe o ano de referência junto do valor — despesa sem ano não "
            "significa nada."
        ),
        "tools": FERRAMENTAS_ANALISE,
    },
    {
        "name": "receitas",
        "description": "Especialista em receita pública municipal (SIOPE).",
        "prompt": (
            "Você lê receita do SIOPE em `obt_fnde_siope_receita_municipio_ano` "
            "(`mi-prd-lake.semantic_zone`). O campo `codigo_conta_contabil` separa as "
            "contas; agregue com cuidado para não somar conta com subconta e dobrar o valor."
        ),
        "tools": FERRAMENTAS_ANALISE,
    },
    {
        "name": "contabilidade",
        "description": "Especialista em contabilidade pública e RREO.",
        "prompt": (
            "Você interpreta o RREO e os indicadores contábeis: `obt_rreo_siope_municipio_ano`, "
            "`obt_rreo_siope_municipio_bimestre` e `obt_fnde_siope_indicador_municipio_ano` em "
            "`mi-prd-lake.semantic_zone`. Explique o que o indicador significa antes de dar o "
            "número — quem pergunta raramente conhece a sigla."
        ),
        "tools": FERRAMENTAS_ANALISE,
    },
    {
        "name": "gerais",
        "description": "Dados gerais do município, geografia e contexto.",
        "prompt": (
            "Você resolve contexto: `obt_fnde_siope_dados_gerais_municipio_ano`, "
            "`obt_ibge_municipio` e `obt_ibge_uf` em `mi-prd-lake.semantic_zone`. Você é quem "
            "traduz nome de município em código IBGE para os outros especialistas."
        ),
        "tools": FERRAMENTAS_ANALISE,
    },
    {
        "name": "analista",
        "description": "Cálculo pesado, projeção, manipulação de planilha e gráfico.",
        "prompt": (
            "Você faz o trabalho pesado de análise. Use `execute_python` para estatística, "
            "séries temporais (ARIMA/SARIMA) e manipulação de planilha. Use "
            "`generate_forecast` para projeção sobre um conjunto de dados já consultado. "
            "Quando pedirem gráfico ou planilha a partir de algo que já foi consultado nesta "
            "conversa, use o resultado que já existe — não consulte de novo."
        ),
        "tools": (
            FERRAMENTAS_DADOS
            + FERRAMENTAS_SAIDA
            + FERRAMENTAS_PESADAS
            + FERRAMENTAS_COBRANCA
            + ["web_search"]
        ),
    },
    {
        "name": "financeiro",
        "description": "Emite cobrança PIX e confere pagamento.",
        "prompt": (
            "Você cuida de cobrança. Use `generate_pix_charge` para emitir e "
            "`check_payment_status` para conferir. Confirme o valor com o "
            "usuário antes de emitir e repita o valor na resposta — cobrança "
            "com valor errado é o pior erro possível aqui."
        ),
        "tools": FERRAMENTAS_COBRANCA + ["calculate"],
    },
    {
        "name": "pesquisador",
        "description": "Busca informação pública na internet.",
        "prompt": (
            "Você busca na web o que não está no banco: nova legislação, notícia sobre um "
            "município, prazo de edital publicado fora do PNCP. Sempre cite a fonte."
        ),
        "tools": ["web_search", "call_http_api"],
    },
]

SUPERVISOR = (
    "Você coordena um time de especialistas em licitações e finanças públicas "
    "municipais para a LicitaEnterprisse.\n\n"
    "Como trabalhar:\n"
    "- Escolha o especialista certo para cada parte da pergunta. Pergunta composta "
    "pode exigir mais de um.\n"
    "- Quando o usuário anexar um arquivo (planilha, PDF, imagem), trabalhe sobre o "
    "conteúdo real dele.\n"
    "- Quando pedirem gráfico ou planilha de algo já levantado nesta conversa, "
    "reaproveite o resultado existente em vez de refazer a consulta.\n"
    "- Nunca invente número. Se o dado não existe, diga que não existe.\n"
    "- Responda em português do Brasil, direto ao ponto."
)


def auth(t):
    return {"Authorization": f"Bearer {t}"}


def main():
    with httpx.Client(base_url=B, timeout=300.0) as c:
        token = c.post("/api/auth/login", json={"email": EMAIL, "password": SENHA}).json()["token"]
        h = auth(token)

        servicos = c.get("/api/ai-services", headers=h).json()
        combo = next((s for s in servicos if s["name"] == "Combo: Work"), None)
        if combo is None:
            print("combo nao encontrado:", [s["name"] for s in servicos])
            return 1
        fontes = c.get("/api/datasources", headers=h).json()
        fonte = next(d for d in fontes if d["name"] == "lake_mindlab")

        templates = c.get("/api/templates", headers=h).json()
        tpl = next((t for t in templates if t["name"] == "Inteligência de Licitações"), None)
        if tpl is None:
            tpl = c.post(
                "/api/templates",
                json={
                    "name": "Inteligência de Licitações",
                    "description": "Editais, despesa, receita e contabilidade pública.",
                },
                headers=h,
            ).json()

        agentes = []
        for a in AGENTES:
            agentes.append(
                {
                    **a,
                    "ai_service_id": combo["id"],
                    "datasource_ids": [fonte["id"]],
                }
            )

        versao = c.post(
            f"/api/templates/{tpl['id']}/versions",
            json={
                "supervisor_prompt": SUPERVISOR,
                "ai_service_id": combo["id"],
                "max_steps": 18,
                "history_limit": 100,
                "compress_history": True,
                "agents": agentes,
                "datasource_ids": [fonte["id"]],
                "require_write_confirmation": True,
            },
            headers=h,
        )
        if versao.status_code >= 400:
            print("erro ao criar versao:", versao.status_code, versao.text[:600])
            return 1
        vid = versao.json()["id"]
        dep = c.post(
            f"/api/templates/{tpl['id']}/deploy", json={"version_id": vid}, headers=h
        )
        print("template:", tpl["id"], "| versao:", vid, "| deploy:", dep.status_code)
        print("agentes:", len(agentes))
        os.environ["TPL"] = tpl["id"]
        print(json.dumps({"template_id": tpl["id"], "combo_service": combo["id"]}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
