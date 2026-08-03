# Spec - pendencias unificadas antes do primeiro cliente

Status: proposta executavel. Atualizada em 03/08/2026.

Progresso da execucao em 03/08/2026:

- higiene local iniciada: `kernel/artifacts/` saiu do indice do Git e novos
  artifacts locais estao ignorados;
- backend e kernel agora usam storage temporario nas suites de teste;
- backend ganhou `/health/ready`, separado de `/health`, com erro saneado e log
  `MIGRATION_FAILED` quando migracao falha;
- validacao local: backend 128/128, kernel 107/107, Ruff backend/kernel limpo e
  build do frontend aprovado;
- ainda nao houve deploy desta rodada em producao.

Esta spec consolida o que falta no `agent-platform` antes de instalar em um
cliente real. Ela junta:

- `HANDOFF.md`;
- `docs/specs/segredos-no-banco.md`;
- as Mega Specs 1, 2 e 3;
- a investigacao de `kernel/artifacts/`;
- o estado local do repo: 20 commits a frente de `origin/main`, CI/CD ainda nao
  exercitado em push, e pendencias de QA contra producao.

O objetivo nao e listar tudo que seria bom fazer um dia. O objetivo e dizer o que
precisa estar resolvido, aceito ou conscientemente adiado antes do primeiro
cliente.

## 1. Criterio de pronto

O criterio que governa esta spec e o mesmo registrado no handoff:

> Harness verde nao e funcionalidade provada.

Um item so esta pronto quando:

1. o codigo ou a decisao existe;
2. foi exercitado no caminho real que o cliente usa;
3. o efeito observavel foi conferido;
4. a evidencia ficou salva em log, JSON, teste automatizado ou documento.

Rodar uma suite e ver verde ajuda, mas nao substitui olhar as ferramentas
chamadas, os artifacts criados, as cobrancas no gateway, a revisao do Cloud Run,
as colunas no banco, ou a resposta aparecendo dentro do Chatwoot.

## 2. Estado atual resumido

### 2.1 Plataforma

O `agent-platform` tem tres processos:

- `backend`: FastAPI, dono do Postgres, auth, tenants, templates, chats, secrets,
  pagamentos, integracoes e ponte para o kernel.
- `kernel`: FastAPI + LangGraph, runtime de agentes, supervisor, especialistas,
  tools, datasources, RAG, artifacts e memoria.
- `frontend`: React/Vite, SPA administrativa e chat.

Fluxo principal: `frontend -> backend /chat/send -> template_runtime -> kernel
/v1/runs -> SSE -> backend -> frontend`.

Regra dura: credenciais de datasource e secrets crus so podem ser descriptografados
no ponto de montagem do payload ou no resolvedor especifico de secrets. Nao devem
vazar para listagens, logs, frontend ou trace.

### 2.2 Producao

Servicos principais em Cloud Run:

- `teste-ia-backend`;
- `teste-ia-kernel`;
- `chatwoot-web`;
- `chatwoot-worker`;
- `chatwoot-bridge`.

Banco principal: Postgres na VPS, porta 5433 com TLS. O PgBouncer da VPS nao e o
caminho do `agent-platform`, porque `pool_mode=transaction` quebra prepared
statements usados por psycopg/LangGraph.

Artifacts e uploads em producao nao ficam em `kernel/artifacts/`. O deploy seta
`STORAGE_BACKEND=s3`, `S3_BUCKET=teste-ia`,
`S3_ENDPOINT_URL=https://storage.rangeltech.net` e `S3_PREFIX=agent-llm`. Ou seja:
producao usa o MinIO da VPS.

### 2.3 `kernel/artifacts/`

`kernel/artifacts/` e residuo de teste local. Foi verificado:

- 579 arquivos locais;
- 194 pastas de tenant;
- 324 arquivos ja rastreados pelo Git;
- 318 desses ja estao em `origin/main`;
- nenhum arquivo do tenant de QA/producao `31445557-8561-4b27-804d-0129a72b467d`;
- arquivos pequenos, ate cerca de 4,9 KB;
- origem nos commits antigos do pipeline de datasources, artifacts e RAG.

Conclusao: nao e vazamento de producao. E sujeira local de teste que nao deveria
ser versionada e que precisa parar de crescer.

### 2.4 Deploy e Git

Estado confirmado nesta trilha:

- 20 commits locais a frente de `origin/main`;
- job `deploy` no GitHub Actions dispara em push no `main`;
- o job roda `./infra/deploy.sh all`;
- WIF/IAM do deploy foi configurado, mas o job ainda nao foi exercitado por push;
- o kernel com `tool_output_limit` foi commitado, mas nao validado em turno real
  depois do deploy;
- a migracao `0023_teto_de_saida_de_ferramenta.sql` ainda precisa ser conferida
  em producao.

Esse conjunto muda a ordem dos trabalhos: o primeiro `git push` nao e um simples
envio de codigo. Ele tambem e o primeiro teste do CI, do deploy automatizado, do
kernel novo, do backend novo e da migracao 0023.

## 3. Ordem obrigatoria

Esta e a ordem recomendada.

| Bloco | Assunto | Por que vem aqui |
|---|---|---|
| 0 | Higiene do repo | Evita versionar artifacts locais antes do push |
| 1 | Observabilidade de boot/migracao | Evita revisao saudavel com migracao quebrada |
| 2 | Deploy fatiado | Separa kernel, backend, migracao e CI |
| 3 | QA pos-deploy | Prova o que acabou de subir |
| 4 | Falhas silenciosas do runtime | Fecha buracos que o cliente sentiria direto |
| 5 | Capacidade | Protege o cenario 10 empresas x 10 usuarios |
| 6 | Segredos no banco | Reduz dependencia do Secret Manager |
| 7 | Chatwoot/canais | Fecha omnichannel real e decisoes de produto |
| 8 | Decisoes do dono | Registra riscos aceitos antes de cliente |

Blocos 0 a 3 sao sequenciais. Blocos 4, 5, 6 e 7 podem andar em paralelo depois
do bloco 3. O bloco 8 deve ser respondido pelo dono antes do primeiro cliente,
mesmo que algumas respostas sejam "aceito o risco".

## 4. Bloco 0 - higiene do repositorio

### 4.1 Ignorar artifacts locais novos

O `.gitignore` precisa conter `kernel/artifacts/` para impedir que novas rodadas
de teste sujem o status do Git.

Feito quando:

- `git status --porcelain` nao lista novas pastas sob `kernel/artifacts/` depois
  de uma rodada de testes.

### 4.2 Remover artifacts ja rastreados do indice

O `.gitignore` nao remove arquivos ja rastreados. Falta tirar `kernel/artifacts/`
do indice:

```bash
git rm -r --cached kernel/artifacts
```

Nao reescrever historico agora. Os arquivos ja foram para `origin/main`, mas sao
fixtures pequenos, em repo privado, sem tenant real identificado. Reescrever
historico junto de 20 commits locais e um CI ainda nao testado aumenta mais o
risco do que reduz.

Feito quando:

- `git ls-files | rg "kernel/artifacts/"` nao devolve nada;
- os arquivos continuam no disco local, se ainda forem uteis para debug;
- o proximo `git add -A` nao volta a adiciona-los.

### 4.3 Fazer testes gravarem fora do repo

Causa raiz: `kernel/app/config.py` usa `artifacts_local_dir = "./artifacts"` por
default. Rodando teste a partir de `kernel/`, isso vira `kernel/artifacts/`.

Correcoes:

- fixture `autouse` no `kernel/tests/conftest.py` apontando
  `ARTIFACTS_LOCAL_DIR` ou `settings.artifacts_local_dir` para `tmp_path`;
- fixture equivalente no backend se algum teste acionar storage local;
- garantir que nenhum teste dependa de arquivo persistido entre rodadas.

Feito quando:

- `pytest` roda duas vezes seguidas;
- `kernel/artifacts/` nao e recriado;
- `git status` continua limpo em relacao a artifacts.

### 4.4 Limpar memorias do tenant de QA

O handoff registra 215 memorias antigas no tenant:

```sql
DELETE FROM memories
WHERE tenant_id = '31445557-8561-4b27-804d-0129a72b467d';
```

Feito quando:

- `SELECT count(*) FROM memories WHERE tenant_id = '31445557-8561-4b27-804d-0129a72b467d'`
  retorna 0;
- a limpeza fica registrada no handoff ou em nota operacional.

## 5. Bloco 1 - boot, readiness e migracoes

### 5.1 Problema

O backend roda migracoes no startup. O desenho atual tenta manter `/health`
respondendo mesmo quando tarefas de boot falham. Isso ajuda a diagnosticar, mas
tem um efeito perigoso: uma migracao quebrada pode nao impedir a revisao de subir
como "saudavel".

O risco concreto aqui e a migracao 0023. Se ela falhar, a coluna
`tool_output_limit` nao existe e o erro aparece depois, quando alguem tentar
salvar versao de template.

### 5.2 Desenho minimo

Implementar estado de boot separado:

- `boot_ok`;
- `migrations_ok`;
- `boot_detail`;
- `migration_error`, sem segredo e sem stack trace cru na resposta HTTP.

Endpoints:

- `/health`: continua simples, 200, para dizer que o processo responde;
- `/health/ready`: 200 se boot/migracoes essenciais passaram, 503 se falharam.

Logs:

- emitir marcador estavel `MIGRATION_FAILED`;
- incluir nome da migracao e erro saneado;
- nunca incluir `DATABASE_URL`, `ENCRYPTION_KEY` ou outro segredo.

Feito quando:

- teste forca falha de migracao;
- `/health` responde 200;
- `/health/ready` responde 503;
- corpo da resposta contem motivo saneado;
- log contem `MIGRATION_FAILED`.

## 6. Bloco 2 - deploy fatiado e primeiro push

### 6.1 Regra

Nao usar o primeiro `git push` como primeiro deploy real dos 20 commits. Primeiro
subir manualmente e fatiado. Depois usar o push para testar o CI/CD idempotente.

### 6.2 Preparacao

Antes de qualquer deploy:

```bash
gcloud config get-value account
```

A conta deve ser a conta com permissao de deploy no projeto `eduk-prd-lake`.
Trabalhos com BigQuery podem trocar a conta global do gcloud; isso ja causou
`PERMISSION_DENIED` antes.

Feito quando:

- conta conferida e registrada no log da sessao;
- `git status` nao contem artifacts acidentais.

### 6.3 Deploy manual do kernel

Subir apenas o kernel:

```bash
./infra/deploy.sh kernel
```

Motivo: o kernel carrega o comportamento novo de runtime, inclusive teto de
saida de ferramenta. Ele nao depende da migracao 0023, porque recebe o campo no
payload montado pelo backend.

Feito quando:

- nova revisao do `teste-ia-kernel` recebe 100% do trafego;
- um turno real responde sem erro;
- logs nao mostram erro novo de runtime.

### 6.4 Deploy manual do backend

Subir o backend depois do kernel:

```bash
./infra/deploy.sh backend
```

Conferir migracao:

- `/health/ready` precisa responder 200;
- a coluna `tool_output_limit` precisa existir em producao;
- salvar uma versao de template pela UI precisa funcionar.

Feito quando:

- migracao 0023 aparece como aplicada;
- a coluna existe no banco;
- operacao de salvar template funciona;
- nenhuma credencial e impressa em log.

### 6.5 Push e CI

Depois do deploy manual estar validado:

```bash
git push
```

O objetivo do primeiro push passa a ser testar o pipeline, nao descobrir se o
produto sobe.

Feito quando:

- workflow do GitHub Actions termina verde;
- job `deploy` roda pelo menos uma vez;
- revisoes finais continuam saudaveis;
- comportamento validado nos blocos de QA nao regrediu.

### 6.6 Documentar a ordem no `deploy.sh`

Adicionar comentario curto no topo do script explicando:

- conferir conta do gcloud;
- kernel antes de backend quando houver mudanca de runtime;
- backend depois, com readiness/migracao conferidas;
- CI so assume depois do primeiro deploy fatiado.

Feito quando:

- o comentario existe;
- proxima pessoa nao precisa redescobrir essa ordem pelo handoff.

## 7. Bloco 3 - QA pos-deploy

### 7.1 Reexecutar C1 e C2

Obrigatorio depois do kernel novo. O teto `tool_output_limit` pode degradar
resposta sem erro visivel.

```bash
LICITA_FIXTURES=<pasta> python scripts/qa_conversa_licita.py 1
LICITA_FIXTURES=<pasta> python scripts/qa_conversa_licita.py 2
```

Conferir:

- total empenhado: R$ 42.917.202,00;
- Porto Velho: R$ 5.204.250,75;
- percentual: 84,25%;
- lista de tools chamadas em `docs/qa-conversa-licita.json`;
- nenhum aviso de truncamento sem `artifact_id`.

Feito quando:

- C1 e C2 rodam contra a producao atual;
- os numeros batem fora do agente;
- o JSON de evidencia e atualizado.

### 7.2 Conferir C5

C5, sandbox e web, ficou pendente de leitura na sessao anterior.

Feito quando:

- resultado do C5 e lido;
- ferramentas realmente chamadas sao conferidas;
- tabela de resultados do handoff ou desta spec e atualizada.

### 7.3 Smokes do Chatwoot

Rodar contra producao, no repo `chatwoot-rt`:

```bash
python scripts/smoke/omnichannel_e2e.py
python scripts/smoke/atendimento_negocio.py
```

Feito quando:

- smoke principal passa 11/11;
- smoke de atendimento passa 18/18, se esse ainda for o contrato atual;
- a IA responde dentro da conversa do Chatwoot;
- handoff humano silencia o bot;
- resultado fica salvo no repo ou no handoff.

### 7.4 PIX de QA

Toda cobranca com credencial `APP_USR-` e pagavel de verdade. Manter as regras:

- valor de QA: R$ 0,01;
- token via `MP_SECRET_FILE`;
- nunca imprimir token alem dos 4 ultimos caracteres;
- cancelar pendentes ao fim;
- nunca tentar estornar automaticamente cobranca paga.

Feito quando:

- toda cobranca criada no QA esta cancelada ou conscientemente registrada;
- `scripts/cancelar_cobrancas_qa.py` foi executado quando necessario.

## 8. Bloco 4 - falhas silenciosas do runtime

### 8.1 Turno sem texto

Defeito observado: C1 turno 11 voltou sem resposta e sem erro.

Correcoes:

1. blindagem: se o supervisor termina sem texto, emitir evento `error` explicito;
2. diagnostico: reproduzir o turno com trace e achar a causa.

Feito quando:

- teste forcando resposta vazia recebe `error`;
- cliente nunca fica com stream encerrado em silencio;
- causa raiz fica registrada, mesmo que a correcao seja posterior.

### 8.2 Teto de chamadas de ferramenta por turno

Defeito observado: C1 turno 25 disparou 30 chamadas de ferramenta e publicou 26
datasets para uma pergunta de grafico.

Correcoes:

- contador por turno em estado compartilhado;
- configuracao `max_tool_calls_per_turn`;
- evento `limit` quando atingir teto;
- supervisor deve responder com o que ja tem, nao falhar seco;
- trace deve preservar as ferramentas ate o limite.

Feito quando:

- teste com tool que sempre pede mais para no teto;
- evento `limit` aparece;
- resposta final orienta o usuario com o que ja foi obtido.

### 8.3 Historico por tamanho real

Hoje `history_limit` conta mensagens, nao tokens nem caracteres. Uma conversa com
poucas mensagens gigantes ainda pode estourar o modelo.

Ordem:

1. instrumentar tamanho do prompt montado por turno;
2. medir em conversa real onde estoura;
3. decidir entre teto por caracteres, estimativa de token ou tokenizer real;
4. aplicar limite antes de enviar ao provider.

Feito quando:

- ha metricas por turno;
- ha numero medido;
- decisao de limite esta registrada;
- teste cobre mensagem gigante.

### 8.4 Gateway de pagamento fora do ar

Nao ha cobertura suficiente para Mercado Pago indisponivel no meio de cobranca.

Feito quando:

- teste simula falha do gateway;
- nenhuma cobranca fantasma e criada;
- nenhum QR invalido e mostrado;
- usuario recebe falha explicita e recuperavel.

## 9. Bloco 5 - capacidade

### 9.1 Risco principal

A Mega Spec 3-A do 9Router foi implementada e validada. A 3-B, capacidade, ainda
nao foi executada. O gargalo esperado para 10 empresas x 10 usuarios nao e IA: e
conexao de banco.

O backend abre conexao por chamada, sem pool equivalente ao do kernel. O Postgres
da VPS tem limite de conexoes finito. Sem medicao, 100 usuarios simultaneos e um
chute.

### 9.2 Backend com pool

Implementar pool no backend, inspirado no kernel:

- pool por processo;
- check de conexao antes de reusar conexao ociosa;
- limites coerentes com Cloud Run concurrency;
- fechamento limpo no shutdown;
- testes de rollback/commit preservados.

Feito quando:

- suites backend passam;
- teste simula conexao ociosa quebrada;
- numero maximo de conexoes por instancia fica documentado.

### 9.3 Postgres dimensionado

Depois do pool:

- definir `max_connections` da VPS a partir do numero de instancias e tamanho do
  pool;
- documentar margem para Chatwoot, worker, bridge, manutencao e conexoes
  manuais;
- nao aumentar `max_connections` por chute.

Feito quando:

- planilha/nota de dimensionamento existe;
- configuracao real do Postgres confere com a conta;
- dashboards mostram conexoes usadas.

### 9.4 Teste de carga

Rodar carga representando 10 empresas x 10 usuarios.

Medir:

- latencia por turno;
- conexoes abertas;
- erros de banco;
- filas/timeout;
- uso do kernel;
- uso do 9Router;
- comportamento do Chatwoot/bridge.

Feito quando:

- ha numero medido de sessoes simultaneas suportadas;
- numero passa o cenario alvo com margem;
- gargalo real fica identificado.

## 10. Bloco 6 - segredos no banco

Esta parte e detalhada em `docs/specs/segredos-no-banco.md`. Aqui fica o plano
unificado e as dependencias.

### 10.1 Meta

Reduzir Secret Manager de muitos segredos operacionais para bootstrap minimo.

Bootstrap nunca sai totalmente do ambiente:

- `DATABASE_URL`;
- `ENCRYPTION_KEY`;
- `SECRET_KEY_BASE` do Chatwoot;
- `REDIS_URL`, quando exigido antes do app falar com banco.

Tudo que nao e bootstrap deve ir para o banco, cifrado e administravel pela tela.

### 10.2 Fase 1 - fundacao

Implementar:

- migracao `installation_secrets`;
- migracao `secret_sync_state`;
- `key_id` para rotacao;
- `version` monotonica;
- rotas master-only;
- UI de segredos de instalacao;
- `GET` sem valor cru;
- auditoria de escrita e leitura descriptografada por servico.

Feito quando:

- valor salvo nao volta pelo `GET`;
- banco armazena cifrado;
- permissao bloqueia usuario de tenant;
- teste garante que log/resposta nao contem segredo.

### 10.3 Fase 2 - resolvedor

Criar `SecretBackend`:

1. banco;
2. env como escape hatch;
3. Infisical quando/se existir.

Banco primeiro e deliberado. Se env vencer banco, uma variavel velha no Cloud Run
esconde chave nova cadastrada na tela.

Feito quando:

- Serper/S3/tokens de servico podem ser resolvidos pelo banco;
- env ainda funciona como fallback consciente;
- teste documenta precedencia.

### 10.4 Fase 3 - Chatwoot

Fato verificado: no Chatwoot v3.16, env vence banco para `InstallationConfig`.
Enquanto `FB_*` existir no env, salvar no banco do Chatwoot nao tem efeito.

Implementar:

- `sync_installation_config.rb` no `chatwoot-rt`;
- execucao via Rails runner/Cloud Run Job;
- remocao de `FB_*`/`IG_*` do deploy quando migrados;
- sync imediato com 3 tentativas e backoff 1/4/15 s;
- reconciliador diario;
- botao manual `POST /api/installation-secrets/{id}/sync`;
- tela mostrando `sync != ok` em destaque.

Feito quando:

- segredo criado na nossa tela chega ao `InstallationConfig`;
- canal Meta aparece sem redeploy;
- falha de sync retorna erro ao usuario;
- reconciliador diario conserta um `error`;
- sync manual propaga na hora.

### 10.5 Fase 4 - segredos de canal

Tokens W-API e page tokens da Meta passam a vir do cofre, nao do corpo solto do
provisionamento.

Feito quando:

- provisionamento de inbox busca token pelo cofre;
- token nunca aparece em resposta/listagem/log;
- rotacao de token por canal nao afeta outro tenant.

### 10.6 Fase 5 - corte do Secret Manager

So depois de uma rodada de QA completa:

- remover segredos migrados do Secret Manager;
- manter apenas bootstrap;
- documentar rollback.

Pre-requisito: backup do Postgres precisa ser tratado como cofre, porque passa a
conter segredos cifrados. A chave mestra nao pode estar no mesmo backup.

Feito quando:

- lista de segredos restantes esta documentada;
- backup tem controle de acesso equivalente ao cofre;
- chave mestra fica fora do backup;
- restore testado nao revela segredo sem chave.

## 11. Bloco 7 - Chatwoot, canais e omnichannel

### 11.1 Meta real

Mega Spec 2 esta em producao: Chatwoot empacotado, bridge propria, SSO,
provisionamento, Agent Bot e handoff. O que falta nao e a arquitetura base, e
sim provar e decidir os canais reais de cliente.

### 11.2 Smokes obrigatorios

Mesmo que ja tenham passado antes, rodar de novo contra a producao atual depois
dos deploys do bloco 2:

- `omnichannel_e2e.py`;
- `atendimento_negocio.py`.

Feito quando:

- IA responde no Chatwoot;
- replay duplicado e ignorado;
- handoff humano interrompe bot;
- falhas ficam registradas em tabela/evento e nao em 5xx infinito.

### 11.3 Meta/Instagram/Messenger

O plumbing e o runbook existem, mas ativar pagina real e checkpoint humano.
Tambem depende da fase de segredos no banco para parar de depender de Secret
Manager/env.

Feito quando:

- dono decide qual pagina/app usar;
- segredos Meta existem no cofre novo ou no mecanismo atual conscientemente;
- canal real recebe e responde mensagem;
- risco de app compartilhado fica registrado.

### 11.4 WhatsApp W-API

A ponte usa W-API, nao WhatsApp Cloud API oficial. Isso e aceitavel tecnicamente,
mas e decisao de produto por risco de ban/SLA.

Feito quando:

- dono registra aceite do risco ou escolhe Cloud API oficial;
- cliente piloto sabe o tipo de canal usado;
- runbook de queda/bloqueio existe.

### 11.5 Itens de acabamento Chatwoot

Pendencias:

- trocar `INSTALLATION_NAME` de "Chatwoot" para marca propria;
- decidir paleta: aceitar visual padrao ou aceitar fork/build customizado;
- decidir Custom Roles visivel: conviver ou forkar;
- manter portugues definitivo nas contas novas e existentes.

Feito quando:

- `INSTALLATION_NAME` aplicado;
- decisao de paleta registrada;
- decisao de Custom Roles registrada;
- smoke visual basico passa.

## 12. Bloco 8 - 9Router e IA

### 12.1 Estado

Mega Spec 3-A foi implementada: uma instancia de 9Router por tenant, porque o
schema do 9Router nao tem multitenancy seguro. Combos sao publicados como
`ai_service` openai-compatible, sem mudar o kernel.

### 12.2 Pendencias antes de cliente

Obrigatorias:

- trocar senha `123456` nas instancias expostas;
- registrar dono/responsavel por cada instancia;
- documentar runbook de reset de OAuth;
- confirmar que nenhum tenant usa instancia de outro.

Feito quando:

- senha padrao nao funciona mais;
- tenant de QA usa sua propria instancia;
- acesso administrativo esta documentado fora do repo.

### 12.3 Pendencias que podem ficar como roadmap

Podem ser conscientemente adiadas:

- autosservico de provisionamento de instancia;
- fallback automatico para BYOK se o router cair;
- tela de painel de uso, apesar da rota `GET /api/ai-router/uso` ja existir.

Se adiadas, registrar no handoff como roadmap, nao como bloqueio.

## 13. Decisoes do dono

Antes do primeiro cliente, responder por escrito:

| # | Decisao | Opcoes |
|---|---|---|
| 1 | WhatsApp nao oficial | Aceitar W-API com risco ou migrar para Cloud API |
| 2 | QA em producao | Aceitar ate o primeiro cliente ou criar homologacao agora |
| 3 | Chatwoot visual | Aceitar padrao ou assumir fork/build custom |
| 4 | Custom Roles visivel | Conviver ou forkar |
| 5 | Senha das instancias 9Router | Trocar agora pela UI e registrar responsavel |
| 6 | Meta real | Escolher pagina/app e janela de ativacao |
| 7 | Segredos no banco | Aceitar que backup vira cofre e ajustar acesso |

Feito quando:

- cada decisao tem resposta no proprio documento, no handoff ou em ADR;
- o primeiro cliente nao recebe risco implicito por omissao.

## 14. Trabalho futuro registrado, fora do corte

Nao bloquear primeiro cliente se houver aceite consciente:

- PIX formatado pelo WhatsApp, com QR e copia-e-cola, consumindo artifact;
- fila persistente para webhook, alem de `BackgroundTasks`;
- replay em massa de eventos guardados;
- autosservico completo de 9Router;
- fallback automatico de IA;
- painel de uso do router;
- ambiente dedicado de homologacao, se ainda nao houver cliente.

Quando houver primeiro cliente, "ambiente dedicado de homologacao" sai desta
lista e vira bloqueio para qualquer QA destrutivo.

## 15. Checklist final de readiness

Antes do primeiro cliente, todos estes itens precisam estar marcados ou
explicitamente aceitos como risco:

- [x] `kernel/artifacts/` ignorado para arquivos novos.
- [x] `kernel/artifacts/` removido do indice do Git.
- [x] Testes gravam artifacts em `tmp_path` ou storage temporario.
- [ ] 215 memorias antigas do tenant de QA limpas.
- [x] `/health/ready` implementado e testado com falha de migracao.
- [x] `MIGRATION_FAILED` emitido em log saneado.
- [ ] Kernel deployado manualmente e validado.
- [ ] Backend deployado manualmente e migracao 0023 conferida.
- [ ] Coluna `tool_output_limit` existe em producao.
- [ ] Primeiro push executa CI/CD verde.
- [ ] C1 e C2 reexecutados contra kernel novo.
- [ ] C5 lido e registrado.
- [ ] Smokes Chatwoot reexecutados contra producao.
- [ ] Turno sem texto vira `error` explicito.
- [ ] Teto de chamadas de ferramenta por turno implementado.
- [ ] Tamanho real de historico/prompt medido.
- [ ] Falha do Mercado Pago coberta por teste.
- [ ] Pool do backend implementado ou risco de conexao aceito por escrito.
- [ ] Teste de carga 10 x 10 executado ou adiado com aceite.
- [ ] Spec de segredos fase 1 implementada ou mantida como pos-primeiro-cliente
      com aceite.
- [ ] Backup classificado como cofre antes de migrar segredos de instalacao.
- [ ] Senhas padrao das instancias 9Router trocadas.
- [ ] Decisao W-API vs Cloud API registrada.
- [ ] Decisao homologacao vs QA em producao registrada.
- [ ] Decisao visual/fork do Chatwoot registrada.
- [ ] Decisao de ativacao Meta registrada.

## 16. Sequencia pratica recomendada

Executar nesta ordem:

1. Fechar higiene do repo: `.gitignore`, `git rm --cached`, fixtures temporarias.
2. Implementar `/health/ready` e teste de migracao falhando.
3. Rodar suites locais relevantes.
4. Deploy manual do kernel.
5. Rodar um turno real simples.
6. Deploy manual do backend.
7. Conferir migracao 0023 e salvar template pela UI.
8. Reexecutar C1, C2 e conferir C5.
9. Rodar smokes do Chatwoot.
10. Fazer `git push` e acompanhar CI/CD ao vivo.
11. Corrigir falhas silenciosas: turno vazio, teto de tools, Mercado Pago fora.
12. Medir capacidade e aplicar pool do backend.
13. Implementar segredos no banco em fases reversiveis.
14. Registrar decisoes do dono.

Se for preciso cortar escopo para instalar um piloto, nao cortar os itens de
seguranca basica: artifacts fora do Git, senhas padrao trocadas, deploy
conferido, QA pos-deploy, e riscos de canal registrados.

## 17. Fontes locais

- `HANDOFF.md`;
- `docs/specs/segredos-no-banco.md`;
- `personal-skills/mega-spec/memoria.md`;
- `personal-skills/mega-spec-fase2/memoria.md`;
- `personal-skills/mega-spec-fase3/memoria.md`;
- `infra/deploy.sh`;
- `.github/workflows/ci.yml`;
- investigacao local de `kernel/artifacts/`.
