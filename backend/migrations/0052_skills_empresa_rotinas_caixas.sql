-- 0052: skills da empresa (compartilhadas pelo tenant), historico de execucoes das rotinas
-- e vinculo template x caixa de entrada guardado no motor (sem depender da ponte).

CREATE TABLE IF NOT EXISTS tenant_skills (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id UUID NOT NULL REFERENCES tenants (id) ON DELETE CASCADE,
    name TEXT NOT NULL,
    description TEXT NOT NULL DEFAULT '',
    content TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('active', 'disabled')),
    revision INTEGER NOT NULL DEFAULT 1,
    created_by UUID REFERENCES users (id) ON DELETE SET NULL,
    updated_by UUID REFERENCES users (id) ON DELETE SET NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (tenant_id, name)
);

CREATE TABLE IF NOT EXISTS scheduled_job_runs (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    job_id UUID NOT NULL REFERENCES scheduled_jobs (id) ON DELETE CASCADE,
    tenant_id UUID NOT NULL,
    started_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    finished_at TIMESTAMPTZ,
    status TEXT NOT NULL DEFAULT 'running' CHECK (status IN ('running', 'ok', 'error')),
    chat_id UUID
);
CREATE INDEX IF NOT EXISTS scheduled_job_runs_job_idx ON scheduled_job_runs (job_id, started_at DESC);

CREATE TABLE IF NOT EXISTS inbox_ai_links (
    tenant_id UUID NOT NULL REFERENCES tenants (id) ON DELETE CASCADE,
    chatwoot_inbox_id BIGINT NOT NULL,
    template_id UUID REFERENCES templates (id) ON DELETE SET NULL,
    autopilot BOOLEAN NOT NULL DEFAULT TRUE,
    handoff_team_id BIGINT,
    bridge_synced BOOLEAN NOT NULL DEFAULT FALSE,
    bridge_error TEXT,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (tenant_id, chatwoot_inbox_id)
);
