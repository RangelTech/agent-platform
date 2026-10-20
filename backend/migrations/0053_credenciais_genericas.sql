-- 0053: credenciais genéricas. O segredo ganha tipo (segredo comum, SSH, service account GCP),
-- metadados não sensíveis (host/porta/usuário do SSH) e o final do valor para exibição; o OAuth
-- por servidor guarda o estado pendente para fechar a conexão sem depender do navegador de origem.

ALTER TABLE secrets
    ADD COLUMN IF NOT EXISTS kind TEXT NOT NULL DEFAULT 'secret'
        CHECK (kind IN ('secret', 'ssh', 'gcp')),
    ADD COLUMN IF NOT EXISTS meta JSONB NOT NULL DEFAULT '{}'::jsonb,
    ADD COLUMN IF NOT EXISTS hint TEXT;

CREATE TABLE IF NOT EXISTS oauth_pending (
    state TEXT PRIMARY KEY,
    tenant_id UUID NOT NULL REFERENCES tenants (id) ON DELETE CASCADE,
    provider TEXT NOT NULL CHECK (provider IN ('google-workspace', 'microsoft-graph')),
    label TEXT,
    redirect_uri TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- A lista de tipos de fonte de dados do código (mongodb, sqlserver, oracle, firebird) nunca
-- coube na restrição original da 0007; o cadastro de bancos pela tela de credenciais precisa dela.
ALTER TABLE datasources DROP CONSTRAINT IF EXISTS datasources_kind_check;
ALTER TABLE datasources ADD CONSTRAINT datasources_kind_check CHECK (
    kind IN ('postgresql', 'mysql', 'bigquery', 'sqlite', 'sqlserver', 'oracle', 'firebird',
             'mongodb'));
