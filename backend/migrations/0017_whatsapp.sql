-- Fase F — canal WhatsApp via W-API.
--
-- A credencial aqui é de SAÍDA (chamar a W-API), diferente da api_key de
-- `integrations`, que autentica quem ENTRA. Por isso tabela própria em vez de
-- colunas novas em `integrations`.

CREATE TABLE IF NOT EXISTS whatsapp_connections (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id UUID NOT NULL REFERENCES tenants (id) ON DELETE CASCADE,
    integration_id UUID NOT NULL REFERENCES integrations (id) ON DELETE CASCADE,
    instance_id TEXT NOT NULL,
    token_encrypted TEXT NOT NULL,
    -- Base da W-API; permite apontar para sandbox sem mexer em código.
    api_base TEXT NOT NULL DEFAULT 'https://api.w-api.app/v1',
    is_active BOOLEAN NOT NULL DEFAULT TRUE,
    last_test_at TIMESTAMPTZ,
    last_test_ok BOOLEAN,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (integration_id)
);

CREATE INDEX IF NOT EXISTS whatsapp_connections_tenant_idx
    ON whatsapp_connections (tenant_id);

-- Toda requisição de webhook é gravada crua ANTES de qualquer parsing: se o
-- formato da W-API mudar, o evento não se perde junto com o parser.
CREATE TABLE IF NOT EXISTS whatsapp_webhook_events (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    integration_id UUID REFERENCES integrations (id) ON DELETE CASCADE,
    raw_body TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'received',
    detail TEXT NOT NULL DEFAULT '',
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS whatsapp_webhook_events_idx
    ON whatsapp_webhook_events (integration_id, created_at DESC);

-- Conversas de canal externo aparecem no Chat como qualquer outra, mas não
-- pertencem a um usuário da plataforma: o dono é o contato do WhatsApp.
ALTER TABLE chats ADD COLUMN IF NOT EXISTS channel TEXT NOT NULL DEFAULT 'web';
ALTER TABLE chats ADD COLUMN IF NOT EXISTS external_contact TEXT;
ALTER TABLE chats ALTER COLUMN user_id DROP NOT NULL;

CREATE INDEX IF NOT EXISTS chats_channel_idx
    ON chats (tenant_id, channel, external_contact);
