-- 0008: business files and their RAG chunks.

CREATE TABLE files (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id UUID NOT NULL REFERENCES tenants (id) ON DELETE CASCADE,
    name TEXT NOT NULL,
    content_type TEXT NOT NULL,
    size_bytes BIGINT NOT NULL,
    storage_path TEXT NOT NULL,
    -- Ingestion lifecycle: pending -> processing -> ready | error
    status TEXT NOT NULL DEFAULT 'pending'
        CHECK (status IN ('pending', 'processing', 'ready', 'error')),
    error_detail TEXT,
    chunk_count INTEGER,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX files_tenant_idx ON files (tenant_id);

-- 768 dims fits both Gemini text-embedding-004 (native) and OpenAI
-- text-embedding-3-small (via the dimensions parameter).
CREATE TABLE file_chunks (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    file_id UUID NOT NULL REFERENCES files (id) ON DELETE CASCADE,
    tenant_id UUID NOT NULL,
    chunk_index INTEGER NOT NULL,
    content TEXT NOT NULL,
    embedding vector(768),
    UNIQUE (file_id, chunk_index)
);
CREATE INDEX file_chunks_tenant_idx ON file_chunks (tenant_id);

-- Files each specialist may search (RAG scope).
CREATE TABLE template_agent_files (
    agent_id UUID NOT NULL REFERENCES template_agents (id) ON DELETE CASCADE,
    file_id UUID NOT NULL REFERENCES files (id) ON DELETE CASCADE,
    PRIMARY KEY (agent_id, file_id)
);
