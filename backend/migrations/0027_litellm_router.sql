-- Fase C (infra-04) — LiteLLM substitui o 9Router.
--
-- Diferença de arquitetura em relação a 0020_router_ia.sql: o 9Router exigia
-- 1 instância por tenant porque não tem multi-tenancy real (achado de
-- auditoria, `infra-04-litellm-substitui-9router.md`: mutex global de
-- seleção só serializa escolha, não uso — 2 requests concorrentes podem usar
-- a mesma conexão ao mesmo tempo, quebrando garantia de token OAuth). O
-- LiteLLM resolve isso com Team (unidade de isolamento lógico, Postgres+Redis
-- reais por trás) — não precisa mais de 1 instância por tenant, `base_url`
-- passa a ser a mesma pra todo mundo (config do serviço, não linha de banco).
--
-- Aditivo de propósito: as colunas do 9Router (`base_url`,
-- `admin_password_encrypted`, `api_key_encrypted`) continuam aqui e
-- continuam obrigatórias (NOT NULL) pros tenants que ainda não migraram — o
-- corte de produção de verdade é `infra-02`, não esta migration. Uma linha
-- de `tenant_routers` pode ter as duas coisas populadas durante a transição;
-- a camada de rotas decide qual cliente usar (`litellm_client` se
-- `litellm_team_id` estiver preenchido, senão `router_client`).

ALTER TABLE tenant_routers ADD COLUMN litellm_team_id TEXT;

-- Decisão fechada em infra-04 seção 2c: 2 virtual keys por tenant, nunca a
-- mesma — uma pro caminho bridge/kernel, outra só pro AI Assist nativo do
-- Chatwoot. Vazamento de uma não compromete a outra (superfícies de ataque
-- diferentes: Postgres do agent-platform vs. banco do Chatwoot).
ALTER TABLE tenant_routers ADD COLUMN bridge_key_encrypted TEXT;
ALTER TABLE tenant_routers ADD COLUMN ai_assist_key_encrypted TEXT;

-- `tenant_ai_accounts.router_connection_id` e `tenant_ai_combos.router_combo_name`
-- não mudam de schema — continuam guardando "o id/nome da coisa dentro da
-- instância", só que agora pode ser um `model_info.id`/`model_name` do
-- LiteLLM em vez de um id/nome do 9Router. Nenhuma migration de dado nessas
-- duas tabelas é necessária por causa desta mudança.
