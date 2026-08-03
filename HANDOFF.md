# Handoff — QA de conversação, correções no core e omnichannel

Estado em 03/08/2026. Documento de continuidade: quem pegar esta trilha lê isto
primeiro e atualiza a seção **Onde parei** ao terminar.

O objetivo declarado deste QA é deixar a plataforma pronta para instalar em um
cliente. Não há clientes ainda: **produção é o nosso ambiente de QA**, e isso é
decisão consciente do dono (03/08/2026), não descuido. O critério aqui não é "o
harness ficou verde": é "a coisa funciona quando alguém usa". Essa distinção
apareceu literalmente — ver 6.1.

Atualizacao de 03/08/2026, fim da rodada: a spec unificada
`docs/specs/pendencias-antes-do-primeiro-cliente.md` virou a fonte principal do
que falta. O deploy manual fatiado foi feito no commit `7118a62`; depois o
primeiro push/CI-CD real foi corrigido ate ficar verde no commit `8a67ba3`.
Kernel, backend, migracao 0023, readiness, UI de template, QA C1/C2/C5 e smokes
Chatwoot foram revalidados contra producao. O que continua pendente antes do
primeiro cliente: limpeza das 215 memorias de QA, guardrails novos de runtime,
capacidade/pool, segredos no banco e decisoes do dono.

---

## 1. Ambiente

| Item | Valor |
|---|---|
| Empresa | `LicitaEnterprisse` / chave `licita-enterprisse` |
| `tenant_id` | `31445557-8561-4b27-804d-0129a72b467d` |
| Usuário | `dono@licita.com` / `licita-senha-forte-123` |
| Instância de IA | `https://ia-licita-enterprisse.rangeltech.net` (senha `123456`, veio do clone) |
| Fonte de dados | `lake_mindlab` → BigQuery `mi-prd-lake.semantic_zone` |
| Template | `Inteligência de Licitações` (`e79281d2-d6a5-41ee-97b4-45b1f4632863`), versão 5 |
| Backend | `https://teste-ia-backend-x27vtpiida-uc.a.run.app` |
| Kernel | `https://teste-ia-kernel-x27vtpiida-uc.a.run.app` (revisão `00032` / imagem `8a67ba3`) |
| Chatwoot | `chatwoot-web` / `chatwoot-worker` / `chatwoot-bridge` (Cloud Run, us-central1) |

### Deploy

`./infra/deploy.sh kernel|backend|all`. **Antes de rodar, confira a conta:**

```bash
gcloud config get-value account   # tem de ser devlake@eduk-prd-lake...
```

Trabalhar com `bq` no lake troca a conta global do gcloud para
`devlakemi@mi-prd-lake`, e o deploy morre com `PERMISSION_DENIED` — aconteceu
duas vezes nesta trilha, e é a razão do CI/CD da seção 4.

### Harness de conversa

```bash
LICITA_FIXTURES=<pasta dos fixtures> python scripts/qa_conversa_licita.py [1..6|todas]
```

Fixtures com números escolhidos a dedo, para que invenção do modelo apareça:
total empenhado **R$ 42.917.202,00**, Porto Velho **R$ 5.204.250,75**, edital com
**120 notebooks** / **R$ 2.480.750,00**, imagem com **310 processos em 2024**.

### Scripts de apoio

| Caminho | O que faz |
|---|---|
| `scripts/provar_qr_pix.py` | Prova que o QR na tela é o código que o banco cobra (baixa, decodifica, compara, cancela) |
| `scripts/declarar_tabelas_licita.py` | Declara as 15 tabelas usadas na fonte |
| `scripts/credencial_pix_qa.py` | Cadastra a credencial do Mercado Pago; imprime só os 4 últimos caracteres |
| `scripts/cancelar_cobrancas_qa.py` | Cancela no gateway as cobranças pendentes do QA |
| `scripts/medir_repeticao_sql.py` | Mede consultas repetidas por turno e a origem (cache/banco/erro) |

`provar_qr_pix.py` precisa de `opencv-python-headless` — instalado no venv do
kernel, **não** está em `requirements`. Só o script de prova depende dele.

---

## 2. Credencial de pagamento — leia antes de rodar C6

A única credencial do Mercado Pago disponível é de **produção** (prefixo
`APP_USR-`; sandbox seria `TEST-`). Toda cobrança gerada pelo QA é **pagável de
verdade** na conta 115691672.

Regras em vigor, decididas com o dono:

- valor de teste é **R$ 0,01**;
- ao terminar, rodar `MP_SECRET_FILE=<arquivo> python scripts/cancelar_cobrancas_qa.py`;
- o token vem de `MP_SECRET_FILE` (`C:/Users/lucas.rangel/Desktop/loki/.secrets/mercado_pago.json`),
  nunca do código, e nunca é impresso além dos 4 últimos caracteres;
- cobrança **paga** não é estornada automaticamente — o script recusa e avisa.

Nesta sessão foram criadas 16 cobranças de R$ 0,01. **Todas canceladas, nenhuma
paga.** Confira antes de encerrar qualquer sessão futura.

---

## 3. Defeitos do core corrigidos (todos com teste)

| # | Defeito | Onde |
|---|---|---|
| 3.1 | Turno sem teto real | `kernel/app/runs.py`, `config.py` |
| 3.2 | Catálogo de tabelas truncado em silêncio | `kernel/app/datasources.py` |
| 3.3 | Corte de histórico devolvia a conversa inteira | `kernel/app/graph.py` |
| 3.4 | Consultar cobrança PIX criava outra cobrança | `kernel/app/tools.py` |
| 3.5 | Especialista pedia confirmação que nunca chegava | `kernel/app/graph.py` |
| 3.6 | Artefato exibido, agente dizendo que não sabe exibir | `kernel/app/graph.py`, `tools.py` |
| 3.7 | Retorno de ferramenta sem teto no prompt | `kernel/app/graph.py`, migração 0023 |

### 3.1 O turno não tinha teto

`turn_timeout_seconds` media **silêncio entre eventos**, não a duração do turno.
No campo: C4 turno 1 morreu em exatos 600 s sem nenhuma mensagem — quem cortou
foi o Cloud Run, e o cliente recebeu conexão cortada em vez do evento de timeout.
Agora `turn_total_timeout_seconds` = 540 s, **abaixo** dos 600 s da borda.

### 3.2 O catálogo de tabelas era truncado sem avisar

180 tabelas, cap de 50, SIOPE nas posições 75–80: o modelo dizia "não existe essa
base" sobre dados que existiam. Agora as demais vão listadas com "colunas não
listadas aqui — consulte INFORMATION_SCHEMA.COLUMNS", e a fonte aceita allowlist
(`config.tables`).

### 3.3 Corte de histórico com limite 1 devolvia tudo

`limite // 2` dava 0 e `mensagens[-0:]` é a lista inteira. Piso de 1.

### 3.4 "Me mostre o código de novo" criava uma cobrança nova

`check_payment_status` devolvia só status e valor. Como só `generate_pix_charge`
produzia código, pedir o código de novo só tinha um caminho: emitir outra
cobrança pagável — no QA isso gerou uma terceira cobrança só para exibir a
primeira. Agora a consulta devolve `pix_copia_e_cola` e republica o QR, **só
enquanto pendente** (reexibir QR de cobrança liquidada convida um segundo
pagamento).

### 3.5 O especialista pedia confirmação a quem não podia ouvi-lo

O especialista recebe apenas a `task`; **nunca** vê a conversa. O prompt dele
dizia "confirme o valor com o usuário antes de emitir" — insatisfazível por
construção. O usuário confirmou e o agente perguntou de novo por quatro turnos.

Corrigido no kernel, não no template: quem monta um agente não deveria precisar
saber que especialista é cego para histórico. O kernel avisa o especialista de
que ele é ferramenta, não interlocutor, **e** manda o supervisor carregar a
confirmação já colhida para dentro da tarefa. Só calar o especialista faria a
cobrança sair sem ninguém ter confirmado.

### 3.6 O QR aparecia na tela e o agente dizia não saber mostrar imagem

Dois problemas em série: o kernel só emite o evento `artifact` quando acha um
`artifact_id` **no retorno da tool** (o PIX publicava e não devolvia o
descriptor); e, corrigido isso, nem especialista nem supervisor veem eventos de
stream. Agora todo artefato publicado é anunciado de volta na conversa do próprio
especialista (**antes** de ele redigir) e de novo ao supervisor. Vale para
gráfico e planilha também.

### 3.7 O retorno de ferramenta entrava inteiro no prompt

Medido numa conversa de 30 turnos: o especialista `despesas` acumulou **889 mil**
tokens de prompt contra 10 mil do supervisor. O corte de histórico ataca a parte
menor. Agora `tool_output_limit` (padrão 24k caracteres, migração 0023, campo no
editor de template) corta **depois** de publicar o artefato e registrar o trace —
limita só o prompt. O aviso do corte aponta o `artifact_id` e proíbe somar sobre
o trecho: truncar em silêncio é como o modelo soma meia coluna e chama de total.

### Defeitos de sessões anteriores, já em produção

`<think>` vazando; 45 consultas idênticas num turno (cache de leitura por turno);
gráfico do sandbox sumindo em silêncio; memória inflando (215 memórias em ~60
turnos, marca de leitura por conversa).

---

## 4. CI/CD — deploy saiu da mão

Job `deploy` no `.github/workflows/ci.yml`: só no `main`, só depois de backend,
kernel e frontend passarem. Autenticação por **Workload Identity Federation**,
sem chave de service account em secret. O provedor OIDC tem condição de atributo
presa a `LucasRangelSSouza/agent-platform` — fork que apresente o mesmo provedor
é recusado.

Criado no GCP nesta sessão (registrado em `infra/ci-deploy-setup.sh`):

- pool `github-actions` + provider OIDC `github`;
- SA `github-deployer@eduk-prd-lake` com `run.admin`,
  `cloudbuild.builds.editor`, `artifactregistry.writer`, `storage.admin`,
  `logging.viewer`;
- `serviceAccountUser` do deployer sobre `devlake@eduk-prd-lake`.

**Nunca foi exercitado**: até o momento deste handoff nada foi pushado, então o
job nunca rodou. Ver os perigos na seção 8.

---

## 5. Chatwoot — o que foi verificado, e o que foi feito

| Pergunta | Resposta verificada |
|---|---|
| Por que Instagram não aparece / Messenger bloqueado | Os 3 segredos Meta **não existem** no Secret Manager. O deploy só injeta `FB_*`/`IG_*` se existirem; sem eles o Chatwoot esconde os canais |
| Tem conector de TikTok? | Não existe em nenhuma edição do Chatwoot. Caminho seria `Channel::Api` com conector nosso na ponte |
| WhatsApp é API oficial? | **Não.** A ponte só aceita `provider="wapi"` (`pattern="^(wapi)$"`, `api_base=https://api.w-api.app/v1`); as 15 inboxes são `Channel::Api` chamadas `wapi:inst-*` |
| Dá para ocultar as opções pagas? | Parcialmente — ver abaixo |

- Plano no banco já é `community` (`installation_configs`).
- Itens com feature flag (Audit Logs, SLA, Captain) somem desligando a feature da
  conta no Super Admin.
- **Custom Roles não**: é `isEnterpriseOnly: true` sem feature flag, e a imagem
  oficial embute o código enterprise. Só sumiria alterando o build — fork de
  facto, contra a decisão `0002-branding-e-planos.md`.
- `INSTALLATION_NAME` ainda é "Chatwoot". Trocando, o Chatwoot esconde os itens
  promocionais dele (`alwaysVisibleOnChatwootInstances`). Ganho barato, não feito.

### Português definitivo — feito

`DEFAULT_LOCALE=pt_BR` só decide o idioma de conta **nova**; as 10 contas
existentes estavam em inglês. Ajustei as 9 que faltavam direto no banco e
adicionei um passo idempotente ao deploy (`./infra/deploy.sh locale`, script
`scripts/ops/locale_pt_br.rb`) — sem ele o próximo deploy voltaria a esquecer.

### Paleta — não feito, e por quê

O dashboard do v3.16 é Tailwind compilado, sem variável de marca exposta, e não
há hook de CSS custom (só widget e help center têm). Herdar o nosso `#4f46e5`
significa mexer nos assets do build — fork na prática, com custo de merge a cada
upgrade. Decisão pendente do dono.

---

## 6. Resultados

| Conversa | Turnos | Erros | Observação |
|---|---|---|---|
| C1 — 30 turnos de dados públicos | 30/30 | 0 | Reexecução pós-deploy sem turno vazio |
| C2 — cadeia de artefatos | 14/14 | 0 | Números críticos batem; alguns pedidos viraram texto, não artifact |
| C3 — PDF e imagem | 15/15 | 0 | |
| C4 — previsão | 12/12 | 0 | |
| C5 — sandbox e web | 12/12 | 0 | Python, SQL, web_search e call_http_api chamados |
| C6 — PIX | 7/7 | 0 | 2 cobranças pedidas, 2 criadas |

Suítes: kernel **47 unitários + 60 integração**; backend **6 + 120**; ponte
**10 + 10**. Todas verdes.

Números conferidos fora do agente: R$ 42.917.202,00 / R$ 5.204.250,75 / 84,25%
contra o fixture; 500º primo 3571 e soma 824693 por cálculo próprio; a faixa
2021–2024 do SIOPE direto no BigQuery.

### PIX provado de verdade

`scripts/provar_qr_pix.py` baixou o artefato pela API, decodificou o QR com
OpenCV e comparou com o copia-e-cola que o gateway guarda para aquele
`payment_id`:

```
imagem: 2823 bytes
QR decodificado: 00020126580014br.gov.bcb.pix0136...
confere com o copia-e-cola do gateway
reexibir usou: ['check_payment_status'] | cobranças: 16 -> 16
```

### 6.1 O aviso que vale mais que os números

**C6 marcou 6/6 verde numa rodada em que não criou cobrança nenhuma.** O agente
exigia confirmação, o roteiro nunca confirmava, e o harness só olhava "houve
resposta sem erro". Depois disso, C6 passou 7/7 com o QR publicado — e a imagem
podia ser um PNG preto de 1×1, porque o harness só checava `kind == "image"`.

Harness verde não é funcionalidade provada. Ao ler resultado de QA aqui, confira
a lista de ferramentas chamadas em `docs/qa-conversa-licita.json`, não a contagem
de turnos.

---

## 7. Onde parei

- [x] 7 defeitos do core corrigidos, com teste
- [x] Kernel deployado até a revisão `00032` no commit `8a67ba3`
- [x] Backend deployado até a revisão `00047` no commit `8a67ba3`
- [x] `/health/ready` 200 em produção
- [x] Migração 0023 aplicada e coluna `template_versions.tool_output_limit` conferida
- [x] C1, C2, C3, C4, C5, C6 verdes em rodadas de QA registradas
- [x] QR do PIX provado de ponta a ponta; todas as cobranças canceladas
- [x] CI/CD com deploy por Workload Identity verde no run `30860519195`
- [x] Chatwoot em português nas 10 contas
- [x] Spec de segredos escrita: `docs/specs/segredos-no-banco.md`
- [x] Smokes do Chatwoot contra produção (`scripts/smoke/omnichannel_e2e.py` e
      `scripts/smoke/atendimento_negocio.py`)
- [ ] Implementar a spec de segredos (fases 1 a 5)
- [ ] 215 memórias antigas no tenant de teste:
      `DELETE FROM memories WHERE tenant_id = '31445557-8561-4b27-804d-0129a72b467d'`

---

## 8. Perigos e o que ainda não foi testado

Esta seção é a mais importante do documento. Nada aqui é hipótese confortável.

### 8.1 O teto de saída (3.7) já está em produção

O kernel em produção está na revisão `00032` e o backend na `00047`, ambos com
imagem `8a67ba3`. C1, C2 e C5 rodaram depois do deploy sem erro e sem turno
vazio. O risco remanescente não é "código não deployado"; é qualidade de agente:
em C2, alguns pedidos de gráfico/planilha/PDF foram respondidos em texto, não
como artifact real.

### 8.2 O job de deploy do CI nunca rodou

WIF configurado, job escrito, zero execuções. Falha típica na primeira vez:
`iam.serviceAccounts.actAs` faltando, ou o `deploy.sh` esbarrar em algo que só
existe na máquina local. **O primeiro push vai ser o teste** — acompanhe a
execução em vez de assumir que passou.

### 8.3 A migração 0023 já rodou em produção

Conferido direto no banco: `0023_teto_de_saida_de_ferramenta.sql` existe em
`schema_migrations` e `template_versions.tool_output_limit` existe como
`integer NOT NULL DEFAULT 24000`.

### 8.4 Turno vazio no C1 (t11) não reapareceu

"Compare essa despesa com a de Campinas" voltou **sem resposta e sem erro**. Não
foi investigado na sessão antiga. Na reexecução pós-deploy de 03/08/2026, C1
rodou 30 turnos com 0 erros e 0 turnos sem resposta. Ainda falta o guardrail
formal: se um supervisor terminar sem texto, o stream deve emitir `error`
explícito.

### 8.5 A conversa gasta muito mais do que deveria

C1 turno 25 disparou **30 chamadas de ferramenta** e publicou **26 datasets** num
único turno. Nenhum erro — e um custo desproporcional para uma pergunta de
gráfico de linha. O cache de leitura pega só consulta idêntica; consultas
ligeiramente diferentes passam. Não há teto de chamadas por turno, só de rodadas
por especialista.

### 8.6 O WhatsApp é não oficial

W-API, não Cloud API da Meta. Número sujeito a ban sem aviso, sem SLA e sem
garantia contratual. Instalar num cliente com este canal é assumir que o canal
dele pode cair e não haver a quem recorrer. Decisão de produto, não técnica — mas
precisa ser decisão consciente antes do primeiro cliente.

### 8.7 A senha `123456` continua exposta na internet

A instância de IA da LicitaEnterprisse (`ia-licita-enterprisse.rangeltech.net`)
herdou a senha do clone. A instância pessoal (`9route.rangeltech.net`) usa a mesma
senha e guarda três sessões OAuth do Claude. Isto não é dívida técnica: é uma
porta aberta.

### 8.8 Não há ambiente de homologação

Todo este QA rodou contra produção, criando empresa, chats, memórias, artefatos e
cobranças pagáveis reais. Enquanto não há clientes é aceitável e foi decidido
assim. **No dia em que houver o primeiro cliente, deixa de ser.**

### 8.9 O backup vai virar o cofre

Assim que a spec de segredos (`docs/specs/segredos-no-banco.md`) for implementada,
o backup do Postgres passa a conter todos os segredos cifrados. A chave mestra
não pode estar no mesmo backup, e o acesso ao backup precisa da mesma restrição
do cofre. Hoje isso não está tratado.

### 8.10 O que nenhum teste cobre

- turno que termina sem texto (8.4);
- número de chamadas de ferramenta por turno (8.5);
- o caminho Chatwoot → ponte → kernel → resposta passou contra produção em
  03/08/2026, mas deve continuar no smoke obrigatório de release;
- sincronização de segredo com o Chatwoot (ainda não existe);
- rotação da chave mestra de cifragem;
- o que acontece quando o gateway de pagamento fica fora do ar no meio de uma
  cobrança.

---

## 9. Janela de contexto — o que sobrou

O corte e o resumo de histórico existem (`history_limit`, `compress_history`,
migração 0022) e o teto de saída de ferramenta agora também (0023). O que **não**
existe: contagem por token. O limite de histórico conta **mensagens**, então uma
conversa de 10 mensagens gigantes ainda estoura o modelo. Ninguém mediu onde
quebra.
