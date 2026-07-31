-- Fase D — cobrança PIX via Mercado Pago.
--
-- Credencial fica em tabela própria (e não em `datasources`) porque a
-- semântica é outra: não é fonte de dados consultável, é credencial de
-- gateway. O token nunca é gravado em claro — mesmo padrão Fernet já usado
-- em datasources.secret_encrypted e ai_services.api_key_encrypted.

CREATE TABLE IF NOT EXISTS payment_credentials (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    provider TEXT NOT NULL DEFAULT 'mercado_pago',
    access_token_encrypted TEXT,
    -- Sandbox troca apenas a credencial usada; nunca o valor cobrado.
    sandbox BOOLEAN NOT NULL DEFAULT TRUE,
    -- Segredo de assinatura do webhook (Mercado Pago -> x-signature).
    webhook_secret_encrypted TEXT,
    -- Sufixo público e opaco da URL de webhook deste tenant.
    webhook_token TEXT NOT NULL DEFAULT encode(gen_random_bytes(18), 'hex'),
    is_active BOOLEAN NOT NULL DEFAULT TRUE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (tenant_id, provider)
);

CREATE UNIQUE INDEX IF NOT EXISTS payment_credentials_webhook_token_idx
    ON payment_credentials (webhook_token);

-- Uma linha por cobrança gerada. É o vínculo payment_id <-> pedido que
-- permite o webhook confirmar o pagamento e o agente consultar depois.
CREATE TABLE IF NOT EXISTS payment_charges (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    provider TEXT NOT NULL DEFAULT 'mercado_pago',
    external_id TEXT NOT NULL,
    chat_id UUID,
    amount NUMERIC(12, 2) NOT NULL,
    description TEXT NOT NULL DEFAULT '',
    -- Referência livre do negócio (ex.: id do pedido na Hamburgueria).
    reference_id TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'pending',
    qr_code TEXT,
    qr_code_base64 TEXT,
    ticket_url TEXT,
    sandbox BOOLEAN NOT NULL DEFAULT TRUE,
    raw JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (provider, external_id)
);

CREATE INDEX IF NOT EXISTS payment_charges_tenant_idx
    ON payment_charges (tenant_id, created_at DESC);
CREATE INDEX IF NOT EXISTS payment_charges_reference_idx
    ON payment_charges (tenant_id, reference_id);

-- Perfis já existentes guardam um snapshot das permissões, então um recurso
-- novo não chega sozinho nos tenants antigos. Quem já administrava templates
-- por completo passa a administrar pagamentos também.
UPDATE user_profiles
   SET permissions = permissions || '{"payments": ["view", "create", "edit", "delete"]}'::jsonb
 WHERE NOT (permissions ? 'payments')
   AND permissions -> 'templates' @> '["view", "create", "edit", "delete"]'::jsonb;
