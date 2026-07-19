-- 0005: templates — the product core. A template is a supervisor plus N
-- specialist agents; versions are immutable snapshots; deploying marks the
-- active version on the template (rollback = point back to an older one).

CREATE TABLE templates (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id UUID NOT NULL REFERENCES tenants (id) ON DELETE CASCADE,
    name TEXT NOT NULL,
    -- Shown to users in the chat's template picker.
    description TEXT NOT NULL DEFAULT '',
    active_version_id UUID,
    is_deleted BOOLEAN NOT NULL DEFAULT FALSE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE UNIQUE INDEX templates_tenant_name_key
    ON templates (tenant_id, name) WHERE NOT is_deleted;

CREATE TABLE template_versions (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    template_id UUID NOT NULL REFERENCES templates (id) ON DELETE CASCADE,
    version_number INTEGER NOT NULL,
    -- Supervisor: talks to the user and decides which specialist to call.
    supervisor_prompt TEXT NOT NULL,
    supervisor_ai_service_id UUID REFERENCES ai_services (id) ON DELETE SET NULL,
    supervisor_model_override TEXT,
    supervisor_reasoning_effort TEXT,
    -- Hard execution limits (anti-loop).
    max_steps INTEGER NOT NULL DEFAULT 6,
    created_by UUID REFERENCES users (id) ON DELETE SET NULL,
    notes TEXT NOT NULL DEFAULT '',
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (template_id, version_number)
);

ALTER TABLE templates
    ADD CONSTRAINT templates_active_version_fk
    FOREIGN KEY (active_version_id) REFERENCES template_versions (id)
    ON DELETE SET NULL;

CREATE TABLE template_agents (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    version_id UUID NOT NULL REFERENCES template_versions (id) ON DELETE CASCADE,
    -- snake_case identifier; the supervisor sees it as a callable tool.
    name TEXT NOT NULL,
    -- Tells the supervisor when this specialist should be called.
    description TEXT NOT NULL,
    prompt TEXT NOT NULL,
    ai_service_id UUID REFERENCES ai_services (id) ON DELETE SET NULL,
    model_override TEXT,
    reasoning_effort TEXT,
    sort_order INTEGER NOT NULL DEFAULT 0,
    UNIQUE (version_id, name)
);

-- A conversation is pinned to a template; the user can switch it mid-chat.
ALTER TABLE chats ADD COLUMN template_id UUID REFERENCES templates (id) ON DELETE SET NULL;
