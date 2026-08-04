# Validação independente da rodada de readiness

Data: 03/08/2026. Valida `docs/handoffs/2026-08-03-readiness-producao.md`
conferindo cada alegação contra o ambiente, não contra o documento.

## 1. O que confere

| Alegação | Como foi conferido |
|---|---|
| `origin/main` em `0bbe989` | `git rev-parse origin/main` |
| kernel `00033-n6p`, backend `00049-ts6`, imagens `:0bbe989` | `gcloud run services describe` |
| Cloud Builds `108caeb6…` / `5681ca9d…` SUCCESS, `SHORT_SHA=0bbe989` | `gcloud builds list` |
| **O deploy veio do CI, não da mão** | audit log: `ReplaceService` por `github-deployer@eduk-prd-lake` |
| Migração 0023 em produção | `schema_migrations` + `information_schema.columns`: `tool_output_limit integer NOT NULL default 24000` |
| `/health` 200, `/health/ready` ready, kernel 403 sem auth | `curl` direto |
| kernel 107 / backend 128 / ruff limpo | suítes reexecutadas |
| `git ls-files kernel/artifacts` = 0, `.gcloudignore` criado | conferido |
| Evidências JSON sem segredo | varredura por `eyJ`, `Bearer`, `token`, `APP_USR`: só `has_token: true` |

O item que o `HANDOFF.md` listava como maior risco aberto — "o job de CI nunca
rodou" — está fechado com prova de terceiro (o audit log do GCP, não o
documento).

## 2. O que não confere, e foi corrigido

### 2.1 A suíte apagava banco sem perguntar qual

`backend/tests/conftest.py` roda `TRUNCATE tenants, users, user_profiles,
sessions CASCADE` entre testes; o conftest do kernel aplica migrações no banco
apontado. Nenhum dos dois olhava para onde o `DATABASE_URL` apontava — e o
handoff de readiness ensina, com poucas seções de distância, a exportar o DSN de
produção (seção 5) e a rodar `pytest` (seção 6.1).

Corrigido em `backend/tests/guardas.py`: host local ou serviço de CI passa;
qualquer host remoto é recusado até alguém dizer `ALLOW_DESTRUCTIVE_TESTS=1`. O
kernel **importa** a regra em vez de copiá-la. 7 testes, incluindo um que garante
que a mensagem de erro não ecoa a senha do DSN.

### 2.2 `/health/ready` entregava diagnóstico a quem só chamou uma URL

O corpo do 503 trazia `detail` com a primeira linha da exceção — e havia teste
garantindo isso. O backend sobe com `--allow-unauthenticated`, e uma falha real
do psycopg diz `connection to server at <host>, port <porta> failed: … for user
<usuário>`. Sem senha, mas com host, porta e usuário do banco.

Agora o motivo vai para o log (`READINESS_NOT_READY`) e o corpo fica com
`status` e `migrations_ok`, que é o que uma probe precisa. O teste casa o corpo
inteiro, então um campo novo com diagnóstico quebra o teste em vez de vazar.

### 2.3 A guarda de deploy não via arquivo novo

`git diff` só enxerga rastreado. Um arquivo novo em `kernel/` passava pela
guarda enquanto o Cloud Build enviava o diretório inteiro — a imagem sairia com
código que o commit do nome dela não contém, exatamente o que a guarda promete
impedir. Trocado por `git status --porcelain --untracked-files=normal`, e a
falha agora imprime o que está sujo.

### 2.4 Números e alegações que não se sustentaram

- **Memórias do tenant de QA: 651, não 215.** Os dois handoffs repetiam 215. O
  `DELETE` proposto apagaria 651 linhas.
- **"C2 respondeu em texto onde deveria haver artifact" não se reproduz.** C2
  rodado contra o mesmo kernel `0bbe989`, com o teto de 24k ativo: 14/14, com
  `chart` nos dois gráficos, `file` nas duas planilhas e no PDF, e os números
  certos (20,07%, 84,25%, R$ 42.917.202,00). Foi variação de rodada, não lacuna
  de template — e, principalmente, **o teto de saída não quebrou C2**.
- **Run do GitHub Actions `30861365151` não pôde ser verificado** aqui (repo
  privado, `gh` sem autenticação). Builds, audit log e revisões corroboram; o run
  em si, não.

## 3. Segredos: o estado real, verificado

### 3.1 O "sistema de keys" NÃO está implementado

- Última migração é a **0023**. Não existem `installation_secrets`,
  `secret_sync_state` nem `SecretBackend` — busca no `backend/` e no
  `frontend/src` não encontra nenhuma referência.
- **Não há cofre na VPS.** `vps_rt_infra/compose/docker-compose.yml` sobe
  traefik, postgres, pgbouncer, redis, minio, pgadmin, grafana, uptime-kuma,
  code-server, prometheus, loki, promtail, node-exporter, cadvisor e ninerouter.
  Nenhum Infisical, nenhum Vault.
- O que existe na VPS é outra coisa: segredos em **GitHub Secrets** +
  `terraform.tfvars` + arquivos locais fora do repo
  (`bootstrap_github_secrets.py`). Isso resolve o provisionamento da VPS, não o
  cadastro de chaves pela nossa tela.

`docs/specs/segredos-no-banco.md` continua sendo proposta, não implementação.

### 3.2 As chaves do Instagram/Meta NÃO estão configuradas

- `chatwoot-meta-app-id`, `chatwoot-meta-app-secret` e
  `chatwoot-meta-verify-token` **não existem** no Secret Manager.
- `chatwoot-web` não tem nenhuma env `FB_*` ou `IG_*` (22 envs, nenhuma delas).

É por isso que Instagram não aparece e Messenger fica bloqueado. Não é plano,
não é bug: a credencial nunca foi criada.

### 3.3 Isolamento por tenant — a resposta é "os dois níveis, e está certo"

Esta é a parte que responde diretamente à preocupação de "não quero um monte de
instâncias do serviço de secret":

| Camada | Escopo | Onde vive |
|---|---|---|
| **App Meta** (`FB_APP_ID`, `FB_APP_SECRET`, verify tokens) | **Um por instalação**, compartilhado por todos os tenants | `InstallationConfig` do Chatwoot (ou env) |
| **Página/conta conectada** (`page_access_token`, `instagram_id`) | **Por tenant** | `channel_facebook_pages`, com `account_id` |

Conferido no banco do Chatwoot: a tabela `channel_facebook_pages` tem
`account_id`, `page_access_token` e `instagram_id`. Ou seja, **o app é um só e as
conexões são isoladas por conta** — que é exatamente o desenho que evita
multiplicar serviço de segredo por cliente. A decisão está registrada em
`chatwoot-rt/docs/decisions/0002-branding-e-planos.md` e no runbook
`docs/runbooks/canais-meta.md`.

O mesmo vale para o cofre proposto: **uma** tabela no nosso banco, não uma
instância de cofre por tenant. O isolamento é por linha (`tenant_id`), como já
acontece em `secrets`, `payments_credentials` e `ai_services`.

Custo a aceitar, e está no runbook: app único significa que um bloqueio da Meta
atinge todos os tenants de uma vez.

### 3.4 Idioma do Chatwoot — a correção parcial já mostrou o furo

As 10 contas foram passadas para pt_BR no banco. A conta **11**, criada pelos
smokes desta rodada às 21:59, **nasceu em inglês** — porque `DEFAULT_LOCALE=pt_BR`
está no `deploy.sh` e o `chatwoot-web` não foi redeployado desde então (o
serviço não tem a env). Ajustei a conta 11, mas a próxima nascerá igual até o
redeploy.

Não é hipótese: é o furo previsto acontecendo dentro da mesma sessão.

## 4. Pendências, em ordem de risco

1. **Redeploy do `chatwoot-web`** para `DEFAULT_LOCALE=pt_BR` valer para conta
   nova (`./infra/deploy.sh chatwoot` no repo `chatwoot-rt`, que já roda o passo
   de locale).
2. **Push destes commits** — as três correções desta validação ainda são locais.
3. **Criar as credenciais Meta** e ligar Instagram/Messenger (runbook pronto).
4. **Implementar a spec de segredos** — nada dela existe ainda.
5. **Limpar as 651 memórias** do tenant de QA (operação destrutiva: confirmar
   janela).
6. Guardrails que continuam sem cobertura: turno sem texto, teto de chamadas de
   ferramenta por turno, gateway de pagamento fora do ar.
7. Decisões do dono ainda abertas: W-API × Cloud API oficial, paleta do
   Chatwoot (aceitar padrão × fork), `INSTALLATION_NAME`, senha `123456` das
   instâncias 9Router.
