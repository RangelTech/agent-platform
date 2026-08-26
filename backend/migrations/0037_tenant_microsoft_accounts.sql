-- Produto-08 secao 12 -- conta Microsoft (Outlook Calendar + Teams) por
-- tenant, credencial das tools outlook_calendar_list_events/create_event.
-- Mesmo desenho de tenant_google_accounts (0035): token_expires_at + lock
-- por linha (SELECT ... FOR UPDATE) na hora de usar, renovacao sob demanda
-- via oauth_engine.renovar -- nunca cron/estado em memoria de processo.
--
-- Diferenca do gap corrigido em tenant_google_accounts (produto-08 §9): esta
-- tabela ja nasce multi-conta (a leitura pro contexto de execucao sempre
-- devolve a lista inteira, nunca "so a mais recente") -- nao repetir o erro.

CREATE TABLE tenant_microsoft_accounts (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    label TEXT NOT NULL DEFAULT 'Microsoft',
    email_address TEXT,
    access_token_encrypted TEXT NOT NULL,
    refresh_token_encrypted TEXT,
    token_expires_at TIMESTAMPTZ,
    token_last_refresh_error TEXT,
    scope TEXT NOT NULL DEFAULT '',
    is_active BOOLEAN NOT NULL DEFAULT true,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX tenant_microsoft_accounts_tenant_idx
    ON tenant_microsoft_accounts (tenant_id) WHERE is_active;

-- Recurso de permissao novo -- backfill JA NESTA MIGRATION (produto-08 §11
-- foi o bug de esquecer isso; nao repetir).
UPDATE user_profiles
   SET permissions = permissions || '{"microsoft_accounts": ["view", "create", "edit", "delete"]}'::jsonb
 WHERE NOT (permissions ? 'microsoft_accounts')
   AND permissions -> 'templates' @> '["view", "create", "edit", "delete"]'::jsonb;
