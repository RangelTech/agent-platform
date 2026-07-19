-- 0004: per-tenant AI services (BYOK). API keys are encrypted at the
-- application layer (Fernet) before touching this table.

CREATE TABLE ai_services (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id UUID NOT NULL REFERENCES tenants (id) ON DELETE CASCADE,
    name TEXT NOT NULL,
    -- litellm provider prefix: gemini, openai, anthropic, deepseek, groq, ...
    -- "openai-compatible" uses api_base with the openai protocol.
    provider TEXT NOT NULL,
    model TEXT NOT NULL,
    -- api_key | oauth. OAuth arrives with the LoginWithChatGPT ticket; the
    -- column exists from day one so that lands without a destructive change.
    auth_type TEXT NOT NULL DEFAULT 'api_key' CHECK (auth_type IN ('api_key', 'oauth')),
    api_key_encrypted TEXT,
    api_base TEXT,
    config JSONB NOT NULL DEFAULT '{}'::jsonb,
    is_active BOOLEAN NOT NULL DEFAULT TRUE,
    last_test_at TIMESTAMPTZ,
    last_test_ok BOOLEAN,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX ai_services_tenant_idx ON ai_services (tenant_id);
CREATE UNIQUE INDEX ai_services_tenant_name_key ON ai_services (tenant_id, name);
