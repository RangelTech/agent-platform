-- 0012: integrations — machine-to-machine access. One row per consuming
-- system: channel + default template + hashed API key + optional webhook.

CREATE TABLE integrations (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id UUID NOT NULL REFERENCES tenants (id) ON DELETE CASCADE,
    name TEXT NOT NULL,
    -- 'api' now; 'whatsapp' arrives with its ticket.
    channel TEXT NOT NULL DEFAULT 'api' CHECK (channel IN ('api', 'whatsapp')),
    template_id UUID REFERENCES templates (id) ON DELETE SET NULL,
    -- SHA-256 of the key; the plaintext is shown exactly once at creation.
    api_key_hash TEXT NOT NULL UNIQUE,
    -- First characters kept for display ("ap_1a2b3c…").
    key_prefix TEXT NOT NULL,
    webhook_url TEXT,
    webhook_secret_encrypted TEXT,
    rate_limit_per_minute INTEGER NOT NULL DEFAULT 60,
    is_active BOOLEAN NOT NULL DEFAULT TRUE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    revoked_at TIMESTAMPTZ,
    UNIQUE (tenant_id, name)
);

CREATE TABLE integration_messages (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    integration_id UUID NOT NULL REFERENCES integrations (id) ON DELETE CASCADE,
    external_session_id TEXT NOT NULL DEFAULT '',
    direction TEXT NOT NULL CHECK (direction IN ('in', 'out')),
    content TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX integration_messages_idx ON integration_messages (integration_id, created_at);
