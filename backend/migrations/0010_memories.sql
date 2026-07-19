-- 0010: long-term memory — facts extracted after conversations, namespaced
-- per tenant+user, retrieved semantically at the start of each turn.

CREATE TABLE memories (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id UUID NOT NULL,
    user_id UUID NOT NULL,
    content TEXT NOT NULL,
    embedding vector(768),
    source_chat TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX memories_scope_idx ON memories (tenant_id, user_id);
