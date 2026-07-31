-- Fase E — MCP Store: catálogo curado pelo admin da plataforma + ativação
-- por tenant.
--
-- Duas tabelas, não uma: o catálogo é a *definição* (quem cura é o master) e a
-- ativação é a *instância* daquele item num tenant, com credenciais próprias.
--
-- Nota de arquitetura: as versões de template são imutáveis, então ativar um
-- item NÃO reescreve `template_mcp_servers` de uma versão já publicada. A
-- ativação é resolvida em tempo de execução por `template_runtime`, do mesmo
-- jeito que os secrets do tenant — o runtime (`ExternalServers`) continua
-- intacto.

CREATE TABLE IF NOT EXISTS mcp_catalog_items (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    -- Vira o prefixo público das tools: ext_<slug>_<tool>.
    slug TEXT NOT NULL UNIQUE CHECK (slug ~ '^[a-z0-9_]{1,60}$'),
    name TEXT NOT NULL,
    description TEXT NOT NULL DEFAULT '',
    category TEXT NOT NULL DEFAULT 'geral',
    icon TEXT NOT NULL DEFAULT '',
    -- URL do servidor MCP (streamable HTTP). Pode conter {{credential:CHAVE}}.
    server_url TEXT NOT NULL DEFAULT '',
    -- Cabeçalho de autorização, também com {{credential:CHAVE}}.
    auth_token_template TEXT NOT NULL DEFAULT '',
    -- [{"key": "api_token", "label": "Token", "secret": true}]
    required_credentials JSONB NOT NULL DEFAULT '[]'::jsonb,
    -- Item nativo = já implementado dentro da plataforma (ex.: PIX da Fase D).
    -- Aparece no catálogo para o tenant saber que existe, mas não gera
    -- servidor MCP externo nenhum.
    is_native BOOLEAN NOT NULL DEFAULT FALSE,
    native_key TEXT NOT NULL DEFAULT '',
    is_active BOOLEAN NOT NULL DEFAULT TRUE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS tenant_mcp_activations (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id UUID NOT NULL REFERENCES tenants (id) ON DELETE CASCADE,
    item_id UUID NOT NULL REFERENCES mcp_catalog_items (id) ON DELETE CASCADE,
    -- JSON com as credenciais do tenant, criptografado (Fernet).
    credentials_encrypted TEXT,
    -- Vazio = vale para todos os templates do tenant.
    template_ids UUID[] NOT NULL DEFAULT '{}',
    is_active BOOLEAN NOT NULL DEFAULT TRUE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (tenant_id, item_id)
);

CREATE INDEX IF NOT EXISTS tenant_mcp_activations_tenant_idx
    ON tenant_mcp_activations (tenant_id) WHERE is_active;

-- Item nativo inicial: a cobrança PIX da Fase D. Fica no catálogo para o
-- tenant descobrir a capacidade; a credencial continua na tabela dedicada
-- `payment_credentials`, que tem modelagem mais rica que um servidor MCP.
INSERT INTO mcp_catalog_items (slug, name, description, category, icon, is_native, native_key)
VALUES (
    'pagamentos_pix',
    'Pagamentos PIX (Mercado Pago)',
    'Gera cobranças PIX e confirma pagamentos dentro da conversa. Configure a credencial em Pagamentos.',
    'pagamentos',
    '$',
    TRUE,
    'payments'
)
ON CONFLICT (slug) DO NOTHING;

-- Novo recurso de permissão para quem já administra templates.
UPDATE user_profiles
   SET permissions = permissions || '{"mcp_store": ["view", "create", "edit", "delete"]}'::jsonb
 WHERE NOT (permissions ? 'mcp_store')
   AND permissions -> 'templates' @> '["view", "create", "edit", "delete"]'::jsonb;
