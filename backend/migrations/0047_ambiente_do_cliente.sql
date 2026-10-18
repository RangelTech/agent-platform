-- 0047: ambiente (workspace) da empresa -- espelho do estado do gerenciador
-- de execucao no Postgres -- e uso por hora; permissao `environment`.

CREATE TABLE IF NOT EXISTS tenant_environment (
    tenant_id UUID PRIMARY KEY REFERENCES tenants (id) ON DELETE CASCADE,
    enabled BOOLEAN NOT NULL DEFAULT false,
    state TEXT NOT NULL DEFAULT 'stopped'
        CHECK (state IN ('disabled', 'stopped', 'starting', 'running', 'degraded', 'suspended')),
    plan TEXT,
    usage JSONB NOT NULL DEFAULT '{}'::jsonb,
    last_backup_at TIMESTAMPTZ,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS tenant_environment_event (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id UUID NOT NULL REFERENCES tenants (id) ON DELETE CASCADE,
    kind TEXT NOT NULL,
    detail JSONB NOT NULL DEFAULT '{}'::jsonb,
    actor_user_id UUID,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS tenant_environment_event_idx
    ON tenant_environment_event (tenant_id, created_at);

CREATE TABLE IF NOT EXISTS tenant_exposures (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id UUID NOT NULL REFERENCES tenants (id) ON DELETE CASCADE,
    name TEXT NOT NULL,
    port INTEGER NOT NULL,
    url TEXT,
    status TEXT NOT NULL DEFAULT 'online' CHECK (status IN ('online', 'offline', 'waking')),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (tenant_id, name)
);

CREATE TABLE IF NOT EXISTS tenant_domains (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id UUID NOT NULL REFERENCES tenants (id) ON DELETE CASCADE,
    exposure_id UUID REFERENCES tenant_exposures (id) ON DELETE SET NULL,
    host TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending_verification'
        CHECK (status IN ('pending_verification', 'active', 'removed')),
    txt_name TEXT NOT NULL,
    txt_value TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    verified_at TIMESTAMPTZ
);
CREATE UNIQUE INDEX IF NOT EXISTS tenant_domains_host_live
    ON tenant_domains (lower(host)) WHERE status <> 'removed';

CREATE TABLE IF NOT EXISTS worker_pool_state (
    worker_id TEXT PRIMARY KEY,
    state TEXT NOT NULL,
    data JSONB NOT NULL DEFAULT '{}'::jsonb,
    last_seen_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS tenant_usage_hourly (
    tenant_id UUID NOT NULL REFERENCES tenants (id) ON DELETE CASCADE,
    hour TIMESTAMPTZ NOT NULL,
    runs INTEGER NOT NULL DEFAULT 0,
    input_tokens BIGINT NOT NULL DEFAULT 0,
    output_tokens BIGINT NOT NULL DEFAULT 0,
    cpu_seconds DOUBLE PRECISION NOT NULL DEFAULT 0,
    egress_bytes BIGINT NOT NULL DEFAULT 0,
    PRIMARY KEY (tenant_id, hour)
);

-- Permissao `environment` (view/manage): backfill nos perfis Administrador.
UPDATE user_profiles
   SET permissions = permissions || '{"environment": ["view", "manage"]}'::jsonb
 WHERE NOT (permissions ? 'environment')
   AND permissions -> 'templates' @> '["view", "create", "edit", "delete"]'::jsonb;
