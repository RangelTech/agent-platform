-- Produto-11 secao 3 -- conta Google por tenant, credencial das tools gerais
-- google_calendar_list_events/create_event, google_sheets_read/write.
-- Mesmo desenho do 0033 (tenant_ai_accounts): token_expires_at + lock por
-- linha (SELECT ... FOR UPDATE) na hora de usar, renovacao sob demanda via
-- oauth_engine.renovar -- nunca cron/estado em memoria de processo.

CREATE TABLE tenant_google_accounts (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    label TEXT NOT NULL DEFAULT 'Google',
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

CREATE INDEX tenant_google_accounts_tenant_idx
    ON tenant_google_accounts (tenant_id) WHERE is_active;
