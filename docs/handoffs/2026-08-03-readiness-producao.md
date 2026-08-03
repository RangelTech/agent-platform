# Handoff - readiness de producao antes do primeiro cliente

Data: 03/08/2026  
Repo: `C:\Users\lucas.rangel\Desktop\claude\agent llm\agent-platform`  
Branch: `main`  
Commit final em producao: `0bbe989`  
Status: deploy e CI/CD verdes; produto funcionando em producao; ainda ha
pendencias de seguranca/operacao antes do primeiro cliente.

## 1. Resumo executivo

Esta rodada consolidou a spec unificada de pendencias antes do primeiro cliente,
fechou riscos de deploy/CI, tirou artifacts locais do Git, adicionou readiness
real de backend, validou migracao 0023 em producao, reexecutou QA pesado e
subiu tudo para producao via CI/CD.

Importante: as Mega Specs 1, 2 e 3 ja estavam implementadas. Elas foram usadas
como referencia historica/arquitetural, nao como backlog para reimplementar. O
trabalho desta rodada foi estabilizacao, verificacao, higiene, deploy e
documentacao do que ainda falta.

O sistema estava funcionando bem antes da rodada e continua funcionando depois.
As mudancas foram deliberadamente pequenas e auditaveis.

## 2. Estado final de producao

Servicos Cloud Run:

| Servico | Revisao final | Imagem |
|---|---|---|
| `teste-ia-kernel` | `teste-ia-kernel-00033-n6p` | `teste_ia-kernel:0bbe989` |
| `teste-ia-backend` | `teste-ia-backend-00049-ts6` | `teste_ia-backend:0bbe989` |

Checks finais apos o deploy automatizado:

- backend `/health`: 200 `{"status":"ok","service":"backend"}`;
- backend `/health/ready`: 200 `{"status":"ready","service":"backend"}`;
- kernel `/health` com ID token: 200 `{"status":"ok","service":"kernel"}`;
- kernel sem autenticacao: permanece privado no Cloud Run;
- `scripts/smoke_chat_prod.py`: aprovado, eventos `chat`, `token`, `done`, sem
  erro.

Run final do GitHub Actions:

- run: `30861365151`;
- commit: `0bbe9893efe787b643b1fc1c612d6afc44bce09a`;
- jobs verdes: `frontend`, `python (backend)`, `python (kernel)`, `deploy`.

Cloud Builds finais:

- kernel: `108caeb6-5672-481f-927e-7b222eca6700`, `SUCCESS`,
  `SHORT_SHA=0bbe989`;
- backend: `5681ca9d-d445-4022-92ad-aab149e9b93c`, `SUCCESS`,
  `SHORT_SHA=0bbe989`.

## 3. Commits relevantes desta rodada

Ordem dos commits mais importantes:

- `97a4ee8` - `Prepare pre-client readiness checks`
  - criou/atualizou a spec unificada;
  - removeu `kernel/artifacts/` do indice do Git;
  - adicionou isolamento de storage nos testes;
  - implementou `/health/ready`;
  - adicionou testes de falha de migracao.
- `7118a62` - `Harden Cloud Run deploy packaging`
  - criou `.gcloudignore`;
  - permitiu `GCLOUD_BIN` no `infra/deploy.sh`;
  - protegeu deploy contra source dirty.
- `2715d1a` - `Record production readiness validation`
  - registrou evidencias de QA/producao;
  - corrigiu `scripts/regressao_fase1.py` para nao gravar token no JSON;
  - atualizou spec e handoff.
- `4a9ca00` - `Run pytest through Python in CI`
  - trocou `pytest` por `python -m pytest` no CI.
- `5f9a4f6` - `Apply schema migrations in kernel CI tests`
  - aplicou migrations no banco fresco do job do kernel.
- `8a67ba3` - `Make CI deploy script executable`
  - marcou `infra/deploy.sh` como executavel;
  - chamou deploy via `bash ./infra/deploy.sh all`.
- `0bbe989` - `Mark CI deploy readiness complete`
  - atualizou documentos depois do CI/CD verde.

## 4. Mudancas de codigo e infraestrutura

### 4.1 Higiene de artifacts

Problema: `kernel/artifacts/` tinha arquivos locais de teste rastreados pelo Git.
Isso nao era vazamento de producao, mas era sujeira perigosa para um primeiro
push/deploy.

Feito:

- `.gitignore` agora ignora:
  - `kernel/artifacts/`;
  - `backend/artifacts/`;
  - `artifacts/`;
  - `docs/fixtures-qa/`.
- `kernel/artifacts/` saiu do indice do Git.
- `git ls-files kernel/artifacts` retornou zero durante a rodada.
- `.gcloudignore` foi criado para o Cloud Build nao enviar venvs, artifacts,
  caches, docs e arquivos locais.

### 4.2 Storage temporario em testes

Feito:

- `backend/tests/conftest.py` força storage local temporario durante testes;
- `kernel/tests/conftest.py` força storage local temporario durante testes.

Objetivo: rodar suite local/CI sem recriar artifacts dentro do repo.

### 4.3 Readiness do backend

Antes: `/health` podia responder mesmo se boot/migration tivesse falhado, o que
era ruim para Cloud Run e para diagnostico.

Feito em `backend/app/main.py`:

- `/health` continua simples e retorna 200 se o processo responde;
- novo `/health/ready` retorna:
  - 200 se boot e migrations passaram;
  - 503 se migrations ou bootstrap falharem;
- log estavel `MIGRATION_FAILED` quando migracao falha;
- erro de readiness saneado, sem expor segredo.

Teste adicionado em `backend/tests/test_health.py`:

- falha de migration simulada;
- `/health` segue 200;
- `/health/ready` vira 503;
- log contem `MIGRATION_FAILED`.

### 4.4 Deploy traceavel

Feito em `infra/deploy.sh`:

- deploy bloqueia source dirty em `backend`, `kernel`, `frontend`, `infra` e
  `.github`;
- docs/evidencias podem estar dirty sem bloquear deploy manual;
- `GCLOUD_BIN` permite chamar `gcloud` certo no Windows/Git Bash;
- `SHORT_SHA` e calculado depois do `cd` para a raiz do repo;
- script ficou executavel no Git.

### 4.5 CI/CD

Falhas encontradas e corrigidas:

1. `ModuleNotFoundError: No module named 'app'` no backend CI.
   - Causa: runner chamava console script `pytest`.
   - Correcao: `python -m pytest`.
2. Kernel CI falhava com tabelas ausentes (`artifacts`, `memories`,
   `usage_records`, `tenants`).
   - Causa: Postgres do CI era fresco e kernel nao aplicava migrations.
   - Correcao: `kernel/tests/conftest.py` aplica `backend/migrations/*.sql`
     antes da suite.
3. Deploy CI falhava com `./infra/deploy.sh: Permission denied`.
   - Causa: bit executavel ausente no Git.
   - Correcao: `git update-index --chmod=+x infra/deploy.sh` e workflow chama
     `bash ./infra/deploy.sh all`.

Resultado final:

- backend CI verde;
- kernel CI verde;
- frontend CI verde;
- deploy CI verde;
- producao atualizada para `0bbe989`.

## 5. Banco e migracao 0023

Migracao validada em producao:

- `0023_teto_de_saida_de_ferramenta.sql` existe em `schema_migrations`;
- coluna `template_versions.tool_output_limit` existe como:
  - tipo: `integer`;
  - `NOT NULL`;
  - default: `24000`.

Consulta usada durante a rodada, sem imprimir `DATABASE_URL`:

```powershell
$env:DATABASE_URL = (& gcloud secrets versions access latest --secret=teste-ia-database-url --project=eduk-prd-lake)
backend\.venv\Scripts\python.exe - <<consulta psycopg somente metadados>>
```

Resultado observado:

```text
{'migration_0023_applied': True, 'column': ('tool_output_limit', 'integer', 'NO', '24000')}
```

## 6. Validacoes executadas

### 6.1 Local

Backend:

```powershell
backend\.venv\Scripts\python.exe -m pytest -q -m "integration or not integration"
backend\.venv\Scripts\python.exe -m ruff check .
```

Resultado:

- `128 passed`;
- Ruff: `All checks passed`.

Kernel:

```powershell
kernel\.venv\Scripts\python.exe -m pytest -q -m "integration or not integration"
kernel\.venv\Scripts\python.exe -m ruff check .
```

Resultado:

- `107 passed`;
- Ruff: `All checks passed`.

Frontend:

```powershell
npm run build
```

Resultado:

- build Vite/TypeScript aprovado;
- aviso de chunk grande do Plotly ja existente.

Observacao importante:

- nao rodar backend e kernel integration suites em paralelo contra o mesmo
  Postgres local; backend trunca tabelas de tenants e pode interferir no kernel.

### 6.2 Producao - plataforma

Smokes e regressao:

- `scripts/smoke_chat_prod.py`: aprovado;
- `scripts/regressao_fase1.py`: 12/12 checks;
- `scripts/super_qa.py`: 22/22 checks.

Evidencias versionadas:

- `docs/regressao-fase1.json`;
- `docs/super-qa.json`.

O script `scripts/regressao_fase1.py` foi corrigido para nao gravar token de
login no JSON quando o login passa.

### 6.3 Producao - UI

Smoke Playwright inline:

- criou tenant descartavel;
- criou usuario descartavel;
- entrou pela SPA de producao;
- criou template;
- salvou nova versao;
- fez deploy pela UI.

Observacao: o teste inline precisou usar seletores por regex sem acento porque
o pipeline PowerShell -> Node/Playwright corrompia texto acentuado em algumas
strings.

### 6.4 Producao - Licita QA

Fixtures geradas por:

```powershell
$env:LICITA_FIXTURES='docs/fixtures-qa'
py -3.12 scripts\gerar_fixtures_qa.py
```

Valores esperados:

- total empenhado: R$ 42.917.202,00;
- Porto Velho: R$ 5.204.250,75;
- maior percentual pago em Porto Velho: 84,25%.

Rodadas:

- C1: 30 turnos, 0 erros, 0 turnos vazios;
- C2: 14 turnos, 0 erros, 0 turnos vazios;
- C5: 12 turnos, 0 erros, 0 turnos vazios.

Evidencia:

- `docs/qa-conversa-licita.json` ficou com a ultima rodada executada, C5.

Observacoes:

- C1 nao reproduziu o antigo turno vazio;
- C5 executou `execute_python`, SQL, `web_search` e `call_http_api`;
- em C5, maior rajada observada foi 16 chamadas de ferramenta no turno 12;
- C2 acertou os numeros criticos, mas varios pedidos de grafico/planilha/PDF
  foram respondidos como texto, nao como artifact real. Isso e lacuna de
  qualidade de template/agente, nao quebra de runtime.

### 6.5 Producao - Chatwoot/omnichannel

Repo usado:

```text
C:\Users\lucas.rangel\Desktop\claude\agent llm\chatwoot-rt
```

Smokes:

- `scripts/smoke/omnichannel_e2e.py`: 11/11 aprovado;
- `scripts/smoke/atendimento_negocio.py`: aprovado para hamburgueria e
  ferragista.

Validado:

- provisionamento de conta Chatwoot;
- SSO;
- canal/inbox;
- Agent Bot;
- webhook aceito;
- evento duplicado ignorado;
- conversa registrada no Chatwoot;
- IA respondeu dentro da conversa;
- hamburgueria respondeu com dados reais do cardapio;
- ferragista respondeu com Furadeira 650W, preco R$ 289,90 e estoque;
- apos mensagem humana, IA silenciou.

Tokens usados vieram do Secret Manager e nao foram impressos:

- `chatwoot-bridge-admin-token`;
- `chatwoot-platform-token`.

## 7. Arquivos principais tocados

Codigo/runtime:

- `backend/app/main.py`;
- `backend/tests/test_health.py`;
- `backend/tests/conftest.py`;
- `kernel/tests/conftest.py`;
- `scripts/regressao_fase1.py`;
- `infra/deploy.sh`;
- `.github/workflows/ci.yml`;
- `.gitignore`;
- `.gcloudignore`.

Docs/evidencias:

- `HANDOFF.md`;
- `docs/specs/pendencias-antes-do-primeiro-cliente.md`;
- `docs/regressao-fase1.json`;
- `docs/super-qa.json`;
- `docs/qa-conversa-licita.json`;
- este arquivo.

## 8. Como validar rapidamente agora

Health:

```powershell
curl.exe -s -i https://teste-ia-backend-x27vtpiida-uc.a.run.app/health
curl.exe -s -i https://teste-ia-backend-x27vtpiida-uc.a.run.app/health/ready
```

Kernel privado:

```powershell
$url='https://teste-ia-kernel-x27vtpiida-uc.a.run.app'
$token=& 'C:\Program Files (x86)\Google\Cloud SDK\google-cloud-sdk\bin\gcloud.cmd' auth print-identity-token --audiences=$url
curl.exe -s -i -H "Authorization: Bearer $token" "$url/health"
```

Smoke real:

```powershell
py -3.12 scripts\smoke_chat_prod.py
```

Cloud Run:

```powershell
gcloud run services describe teste-ia-kernel --project=eduk-prd-lake --region=us-central1 --format="value(status.latestReadyRevisionName,spec.template.spec.containers[0].image)"
gcloud run services describe teste-ia-backend --project=eduk-prd-lake --region=us-central1 --format="value(status.latestReadyRevisionName,spec.template.spec.containers[0].image)"
```

CI:

```text
https://github.com/LucasRangelSSouza/agent-platform/actions/runs/30861365151
```

Se `gh` nao estiver instalado, da para consultar a API usando Git Credential
Manager, sem imprimir token, como foi feito nesta rodada.

## 9. Cuidados operacionais importantes

### 9.1 GCloud no Windows

Evitar `gcloud` puro no PowerShell desta maquina: em uma tentativa anterior ele
resolveu para um shim ruim em `System32` e travou.

Use explicitamente:

```powershell
& 'C:\Program Files (x86)\Google\Cloud SDK\google-cloud-sdk\bin\gcloud.cmd' ...
```

No Git Bash, usar PATH curto se necessario:

```bash
export PATH=/c/PROGRA~2/Google/CLOUDS~1/GOOGLE~1/bin:$PATH
```

### 9.2 Conta GCP

Conta validada durante a rodada:

```text
devlake@eduk-prd-lake.iam.gserviceaccount.com
```

O projeto default local do SDK apareceu como `mi-prd-lake`, mas o script de
deploy usa `--project=eduk-prd-lake`, entao isso nao afetou o deploy.

### 9.3 Segredos

Nao imprimir:

- `DATABASE_URL`;
- `ENCRYPTION_KEY`;
- tokens Chatwoot;
- tokens Mercado Pago;
- secrets S3.

Os smokes de Chatwoot e consultas de banco foram feitos com secrets carregados
para variaveis de processo e sem ecoar valores.

### 9.4 PIX QA

C6 envolve credencial Mercado Pago de producao e cobrancas pagaveis de verdade.
Nesta rodada nao foi rodado C6. Antes de rodar qualquer PIX:

- valor QA deve ser R$ 0,01;
- token deve vir de `MP_SECRET_FILE`;
- cancelar pendentes ao final;
- nunca tentar estornar automaticamente cobranca paga.

## 10. Pendencias antes do primeiro cliente

Estas pendencias continuam reais. Algumas sao bloqueios tecnicos; outras sao
decisao consciente do dono.

### 10.1 Operacao/seguranca

- limpar as 215 memorias antigas do tenant QA:

```sql
DELETE FROM memories
WHERE tenant_id = '31445557-8561-4b27-804d-0129a72b467d';
```

Nao execute automaticamente sem confirmar janela/risco, porque e operacao
destrutiva.

- trocar senhas padrao `123456` das instancias 9Router expostas;
- registrar dono/responsavel por cada instancia 9Router;
- confirmar que nenhum tenant usa instancia de outro;
- classificar backup do Postgres como cofre antes de migrar segredos para o
  banco.

### 10.2 Runtime

- implementar guardrail formal: turno sem texto deve emitir evento `error`;
- implementar teto formal por quantidade de tool calls por turno;
- medir tamanho real de historico/prompt por turno;
- testar falha do Mercado Pago/gateway fora do ar;
- melhorar comportamento de C2 para gerar artifacts reais quando o usuario pede
  grafico/planilha/PDF, em vez de responder apenas em texto.

### 10.3 Capacidade

- implementar ou aceitar por escrito o risco de o backend ainda nao ter pool de
  conexoes equivalente ao kernel;
- dimensionar Postgres com margem para:
  - backend;
  - kernel;
  - Chatwoot web/worker/bridge;
  - manutencao;
  - conexoes manuais;
- executar teste de carga 10 empresas x 10 usuarios ou adiar com aceite.

### 10.4 Segredos no banco

Spec fonte:

```text
docs/specs/segredos-no-banco.md
```

Falta implementar ou adiar conscientemente:

- `installation_secrets`;
- `secret_sync_state`;
- `SecretBackend`;
- UI master-only;
- sync Chatwoot;
- reconciliador;
- corte de Secret Manager para tudo que nao for bootstrap.

### 10.5 Chatwoot/canais

- decidir W-API vs WhatsApp Cloud API oficial;
- decidir visual/paleta: aceitar Chatwoot padrao ou assumir fork/build custom;
- decidir se convive com Custom Roles visivel ou faz fork;
- escolher pagina/app Meta real e janela de ativacao;
- trocar `INSTALLATION_NAME` de "Chatwoot" para marca propria.

## 11. Fonte de verdade para continuar

Leia nesta ordem:

1. `docs/handoffs/2026-08-03-readiness-producao.md` (este arquivo);
2. `docs/specs/pendencias-antes-do-primeiro-cliente.md`;
3. `HANDOFF.md`;
4. `docs/specs/segredos-no-banco.md`;
5. `.github/workflows/ci.yml`;
6. `infra/deploy.sh`.

## 12. Estado final do Git

No fechamento desta rodada:

- `origin/main` estava em `0bbe989`;
- workspace estava limpo;
- CI run final `30861365151` verde;
- Cloud Run estava servindo imagens `0bbe989`;
- smoke final de chat real passou.

