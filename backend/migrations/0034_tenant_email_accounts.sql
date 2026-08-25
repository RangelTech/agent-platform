-- Produto-11 (mega-spec-reestrutura) -- tools gerais de email (SMTP/IMAP),
-- credencial por tenant. Mesma logica de tenant_ai_accounts (varias contas
-- por tenant, credencial cifrada), mas sem OAuth: qualquer provedor de
-- email aceita usuario/senha (ou senha de app) via SMTP/IMAP, mais generico
-- do que registrar OAuth app por provedor (decisao do dono, 25/08/2026).

CREATE TABLE tenant_email_accounts (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    label TEXT NOT NULL,
    email_address TEXT NOT NULL,
    smtp_host TEXT NOT NULL,
    smtp_port INTEGER NOT NULL DEFAULT 587,
    imap_host TEXT NOT NULL,
    imap_port INTEGER NOT NULL DEFAULT 993,
    username TEXT NOT NULL,
    password_encrypted TEXT NOT NULL,
    use_tls BOOLEAN NOT NULL DEFAULT true,
    is_active BOOLEAN NOT NULL DEFAULT true,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX idx_tenant_email_accounts_tenant ON tenant_email_accounts (tenant_id) WHERE is_active;
