# API interna do backend para o kernel-harness e o runtime-manager

Documento de serviço (spec 04). Não é contrato com o cliente: nada daqui aparece na UI.
Fonte do contrato de UI: `specs/anexos/04/api-contract.md`.

## 1. Autenticação

Todas as rotas ficam em `/internal/...` e também sob `/api/internal/...` (mesmo código, para
proxies que só encaminham `/api`). Header `Authorization: Bearer <token>`.

| Token | Variável | Vale para |
|---|---|---|
| Token de serviço | `HARNESS_INTERNAL_TOKEN` | todas as rotas. Vazio = rotas de serviço **fechadas** (401). |
| `run_token` | assinado com `RUN_TOKEN_SECRET` (ou derivado de `ENCRYPTION_KEY`) | só as rotas `/internal/runs/{run_id}/*` e `/internal/approvals*` **da própria run**. TTL `RUN_TOKEN_TTL_SECONDS` (900). Revogado quando a run termina. |

Regras comuns:

- Tenant, usuário e chat da run vêm da linha de `chat_runs`; o corpo nunca os informa.
- `run_token` de outra run, vencido, adulterado ou de run encerrada → `401`. Run inexistente → `404`.
- O token de sessão de usuário nunca vale aqui.
- Erros: `{detail: "<mensagem>"}`.

Rotas **só de serviço** (o `run_token` é rejeitado): `GET /internal/skills/user/...`, `PUT /internal/environment/...`.

## 2. Backend → kernel-harness

`POST {KERNEL_HARNESS_URL}/v1/runs` com o payload v1 (spec 04 §4.1), construído por
`app/harness_runtime.py::prepare_harness_run`. Auth: OIDC se `KERNEL_HARNESS_AUDIENCE`, senão
`Bearer KERNEL_HARNESS_TOKEN`. Resposta: SSE.

Diferenças e decisões da implementação:

- `run_id` e `idempotency_key` são gerados pelo backend; a run é registrada em `chat_runs` antes do POST.
- `skills.tenant_package`: `{sha256, files:[{path,sha256,content}]|null, url|null}`. Até 700 KiB de pacote vão inline;
  acima disso `files = null` e `url = {PUBLIC_BASE_URL}/internal/runs/{run_id}/package` (use o `run_token`).
  Arquivos da pasta reservada `arquivos/` (qualquer tipo: PDF, XLSX, imagem, CSV) chegam como
  `{path, sha256, content, encoding: "base64", mime}`; `sha256` é dos BYTES. Texto não traz `encoding`.
  Limites: 10 MB por arquivo, 50 MB de arquivos por pacote, até 100 arquivos; executáveis e scripts
  (`.exe .sh .py .js ...`, ou conteúdo MZ/ELF/`#!`) são recusados no envio (código vai em `scripts/`).
- `skills.user_skills`: skills ativas do usuário (`template_id` nulo ou deste template). Até 256 KiB inline;
  o excedente vem com `content: null` (buscar com `GET /internal/skills/user/...`).
- `skills.system_rev`: `HARNESS_SYSTEM_SKILLS_REV` (default `latest`).
- `manifest`: `{tools[], credential_grants[{kind,id|null}], environment{enabled}, limits{max_turns,turn_timeout_s,idle_timeout_s}}`.
  Padrões de limites: 60 turnos, 540 s por turno, 120 s de silêncio.
- `credentials`: mesmo bloco do fluxo estruturado, valores em claro (D-57): `secrets`, `datasources` (todas as ativas da
  empresa), `payment`, `email_accounts`, `google_accounts`, `microsoft_accounts`, `embedding` e, se houver anexo, `transcription`.
  `credentials_catalog` leva só rótulos (`{kind,label}`).
- `history`: presente (últimas 20 mensagens, sem a atual) **apenas** na primeira run do chat no kernel de skills.
- `memory.recall`: sempre `[]` (a recordação fica a cargo de `rt_memory_recall`).
- Canal público (`/v1/messages`): `channel = "api"`, `actor.type = "external"`, `user_id = null`. Sem usuário não há
  skills de usuário, aprovação autenticada nem gravação em `chat_messages` (a conversa é `ext-...`).
- O corpo nunca deve ser logado. O backend tampouco o registra.

Rotas que o backend chama no kernel: `POST /v1/runs/{run_id}/cancel` (cancelamento pelo usuário),
`POST /v1/approvals/{approval_id}` com `{decision:"approve"|"deny", run_id, decided_by}` (decisão autenticada; falha de
entrega não desfaz a decisão, o kernel também pode consultar `GET /internal/approvals/{id}`).

### Eventos SSE aceitos de volta

`token`, `step`, `agent`, `artifact`, `approval_required`, `skill_proposed`, `usage`, `limit`, `done`, `error`. Tipos
desconhecidos são repassados sem alteração. O backend só interpreta `done` (grava a resposta) e `error`.
A leitura roda numa tarefa de fundo (timeout de leitura `HARNESS_RUN_TIMEOUT_SECONDS`, 660 s): a resposta é gravada mesmo
que o navegador desconecte. `done` só é gravado se a run não foi interrompida.

## 3. Rotas de run

### `POST /internal/runs/{run_id}/usage`
```json
{"input_tokens": 100, "output_tokens": 40, "cost_usd": 0.01, "provider": "…", "model": "…", "latency_ms": 0, "agent_name": ""}
```
Grava em `usage_records` e soma em `tenant_usage_hourly`. Delta do turno. → `{ok:true}`.

### `POST /internal/runs/{run_id}/artifacts` (201)
```json
{"artifact_id": "uuid|null", "kind": "dataset|chart|document|file|image", "title": "", "content_type": "", "storage_path": "tenants/<tenant_id>/…", "row_count": null, "columns": null, "preview_json": null}
```
`storage_path` precisa conter o id da empresa da run e não ter `..` (senão `400`). `artifact_id` repetido → `409`.
→ `{artifact_id}`. O download segue pelas rotas de artefatos já existentes, com o isolamento por empresa.

### `POST /internal/runs/{run_id}/messages`
`{"content": "…", "attachments": []}` → `{saved, message_id}`. A resposta do assistente é gravada **uma vez por run**
(`chat_runs.assistant_saved`); a gravação feita pelo stream do backend e esta rota não duplicam. Conversa de canal
externo → `saved:false`.

### `POST /internal/runs/{run_id}/status`
`{"status": "done"|"error"|"cancelled"}` → fecha a run e revoga o `run_token`. O backend também fecha ao fim do stream.

### `POST /internal/runs/{run_id}/tool-audit` (201)
`{"tool": "rt_sql_read", "credential_kind": "datasource|null", "ok": true}` → `tool_audit` (sem valores de credencial).

### `GET /internal/runs/{run_id}/package`
`{sha256, files:[{path,sha256,content,encoding?,mime?}]}` da versão da run (para o `url` do payload).
Binários vão em base64 (`encoding: "base64"`).

### `POST /internal/runs/{run_id}/skills` (201)
Proposta de skill pessoal pelo agente.
```json
{"name": "u-resumo", "description": "", "content": "<SKILL.md completo>", "scope": "user|template"}
```
Valida nome `u-…`, frontmatter, 32 KiB, segredos e colisão com skill da empresa (`422` com a lista). Grava
`source='agent'`, `status='pending_review'` (proposta nova de skill já existente volta para revisão). Máximo 20 pendentes
por usuário (`409`). Sem usuário na run → `400`. → `{user_skill_id, name, status}`. O kernel emite o SSE `skill_proposed`.

## 4. Outras rotas

### `GET /internal/skills/user/{tenant_id}/{user_id}?template_id=` (só serviço)
`{skills:[{name,description,content,revision}]}` das skills **ativas** (materialização em disco). Usuário de outra
empresa/inativo → `404`.

### Aprovações
- `POST /internal/approvals` (201): `{run_id, summary, risk:"low|medium|high", kind, details:{}, ttl_seconds=600 (30–3600)}`
  → `{approval_id, status:"pending", expires_at}`. Aceita serviço ou `run_token` da mesma run. Run sem usuário → `400`.
  O kernel então emite `approval_required {approval_id, summary, risk, expires_at}`.
- `GET /internal/approvals/{approval_id}` → `{approval_id, run_id, status:"pending|approved|denied|expired", decided_at}`.
- O usuário decide em `POST /api/approvals/{id}` (sessão autenticada; só o dono do chat). Aprovações vencidas viram `expired`.

### `PUT /internal/environment/{tenant_id}` (só serviço)
O runtime-manager informa o estado observado:
```json
{"state": "stopped|starting|running|degraded|suspended", "plan": "", "usage": {"cpu_pct":0,"ram_mb":0,"ram_limit_mb":0,"disk_gb":0,"disk_limit_gb":0,"egress_gb":0}, "last_backup_at": "iso", "apps": [{"name":"loja","port":8080,"url":"https://…","status":"online|offline|waking"}]}
```
`apps` (quando presente) substitui a lista de exposições da empresa. Todos os campos são opcionais.

## 5. Backend → runtime-manager (opcional)

Com `RUNTIME_MANAGER_URL`, `GET/POST /api/environment/*` repassam a
`{RUNTIME_MANAGER_URL}/v1/tenants/{tenant_id}/environment[/start|/stop|/backup|/domains|/domains/{host}|/apps/{name}]`
(Bearer `RUNTIME_MANAGER_TOKEN`) e espelham o estado no Postgres. Sem URL, o backend simula (só Postgres). Falha do gerenciador
→ `502` "Ambiente indisponível no momento" (e `state:"degraded"` na leitura).

## 6. Persistência (migrações 0043–0047)

`chat_runs`, `template_skill_files`, `template_skill_drafts`, `template_version_credentials`, `user_skills`, `skill_audit`,
`approvals`, `tool_audit`, `tenant_environment`, `tenant_environment_event`, `tenant_exposures`, `tenant_domains`,
`worker_pool_state`, `tenant_usage_hourly`; colunas `templates.agent_kind`, `chats.kernel`, `template_versions.manifest`,
`tenants.harness_enabled`.

## 7. Variáveis de ambiente novas

`KERNEL_HARNESS_URL`, `KERNEL_HARNESS_AUDIENCE`, `KERNEL_HARNESS_TOKEN`, `HARNESS_INTERNAL_TOKEN`, `RUN_TOKEN_SECRET`,
`RUN_TOKEN_TTL_SECONDS`, `HARNESS_RUN_TIMEOUT_SECONDS`, `HARNESS_SYSTEM_SKILLS_REV`, `RUNTIME_MANAGER_URL`,
`RUNTIME_MANAGER_TOKEN`, `APPS_BASE_DOMAIN`.
