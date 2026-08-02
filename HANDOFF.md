# Handoff — QA de conversação (LicitaEnterprisse) e correções no core

Estado em 02/08/2026. Documento de continuidade: quem pegar esta trilha lê isto
primeiro e atualiza a seção **Onde parei** ao terminar.

---

## 1. O que foi pedido

Testar a mecânica de conversação a fundo, com um caso realista, e corrigir o
que aparecesse:

- empresa nova `LicitaEnterprisse` conectada ao BigQuery (PNCP + SIOPE);
- template grande, com vários especialistas (edital, contabilidade, despesa,
  receita, dados gerais, itens);
- memória com os clientes principais (Porto Velho, Campinas, Rondônia);
- anexo de xlsx, PDF e imagem;
- **encadeamento de artefatos**: mandar um Excel, perguntar o que tem nele,
  pedir gráfico dele, e depois outro Excel derivado do primeiro;
- previsão com ARIMA/SARIMA, sandbox Python, busca na web, QR Code de PIX;
- 5–6 conversas longas (~30 turnos) para ver o comportamento acumulando
  contexto;
- copiar os providers do 9Router pessoal para a instância do cliente.

Pedido adicional, feito no meio e **ainda não executado**: avaliar janela de
contexto e compressão de histórico (seção 7).

---

## 2. Ambiente montado

| Item | Valor |
|---|---|
| Empresa | `LicitaEnterprisse` / chave `licita-enterprisse` |
| `tenant_id` | `31445557-8561-4b27-804d-0129a72b467d` |
| Usuário | `dono@licita.com` / `licita-senha-forte-123` |
| Instância de IA | `https://ia-licita-enterprisse.rangeltech.net` (senha `123456`, veio do clone) |
| Container na VPS | `9router-licita-enterprisse` |
| Combo | `Work` → serviço de IA `Combo: Work` (`a47ca5da-2b27-41ce-a53e-8376da9a6428`) |
| Fonte de dados | `lake_mindlab` → BigQuery `mi-prd-lake.semantic_zone` (`b6f4f126-5c45-46c1-ad20-30a4e2c6439a`) |
| Template | `Inteligência de Licitações` (`e79281d2-d6a5-41ee-97b4-45b1f4632863`), 8 especialistas |
| Backend | `https://teste-ia-backend-x27vtpiida-uc.a.run.app` |

### Como o combo foi copiado

O banco SQLite da instância pessoal foi clonado para o volume da instância nova:

```bash
ssh -i ~/.ssh/vps_rt_infra_ed25519_v2 root@66.94.101.153
docker stop 9router-licita-enterprisse
cp -a /opt/platform/data/9router/. /opt/platform/data/9router-licita-enterprisse/
docker start 9router-licita-enterprisse
```

**Duas consequências que custaram tempo e vão repetir se alguém clonar de
novo:** o clone leva junto a senha administrativa (virou `123456`, a da
instância pessoal) e apaga a chave de consumo criada no provisionamento. Foi
preciso criar uma chave nova (`POST /api/keys`) e reregistrar a instância em
`PUT /api/ai-router/instancias`.

As três contas Claude vieram com status `unavailable` (limite 429 na conta
pessoal). O combo continuou respondendo por Codex e Gemini — que é exatamente
o comportamento que o revezamento deveria ter.

---

## 3. Arquivos criados ou alterados

### Harness e apoio

| Caminho | O que é |
|---|---|
| `scripts/qa_conversa_licita.py` | As 6 conversas. `python scripts/qa_conversa_licita.py [1..6\|todas]` |
| `docs/qa-conversa-licita.json` | Saída do último harness executado (é sobrescrito) |
| `scripts/gerar_roteiro_qa.py` | Gerador do roteiro de QA manual em .docx |
| `docs/Roteiro-QA-Plataforma.docx` | Roteiro para o QA júnior, 74 casos |

O harness precisa de `LICITA_FIXTURES` apontando para a pasta com os arquivos
de teste. Os fixtures são gerados por um script de scratchpad (planilha, PDF e
imagem com números escolhidos a dedo, para que invenção do modelo apareça):

```
scratchpad/fixtures.py  ->  execucao_orcamentaria_2025.xlsx
                            edital_pregao_042_2025.pdf
                            painel_licitacoes.png
```

Valores que os testes conferem: total empenhado **R$ 42.917.202,00**, Porto
Velho **R$ 5.204.250,75**, edital com **120 notebooks** e total estimado
**R$ 2.480.750,00**, imagem com **310 processos em 2024**.

### Correções no core (todas com teste)

| Caminho | Correção |
|---|---|
| `kernel/app/providers.py` | `ReasoningFilter` — remove `<think>…</think>` do texto |
| `kernel/tests/test_reasoning_filter.py` | 10 casos, incluindo tag partida entre chunks |
| `kernel/app/tools.py` | cache de leitura por turno + aviso de gráfico no sandbox |
| `kernel/tests/test_cache_de_leitura.py` | 6 casos, incluindo escrita invalidando leitura |
| `kernel/app/memories.py` | marca de leitura por conversa |
| `backend/migrations/0021_memory_watermark.sql` | tabela `memory_extraction_state` |

---

## 4. O que os testes provaram

### C2 — encadeamento de artefatos: **14/14, o ponto central do pedido**

O Excel foi lido de verdade e a cadeia inteira se sustentou:

1. leu a planilha e descreveu colunas, linhas e municípios;
2. somou **R$ 42.917.202,00** — bate com o arquivo;
3. Porto Velho **R$ 5.204.250,75** — bate;
4. maior empenho do mês 3: Campinas **R$ 9.530.000,00** — bate;
5. maior diferença proporcional: Porto Velho **20,07%** — confere no braço;
6. gerou gráfico de barras e de linha (`generate_chart`, artefato `chart`);
7. gerou **planilha nova derivada** com coluna calculada `percentual_pago`;
8. respondeu sobre a planilha que ele mesmo criou (abril, **84,25%** — confere);
9. gerou planilha consolidada e **conferiu o total contra o que tinha dito no
   turno 2**;
10. gerou PDF;
11. cruzou a planilha do usuário com o SIOPE e disse que a ordem de grandeza
    **não** bate — em vez de forçar uma conclusão.

### C1 — 30 turnos de dados públicos: **30/30**

Supervisor revezou entre `despesas`, `receitas`, `gerais`, `itens`, `editais`.
No turno 30, perguntado sobre os clientes principais, respondeu **Porto Velho,
Campinas e Rondônia** — a memória atravessou 30 turnos.

### C3 — PDF e imagem: **15/15**

PDF: objeto, data, itens, 120 notebooks, R$ 504.000, multa de 0,5%/dia. No
turno 6 percebeu que a soma dos itens **não** fecha com o total do edital, em
vez de forçar. Imagem: leu 310 processos em 2024 e a queda de 45 em 2025.

### C4 e C5 — previsão e sandbox: **falharam por erro meu de configuração**

O agente respondeu *"não existe a ferramenta `execute_python` disponível"*.
Está certo: no template eu dei `execute_python`, `generate_forecast`,
`export_xlsx` e `generate_chart` **apenas** ao especialista `analista`, e o
supervisor roteou para `despesas`, que não as tem. O supervisor **não compôs os
dois especialistas** — levou a pergunta inteira para um só.

Isso é dois achados, não um:

- **meu erro**: o template precisa dar as ferramentas de saída aos
  especialistas de dados, ou o supervisor precisa ser instruído a encadear;
- **achado de produto, real**: com 8 especialistas, o supervisor tende a
  escolher um e não combinar. Vale medir e, se confirmar, tratar no prompt do
  supervisor.

### C6 — PIX: **falhou por erro meu de configuração**

Nenhum agente do template recebeu `generate_pix_charge` /
`check_payment_status`, então o agente respondeu que não tem a ferramenta —
corretamente. A credencial de sandbox foi cadastrada, mas com **token falso**:
não havia credencial real do Mercado Pago disponível.

**Pendência honesta: QR Code de PIX não foi provado.** Provar exige token de
sandbox real do Mercado Pago. O que já está provado em outro tenant (super QA
da loja) é que a tool é acionada e que a credencial não volta em claro.

---

## 5. Defeitos encontrados no core, e o que foi feito

### 5.1 O rascunho do modelo vazava para o usuário

A resposta chegava com `<think></think>` no meio. É o rascunho de modelos de
raciocínio. Com combo isso deixa de ser caso raro: um provedor do rodízio emite
a tag e outro não, então o mesmo template vaza ou não dependendo de qual conta
atendeu.

Corrigido em `kernel/app/providers.py`. O streaming é o que torna isso mais que
um `replace`: a tag chega partida entre pedaços, então um pedaço terminando em
`<thi` fica retido até o próximo provar o que é. Bloco sem fechamento é
descartado — mostrar o rascunho é pior que não mostrar nada.

### 5.2 O agente repetia a mesma consulta muitas vezes

Medido: **45 consultas num único turno**, várias byte a byte idênticas,
`describe_datasources` chamado 5 vezes, turno de **9 minutos**. Nada no retorno
dizia que aquilo já tinha sido perguntado.

Corrigido em `kernel/app/tools.py`: leituras são lembradas durante o turno e a
repetição devolve o resultado guardado **com um aviso**. O aviso importa tanto
quanto o cache — devolver as mesmas linhas em silêncio só convida a repetir de
novo. Escrita limpa o cache, para que leitura depois de escrita nunca sirva
dado velho.

### 5.3 Gráfico desenhado no sandbox sumia, e o agente dizia que deu certo

O sandbox publica `dataset` e `document` — imagem, não. O agente plotou com
matplotlib, o desenho se perdeu e ele anunciou *"gráfico gerado com sucesso"*.
O usuário ficava sem gráfico e sem saber.

Corrigido: quando o código usa biblioteca de gráfico e não publica dataset, o
retorno traz um aviso explícito apontando para `generate_chart`.

### 5.4 Memória inflando

**215 memórias para ~60 turnos**, muitas dizendo a mesma coisa com outra
redação. Causa: a extração roda ao fim de cada turno sobre uma janela de 8
mensagens, que é maior que um turno — o mesmo trecho voltava a ser lido a cada
turno seguinte e virava fato novo com outra redação, que a deduplicação por
similaridade (0,92) não pega.

Corrigido com uma marca de leitura por conversa
(`memory_extraction_state`). Cada troca passa a ser extraída uma vez.

**Não feito, e vale decidir:** as 215 memórias antigas continuam no banco do
tenant de teste. Limpar é `DELETE FROM memories WHERE tenant_id = '3144...'`.

---

## 6. Onde parei

- [x] Empresa, instância, combo clonado, BigQuery, template de 8 especialistas
- [x] C1 (30/30), C2 (14/14), C3 (15/15)
- [x] 4 defeitos do core corrigidos, com teste, deployados em produção
- [x] Kernel: 17 testes passando; backend: 118 passando (antes das últimas mudanças)
- [ ] **C4, C5 e C6 precisam rodar de novo** depois de corrigir o template
- [ ] **Não commitado/pushado**: `docs/qa-conversa-licita.json` e este arquivo.
      Havia **2 commits locais à frente do origin/main** no momento em que
      escrevi isto — conferir com `git status` antes de continuar
- [ ] Avaliação de janela de contexto e compressão (seção 7)
- [ ] QR Code de PIX (falta token sandbox real do Mercado Pago)

### Próximo passo concreto

1. Editar o template para dar `execute_python`, `generate_forecast`,
   `generate_chart`, `export_xlsx`, `generate_pix_charge` e
   `check_payment_status` aos especialistas de dados — ou instruir o supervisor
   a compor especialistas. O template é montado em
   `scratchpad/licita_setup.py`; vale movê-lo para `scripts/`.
2. `python scripts/qa_conversa_licita.py 4,5,6`.
3. Medir se o cache de leitura reduziu o número de consultas por turno
   (comparar com os 45 registrados em `docs/qa-conversa-licita.json`).

---

## 7. Janela de contexto e compressão — avaliação inicial

Pedido no meio da sessão, ainda não tratado. O levantamento já feito:

**Não existe compressão nem corte de histórico.** `_history_messages` em
`kernel/app/graph.py` monta o prompt assim:

```python
for m in state["messages"]:
    messages.append({"role": role, "content": m.content})
```

Todas as mensagens, sempre. Não há sumarização, nem janela, nem contagem de
tokens. Consequências:

- o custo por turno cresce junto com a conversa;
- uma conversa longa o bastante bate no limite do modelo e **falha**, em vez de
  degradar comprimindo o começo;
- o limite efetivo depende do modelo que o combo escolher naquele turno — o que
  significa que a mesma conversa pode falhar ou não dependendo da conta que
  atendeu.

Medido na conversa de 30 turnos (`chat_id b9749376-…`), por especialista:

| Agente | Chamadas | Maior prompt | Total de prompt |
|---|---:|---:|---:|
| despesas | 75 | 28.350 | 889.253 |
| pesquisador | 24 | 42.021 | 463.944 |
| supervisor | 48 | 10.796 | 264.505 |
| receitas | 19 | 15.733 | 195.975 |
| gerais | 13 | 16.961 | 155.386 |
| itens | 12 | 28.090 | 146.583 |

Duas leituras importantes:

1. O supervisor chegou a **10.796 tokens** de prompt em 30 turnos — ainda longe
   do limite, mas crescendo de forma linear e sem freio.
2. O gasto grande **não está no histórico do supervisor**: está nos
   especialistas, que recebem o resultado das ferramentas. `despesas` sozinho
   consumiu 889 mil tokens de prompt. Ou seja, comprimir só o histórico da
   conversa atacaria a parte menor do problema.

**Recomendação para a próxima sessão** (não implementar sem decidir):

- medir onde o limite realmente é atingido, forçando uma conversa até quebrar;
- se for tratar, tratar as duas frentes — sumarizar o histórico antigo do
  supervisor **e** limitar o que volta de uma ferramenta para o especialista
  (hoje o dataset inteiro pode voltar);
- o cache de leitura da seção 5.2 já reduz parte disso, porque consulta
  repetida deixa de reinserir o mesmo resultado no contexto.

---

## 8. Avisos

- A senha da instância de IA da LicitaEnterprisse é **`123456`**, herdada do
  clone, e a instância está exposta na internet. Trocar antes de qualquer uso
  que não seja teste.
- A instância pessoal (`9route.rangeltech.net`) usa a mesma senha e guarda três
  sessões OAuth do Claude.
- As conversas de teste rodaram **contra produção** e criaram empresa, chats,
  memórias e artefatos reais. Vale um ambiente de homologação.
