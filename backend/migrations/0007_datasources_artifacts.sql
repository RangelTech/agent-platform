-- 0007: data plane — tenant datasources, their link to template versions,
-- and the artifact registry (metadata here, payload in object storage).

CREATE TABLE datasources (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id UUID NOT NULL REFERENCES tenants (id) ON DELETE CASCADE,
    -- Alias the agents use to refer to this connection.
    name TEXT NOT NULL,
    kind TEXT NOT NULL CHECK (kind IN ('postgresql', 'mysql', 'bigquery', 'sqlite')),
    -- Non-sensitive connection settings (host, port, database, project, ...).
    config JSONB NOT NULL DEFAULT '{}'::jsonb,
    -- Sensitive part (password, service-account JSON), Fernet-encrypted.
    secret_encrypted TEXT,
    is_active BOOLEAN NOT NULL DEFAULT TRUE,
    last_test_at TIMESTAMPTZ,
    last_test_ok BOOLEAN,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (tenant_id, name)
);

CREATE TABLE template_version_datasources (
    version_id UUID NOT NULL REFERENCES template_versions (id) ON DELETE CASCADE,
    datasource_id UUID NOT NULL REFERENCES datasources (id) ON DELETE CASCADE,
    PRIMARY KEY (version_id, datasource_id)
);

CREATE TABLE artifacts (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id UUID,
    chat_id TEXT,
    agent_name TEXT NOT NULL,
    -- dataset | chart | document | file
    kind TEXT NOT NULL,
    title TEXT NOT NULL DEFAULT '',
    -- Column names/types for datasets; shape metadata for other kinds.
    schema_json JSONB,
    -- First rows / summary shown to the model instead of the full payload.
    preview_json JSONB,
    row_count INTEGER,
    -- Where the full payload lives (gs://... or file path in dev).
    storage_path TEXT NOT NULL,
    content_type TEXT NOT NULL DEFAULT 'application/json',
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX artifacts_chat_idx ON artifacts (chat_id, created_at);
