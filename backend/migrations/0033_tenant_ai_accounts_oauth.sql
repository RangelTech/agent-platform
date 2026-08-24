-- Produto-08 (mega-spec-reestrutura) -- OAuth de assinatura (Claude/ChatGPT/
-- etc.) plugado no LiteLLM. Estende tenant_ai_accounts (já usada pelo modo
-- chave-de-API, 0029) com os campos de token -- mesma linha, mesma tela,
-- só um branch a mais em cima de auth_type.
--
-- Fonte de verdade da renovação: token_expires_at + lock por linha
-- (SELECT ... FOR UPDATE) na hora de usar, nunca cron/estado em memória de
-- processo -- é o que evita o bug real do 9Router (selectionMutex só
-- serializava a escolha da conexão, não o uso dela).

ALTER TABLE tenant_ai_accounts ADD COLUMN access_token_encrypted TEXT;
ALTER TABLE tenant_ai_accounts ADD COLUMN refresh_token_encrypted TEXT;
ALTER TABLE tenant_ai_accounts ADD COLUMN token_expires_at TIMESTAMPTZ;
ALTER TABLE tenant_ai_accounts ADD COLUMN token_last_refresh_error TEXT;
-- Metadados por provedor que não são o token em si (ex.: projectId do
-- Google/Antigravity, orgId do Kilo Code) -- nunca segredo, guardado claro.
ALTER TABLE tenant_ai_accounts ADD COLUMN provider_data JSONB NOT NULL DEFAULT '{}'::jsonb;

ALTER TABLE tenant_ai_accounts DROP CONSTRAINT tenant_ai_accounts_9router_ou_litellm;
ALTER TABLE tenant_ai_accounts
    ADD CONSTRAINT tenant_ai_accounts_9router_ou_litellm_ou_oauth CHECK (
        router_connection_id IS NOT NULL
        OR api_key_encrypted IS NOT NULL
        OR access_token_encrypted IS NOT NULL
    );

-- router_connection_id era único junto com tenant_id (herdado do 9Router,
-- onde toda conta tinha uma conexão remota). Conta OAuth não tem isso --
-- já não tinha pra chave de API desde 0029, mas a constraint original só
-- cobria NOT NULL, não a unicidade condicional. Sem efeito prático até
-- aqui (nenhuma linha OAuth existia), registrado por completude.
