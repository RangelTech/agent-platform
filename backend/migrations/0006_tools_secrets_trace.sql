-- 0006: platform tools support — named secrets, per-agent tool selection,
-- external MCP servers per version, and the tool-call trace.

CREATE TABLE secrets (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id UUID NOT NULL REFERENCES tenants (id) ON DELETE CASCADE,
    -- Referenced from tool inputs as {{secret:NAME}}.
    name TEXT NOT NULL,
    value_encrypted TEXT NOT NULL,
    is_active BOOLEAN NOT NULL DEFAULT TRUE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (tenant_id, name)
);

-- Which platform/external tools each specialist may call.
ALTER TABLE template_agents ADD COLUMN tools JSONB NOT NULL DEFAULT '[]'::jsonb;

CREATE TABLE template_mcp_servers (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    version_id UUID NOT NULL REFERENCES template_versions (id) ON DELETE CASCADE,
    -- Tool names are exposed to agents as ext_<name>_<tool>.
    name TEXT NOT NULL,
    url TEXT NOT NULL,
    auth_token_encrypted TEXT,
    UNIQUE (version_id, name)
);

-- Every tool call an agent makes, queryable per conversation.
CREATE TABLE tool_calls (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id UUID,
    -- Thread id as text: kernel threads are opaque strings, not always UUIDs.
    chat_id TEXT,
    agent_name TEXT NOT NULL,
    tool_name TEXT NOT NULL,
    input JSONB,
    output TEXT,
    status TEXT NOT NULL,
    duration_ms INTEGER,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX tool_calls_chat_idx ON tool_calls (chat_id, created_at);
