"""Exercita a plataforma com conversas longas e reais, sem mocar nada.

O que este harness procura não é "o agente respondeu": é se a mecânica por
baixo aguenta uma conversa de verdade — dezenas de turnos, arquivo anexado no
meio, artefato gerado a partir de outro artefato, código rodando em sandbox e
busca na web. São exatamente os pontos onde uma plataforma de agentes quebra
sem que a resposta pareça errada.

Cada conversa é uma sessão só (mesmo chat_id), porque o objetivo é ver o
comportamento acumulando contexto, não perguntas isoladas.

Uso:
    python scripts/qa_conversa_licita.py [numero-da-conversa]
"""

import json
import os
import sys
import time
from pathlib import Path

import httpx

# O console do Windows usa cp1252 e quebra em sinal de menos unicode, seta e
# emoji — que aparecem direto em resposta de modelo.
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

BACKEND = os.environ.get("REGRESSAO_BACKEND", "https://teste-ia-backend-x27vtpiida-uc.a.run.app")
EMAIL = os.environ.get("LICITA_EMAIL", "dono@licita.com")
SENHA = os.environ.get("LICITA_SENHA", "licita-senha-forte-123")
TEMPLATE = "Inteligência de Licitações"
_FIX = os.environ.get("LICITA_FIXTURES", "")
FIXTURES = Path(_FIX) if _FIX else None

# Valores que a planilha realmente contém. Se o agente responder outra coisa
# ao ler o anexo, é invenção — e é isso que o teste precisa pegar.
XLSX_TOTAL_EMPENHADO = 42_917_202.00
XLSX_PORTO_VELHO = 5_204_250.75
PDF_VALOR_TOTAL = "2.480.750"
PDF_NOTEBOOKS = "120"

relatorio: list[dict] = []


def registrar(conversa: str, turno: int, pergunta: str, r: dict, notas: str = "") -> None:
    relatorio.append(
        {
            "conversa": conversa,
            "turno": turno,
            "pergunta": pergunta[:120],
            "resposta": (r.get("reply") or "")[:400],
            "ferramentas": [t.get("tool") or t.get("name") for t in r.get("tools") or []],
            "agentes": [a.get("name") or a.get("agent") for a in (r.get("agents") or [])],
            "artefatos": [a.get("kind") or a.get("type") for a in r.get("artifacts") or []],
            "erro": r.get("error"),
            "ms": r.get("latency_ms"),
            "notas": notas,
        }
    )


def _consumir(response) -> dict:
    """Lê o SSE e devolve o que aconteceu de observável no turno.

    O fluxo manda o nome do evento numa linha (`event: tool`) e o corpo na
    seguinte (`data: {...}`). Ler só as linhas `data:` faz tudo virar dicionário
    sem tipo — e a conversa inteira parece vazia.
    """
    tools: list[dict] = []
    artifacts: list[dict] = []
    agents: list[dict] = []
    reply = ""
    erro = None
    chat_id = None
    evento = ""
    for linha in response.iter_lines():
        if linha.startswith("event: "):
            evento = linha[7:].strip()
        elif linha.startswith("data: "):
            try:
                dados = json.loads(linha[6:])
            except ValueError:
                continue
            if evento == "chat":
                chat_id = dados.get("chat_id", chat_id)
            elif evento == "tool":
                tools.append(dados)
            elif evento == "artifact":
                artifacts.append(dados)
            elif evento == "agent":
                agents.append(dados)
            elif evento == "done":
                reply = dados.get("text", "")
            elif evento == "error":
                erro = dados.get("detail") or dados
    return {
        "reply": reply,
        "tools": tools,
        "artifacts": artifacts,
        "agents": agents,
        "error": erro,
        "chat_id": chat_id,
    }


def enviar(
    client: httpx.Client,
    token: str,
    mensagem: str,
    template_id: str,
    chat_id: str | None = None,
    arquivo: Path | None = None,
) -> dict:
    inicio = time.monotonic()
    cabecalho = {"Authorization": f"Bearer {token}"}
    if arquivo is not None:
        dados = {"message": mensagem, "template_id": template_id}
        if chat_id:
            dados["chat_id"] = chat_id
        tipo = {
            ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            ".pdf": "application/pdf",
            ".png": "image/png",
        }.get(arquivo.suffix, "application/octet-stream")
        with arquivo.open("rb") as fh:
            with client.stream(
                "POST",
                "/api/chat/send",
                data=dados,
                files={"files": (arquivo.name, fh, tipo)},
                headers=cabecalho,
                timeout=600.0,
            ) as resposta:
                saida = _consumir(resposta)
    else:
        corpo = {"message": mensagem, "template_id": template_id}
        if chat_id:
            corpo["chat_id"] = chat_id
        with client.stream(
            "POST", "/api/chat/send", json=corpo, headers=cabecalho, timeout=600.0
        ) as resposta:
            saida = _consumir(resposta)
    saida["latency_ms"] = int((time.monotonic() - inicio) * 1000)
    return saida


def conversa(
    client: httpx.Client,
    token: str,
    template_id: str,
    nome: str,
    turnos: list,
) -> str | None:
    """Roda uma conversa inteira no mesmo chat e imprime o que aconteceu."""
    print(f"\n{'=' * 78}\n>> {nome}\n{'=' * 78}")
    chat_id = None
    for i, turno in enumerate(turnos, start=1):
        if isinstance(turno, tuple):
            pergunta, arquivo = turno
        else:
            pergunta, arquivo = turno, None
        r = enviar(client, token, pergunta, template_id, chat_id, arquivo)
        chat_id = r.get("chat_id") or chat_id
        ferramentas = [t.get("tool") or t.get("name") for t in r["tools"]]
        marca = "ERRO" if r["error"] else "ok  "
        anexo = f" [+{arquivo.name}]" if arquivo else ""
        print(f"  {marca} {i:>2}. {pergunta[:66]}{anexo}")
        if ferramentas:
            print(f"        tools: {ferramentas}")
        if r["artifacts"]:
            kinds = [a.get("kind") or a.get("type") for a in r["artifacts"]]
            print(f"        artefatos: {kinds}")
        if r["agents"]:
            print(f"        agentes: {[a.get('name') or a.get('agent') for a in r['agents']]}")
        if r["error"]:
            print(f"        ERRO: {json.dumps(r['error'], ensure_ascii=False)[:220]}")
        resposta = (r["reply"] or "").replace("\n", " ")[:150]
        print(f"        -> {resposta}")
        registrar(nome, i, pergunta, r)
    return chat_id


# --------------------------------------------------------------------------
# Roteiros
# --------------------------------------------------------------------------


def c1_dados_e_memoria(client, token, tpl):
    """Conversa longa só de dados: o supervisor tem que revezar especialistas."""
    return conversa(
        client,
        token,
        tpl,
        "C1 — dados públicos e memória (30 turnos)",
        [
            "Olá! Quero que você guarde uma informação importante: os clientes "
            "principais da LicitaEnterprisse são Porto Velho (RO), Campinas (SP) "
            "e o estado de Rondônia inteiro. Sempre priorize esses lugares.",
            "Quais fontes de dados você tem acesso?",
            "Liste as tabelas de licitação disponíveis.",
            "Quantas contratacoes do PNCP existem para Porto Velho?",
            "E para Campinas?",
            "Compare as duas: qual tem mais contratações?",
            "Qual o valor total estimado das contratações de Porto Velho em 2025?",
            "Mostre as 5 maiores contratações de Porto Velho por valor.",
            "Quais modalidades de contratação aparecem em Rondônia?",
            "Agora despesa: qual a despesa em educação de Porto Velho no ano mais recente?",
            "Compare essa despesa com a de Campinas.",
            "E a receita de Porto Velho no mesmo ano?",
            "Qual a proporção entre despesa em educação e receita total?",
            "Existe algum indicador do SIOPE sobre aplicação mínima em educação?",
            "O que esse indicador significa na prática?",
            "Porto Velho cumpriu o mínimo constitucional?",
            "E Campinas?",
            "Me traga os dados gerais do município de Porto Velho.",
            "Qual o código IBGE de Porto Velho?",
            "Quantos municípios existem em Rondônia?",
            "Liste os 5 municípios de Rondônia com maior despesa em educação.",
            "Faça um gráfico de barras dessa comparação.",
            "Exporte esses mesmos dados em planilha.",
            "Qual a evolução da despesa em educação de Porto Velho ano a ano?",
            "Faça um gráfico de linha dessa evolução.",
            "Qual foi o ano de maior despesa?",
            "Calcule a variação percentual entre o primeiro e o último ano.",
            "Existe algum ano em que a despesa caiu em relação ao anterior?",
            "Resumindo tudo que vimos: escreva um parágrafo executivo sobre Porto Velho.",
            "Quais eram mesmo os clientes principais que eu te falei no começo?",
        ],
    )


def c2_artefatos_encadeados(client, token, tpl, xlsx):
    """O teste central: artefato que nasce de outro artefato."""
    return conversa(
        client,
        token,
        tpl,
        "C2 — cadeia de artefatos a partir de planilha",
        [
            (
                "Segue nossa planilha de execução orçamentária. Me diga o que tem nela: "
                "quais colunas, quantas linhas e quais municípios aparecem.",
                xlsx,
            ),
            "Qual o total empenhado somando todas as linhas da planilha?",
            "E só de Porto Velho, quanto foi empenhado?",
            "Qual município teve o maior empenho no mês 3?",
            "Calcule, para cada município, a diferença entre empenhado e pago.",
            "Qual município tem a maior diferença proporcional entre empenhado e pago?",
            "Agora faça um gráfico de barras comparando o empenhado total por município.",
            "Faça também um gráfico de linha mostrando a evolução mensal de Campinas.",
            "Gere uma planilha nova contendo apenas os dados de Porto Velho, "
            "com uma coluna a mais chamada 'percentual_pago' = pago / empenhado.",
            "Na planilha que você acabou de gerar, qual foi o mês com maior percentual pago?",
            "Agora gere outra planilha consolidando os três municípios, "
            "com totais por município e uma linha de total geral.",
            "Confira: o total geral dessa última planilha bate com o total que você "
            "me falou no segundo turno desta conversa?",
            "Gere um PDF com um resumo executivo desses números.",
            "Compare os dados da minha planilha com a despesa em educação do SIOPE "
            "para Porto Velho. Os valores são da mesma ordem de grandeza?",
        ],
    )


def c3_pdf_e_imagem(client, token, tpl, pdf, imagem):
    return conversa(
        client,
        token,
        tpl,
        "C3 — leitura de PDF e de imagem",
        [
            ("Segue um edital. Me diga o objeto e o valor total estimado.", pdf),
            "Qual a data de abertura?",
            "Liste todos os itens licitados com quantidade e valor unitário.",
            "Quantos notebooks estão sendo licitados?",
            "Calcule o valor total só dos notebooks.",
            "Somando todos os itens, o valor bate com o total estimado do edital?",
            "Qual a multa por atraso prevista?",
            "Que documentos de habilitação são exigidos?",
            "Esse edital é de qual município? Isso combina com nossos clientes principais?",
            ("Agora veja esta imagem e me diga o que ela mostra.", imagem),
            "Quantos processos foram abertos em 2024 segundo a imagem?",
            "Qual ano teve mais processos?",
            "Houve queda em algum ano? De quanto?",
            "Faça um gráfico reproduzindo os dados dessa imagem.",
            "Gere uma planilha com os dados da imagem.",
        ],
    )


def c4_previsao(client, token, tpl):
    return conversa(
        client,
        token,
        tpl,
        "C4 — série temporal e previsão (ARIMA/SARIMA)",
        [
            "Traga a série histórica anual da despesa em educação de Porto Velho, "
            "do ano mais antigo disponível até o mais recente.",
            "Quantos pontos tem essa série?",
            "Faça um gráfico de linha dessa série.",
            "Agora projete os próximos 3 anos usando essa série.",
            "Qual modelo você usou para a projeção?",
            "Refaça a projeção usando ARIMA explicitamente, com execute_python "
            "e statsmodels. Mostre os parâmetros escolhidos.",
            "Agora tente SARIMA considerando sazonalidade. Compare com o ARIMA anterior.",
            "Qual dos dois modelos teve menor erro? Mostre a métrica.",
            "Plote a série histórica junto com a projeção num único gráfico.",
            "Exporte a série histórica mais a projeção em planilha.",
            "Faça a mesma projeção para Campinas e compare a tendência das duas cidades.",
            "Em qual das duas o crescimento projetado é maior, em termos percentuais?",
        ],
    )


def c5_python_e_web(client, token, tpl):
    return conversa(
        client,
        token,
        tpl,
        "C5 — sandbox Python pesado e busca na web",
        [
            "Use Python para gerar 10 mil números aleatórios com distribuição normal "
            "(média 100, desvio 15) e me diga média, mediana, desvio e os percentis 5 e 95.",
            "Agora rode um teste de normalidade nesses dados e me diga o resultado.",
            "Monte uma matriz 500x500 aleatória, calcule os autovalores e me diga "
            "o maior e o menor em módulo.",
            "Calcule os 500 primeiros números primos e some todos.",
            "Faça uma simulação de Monte Carlo com 100 mil iterações para estimar pi.",
            "Traga do banco a despesa em educação dos municípios de Rondônia no "
            "último ano e rode um clustering k-means com 3 grupos.",
            "Descreva o perfil de cada cluster.",
            "Plote os clusters num gráfico.",
            "Agora busque na internet: qual a legislação atual sobre o percentual "
            "mínimo de aplicação em educação pelos municípios?",
            "Busque também notícias recentes sobre licitações em Porto Velho.",
            "Compare o que você achou na web com os números que temos no banco.",
            "Existe alguma mudança recente na Lei 14.133 que afete nossos clientes?",
        ],
    )


def c6_pagamento(client, token, tpl):
    return conversa(
        client,
        token,
        tpl,
        "C6 — cobrança PIX e QR Code",
        [
            "Preciso cobrar a mensalidade da consultoria. Gere uma cobrança PIX "
            "de R$ 349,90 para o cliente Porto Velho.",
            "Me mostre o código copia e cola dessa cobrança.",
            "Tem QR Code? Me mostre a imagem.",
            "Essa cobrança já foi paga?",
            "Gere outra cobrança, agora de R$ 1.250,00 para Campinas.",
            "Confirme os valores das duas cobranças que você gerou.",
        ],
    )


def main() -> int:
    if FIXTURES is None or not FIXTURES.exists():
        print("defina LICITA_FIXTURES apontando para a pasta dos arquivos de teste")
        return 1
    xlsx = FIXTURES / "execucao_orcamentaria_2025.xlsx"
    pdf = FIXTURES / "edital_pregao_042_2025.pdf"
    imagem = FIXTURES / "painel_licitacoes.png"

    escolha = sys.argv[1] if len(sys.argv) > 1 else "todas"

    with httpx.Client(base_url=BACKEND, timeout=600.0) as client:
        token = client.post(
            "/api/auth/login", json={"email": EMAIL, "password": SENHA}
        ).json()["token"]
        templates = client.get(
            "/api/templates", headers={"Authorization": f"Bearer {token}"}
        ).json()
        tpl = next(t for t in templates if t["name"] == TEMPLATE)["id"]

        rodar = {
            "1": lambda: c1_dados_e_memoria(client, token, tpl),
            "2": lambda: c2_artefatos_encadeados(client, token, tpl, xlsx),
            "3": lambda: c3_pdf_e_imagem(client, token, tpl, pdf, imagem),
            "4": lambda: c4_previsao(client, token, tpl),
            "5": lambda: c5_python_e_web(client, token, tpl),
            "6": lambda: c6_pagamento(client, token, tpl),
        }
        if escolha == "todas":
            for chave in sorted(rodar):
                rodar[chave]()
        else:
            for chave in escolha.split(","):
                rodar[chave.strip()]()

    destino = Path(__file__).parent.parent / "docs" / "qa-conversa-licita.json"
    destino.write_text(
        json.dumps(relatorio, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    turnos = len(relatorio)
    erros = [r for r in relatorio if r["erro"]]
    sem_resposta = [r for r in relatorio if not r["resposta"].strip() and not r["erro"]]
    print(f"\n{'=' * 78}")
    print(f"turnos: {turnos} | com erro: {len(erros)} | sem resposta: {len(sem_resposta)}")
    for r in erros:
        detalhe = json.dumps(r["erro"], ensure_ascii=False)[:200]
        print(f"  ERRO {r['conversa']} t{r['turno']}: {detalhe}")
    for r in sem_resposta:
        print(f"  VAZIO {r['conversa']} t{r['turno']}: {r['pergunta'][:70]}")
    print(f"relatório: {destino}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
